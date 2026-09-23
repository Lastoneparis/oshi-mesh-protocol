# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oshi Lab.
# oshi_omp/omp.py
"""OSHI Mesh Protocol (OMP) v1 frame codec, byte-for-byte compatible with
oshi-mesh-firmware src/oshi/OshiProtocol.cpp (branch ``oshi``).

Every frame is the payload of a Meshtastic PRIVATE_APP (256) packet and starts
with ``"OS"`` followed by ``(version << 4) | type``. All integers are
little-endian.

    DATA    : prefix(3) msgId u32 | origin u32 | dest u32 | idx u8 | count u8 | flags u8 | data (<=182)
    SACK    : prefix(3) msgId u32 | origin u32 | count u8 | bitmap u64
    CUSTODY / RECEIPT : prefix(3) msgId u32 | origin u32 | dest u32
    BEACON  : prefix(3) caps u8 | version u16 | custodyFreeKb u8
    PULL    : prefix(3) nodeNum u32 | afterSeq u32 | tsSec u32 | sig[64]
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional, Union

MAGIC = b"OS"
VERSION = 1
PORTNUM_PRIVATE_APP = 256

MAX_FRAME = 200
DATA_HEADER = 18
MAX_FRAG_DATA = MAX_FRAME - DATA_HEADER
MAX_FRAGS = 64

DEST_BROADCAST = 0xFFFFFFFF
DEST_INTERNET = 0xFFFFFFF0

FLAG_CUSTODY_OK = 1 << 0
FLAG_VIA_CUSTODY = 1 << 1
FLAG_CUSTODY_REQ = 1 << 2

# BEACON caps (BeaconCaps in OshiProtocol.h)
CAP_CUSTODIAN = 1 << 0
CAP_GATEWAY_ONLINE = 1 << 1
CAP_BRIDGE = 1 << 2  # the firmware accepts a RECEIPT from a node whose fresh beacon carries this

_PREFIX = 3
_SACK_LEN = _PREFIX + 4 + 4 + 1 + 8
_NOTICE_LEN = _PREFIX + 4 + 4 + 4
_BEACON_LEN = _PREFIX + 1 + 2 + 1
_STATUS_LEN = _PREFIX + 4 + 1 + 4
PULL_SIG_LEN = 64
_PULL_LEN = _PREFIX + 4 + 4 + 4 + PULL_SIG_LEN
PULL_SIGN_PORT = 0x4F505531

# STATUS states (MsgState in OshiProtocol.h), radio -> its own phone
QUEUED, SENT, DELIVERED, IN_CUSTODY, FAILED, UPLINKED, REJECTED = range(1, 8)

# Message body tags (spec section 5): raw, or raw DEFLATE (no zlib header, no checksum)
BODY_RAW = 0x01
BODY_DEFLATE = 0x02


class FrameType(IntEnum):
    DATA = 1
    SACK = 2
    CUSTODY = 3
    RECEIPT = 4
    BEACON = 5
    STATUS = 6
    PULL = 7


def full_bitmap(count: int) -> int:
    return (1 << 64) - 1 if count >= 64 else (1 << count) - 1


def is_omp(buf: bytes) -> bool:
    return (
        buf is not None
        and len(buf) >= _PREFIX
        and buf[0:2] == MAGIC
        and (buf[2] >> 4) == VERSION
    )


def frame_type(buf: bytes) -> Optional[FrameType]:
    if not is_omp(buf):
        return None
    t = buf[2] & 0x0F
    try:
        return FrameType(t)
    except ValueError:
        return None


def _prefix(t: FrameType) -> bytes:
    return MAGIC + bytes([(VERSION << 4) | int(t)])


@dataclass(frozen=True)
class DataFrame:
    msg_id: int
    origin: int
    dest: int
    idx: int
    count: int
    flags: int
    data: bytes

    def encode(self) -> bytes:
        if not (0 < self.count <= MAX_FRAGS) or self.idx >= self.count:
            raise ValueError("bad idx/count")
        if len(self.data) > MAX_FRAG_DATA:
            raise ValueError("fragment too long")
        return (
            _prefix(FrameType.DATA)
            + struct.pack("<IIIBBB", self.msg_id, self.origin, self.dest, self.idx, self.count, self.flags)
            + self.data
        )


@dataclass(frozen=True)
class SackFrame:
    msg_id: int
    origin: int
    count: int
    bitmap: int

    def encode(self) -> bytes:
        if not (0 < self.count <= MAX_FRAGS):
            raise ValueError("bad count")
        return _prefix(FrameType.SACK) + struct.pack(
            "<IIBQ", self.msg_id, self.origin, self.count, self.bitmap & full_bitmap(self.count)
        )

    @property
    def complete(self) -> bool:
        return self.bitmap & full_bitmap(self.count) == full_bitmap(self.count)


@dataclass(frozen=True)
class NoticeFrame:
    """CUSTODY or RECEIPT."""

    type: FrameType
    msg_id: int
    origin: int
    dest: int

    def encode(self) -> bytes:
        if self.type not in (FrameType.CUSTODY, FrameType.RECEIPT):
            raise ValueError("not a notice type")
        return _prefix(self.type) + struct.pack("<III", self.msg_id, self.origin, self.dest)


@dataclass(frozen=True)
class BeaconFrame:
    caps: int
    version: int = VERSION << 8  # OMP_IMPL_VERSION: (OMP_VERSION << 8) | 0
    custody_free_kb: int = 0

    def encode(self) -> bytes:
        return _prefix(FrameType.BEACON) + struct.pack("<BHB", self.caps, self.version, self.custody_free_kb)


def decode_beacon(buf: bytes) -> Optional[BeaconFrame]:
    if frame_type(buf) != FrameType.BEACON or len(buf) < _BEACON_LEN:
        return None
    caps, version, free_kb = struct.unpack_from("<BHB", buf, _PREFIX)
    return BeaconFrame(caps, version, free_kb)


@dataclass(frozen=True)
class StatusFrame:
    """Radio -> its own phone: what became of a message the phone handed it."""

    msg_id: int
    state: int
    node: int

    def encode(self) -> bytes:
        return _prefix(FrameType.STATUS) + struct.pack("<IBI", self.msg_id, self.state, self.node)


@dataclass(frozen=True)
class PullFrame:
    node_num: int
    after_seq: int
    ts_sec: int
    sig: bytes

    def encode(self) -> bytes:
        if len(self.sig) != PULL_SIG_LEN:
            raise ValueError("signature must be 64 bytes")
        return _prefix(FrameType.PULL) + struct.pack("<III", self.node_num, self.after_seq, self.ts_sec) + self.sig


def pull_signed_bytes(node_num: int, ts_sec: int, gateway: int, after_seq: int) -> bytes:
    """The 20 bytes a PULL signature covers (spec, vector V12)."""
    return struct.pack("<IIIII", node_num, ts_sec, PULL_SIGN_PORT, gateway, after_seq)


def fragment(msg_id: int, origin: int, dest: int, body: bytes, flags: int = 0) -> List[DataFrame]:
    """Split a message body into DATA frames: every fragment but the last is exactly 182 bytes."""
    count = max(1, -(-len(body) // MAX_FRAG_DATA))
    if count > MAX_FRAGS:
        raise ValueError("message larger than OMP carries")
    return [DataFrame(msg_id, origin, dest, i, count, flags, body[i * MAX_FRAG_DATA:(i + 1) * MAX_FRAG_DATA])
            for i in range(count)]


def encode_body(payload: bytes) -> bytes:
    """0x02 + raw DEFLATE when that is smaller, else 0x01 + payload."""
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    z = c.compress(payload) + c.flush()
    return bytes([BODY_DEFLATE]) + z if len(z) < len(payload) else bytes([BODY_RAW]) + payload


def decode_body(body: bytes) -> Optional[bytes]:
    if not body:
        return None
    if body[0] == BODY_RAW:
        return bytes(body[1:])
    if body[0] == BODY_DEFLATE:
        try:
            return zlib.decompress(bytes(body[1:]), -15)
        except zlib.error:
            return None
    return None


Frame = Union[DataFrame, SackFrame, NoticeFrame, BeaconFrame, StatusFrame, PullFrame]


def decode(buf: bytes) -> Optional[Frame]:
    """Decode any OMP v1 frame. Returns None for anything malformed or not OMP."""
    t = frame_type(buf)
    if t is None:
        return None
    p = buf[_PREFIX:]
    if t == FrameType.DATA:
        if len(buf) < DATA_HEADER or len(buf) > MAX_FRAME:
            return None
        msg_id, origin, dest, idx, count, flags = struct.unpack_from("<IIIBBB", p)
        if not (0 < count <= MAX_FRAGS) or idx >= count:
            return None
        return DataFrame(msg_id, origin, dest, idx, count, flags, bytes(buf[DATA_HEADER:]))
    if t == FrameType.SACK:
        if len(buf) < _SACK_LEN:
            return None
        msg_id, origin, count, bitmap = struct.unpack_from("<IIBQ", p)
        if not (0 < count <= MAX_FRAGS):
            return None
        return SackFrame(msg_id, origin, count, bitmap & full_bitmap(count))
    if t in (FrameType.CUSTODY, FrameType.RECEIPT):
        if len(buf) < _NOTICE_LEN:
            return None
        msg_id, origin, dest = struct.unpack_from("<III", p)
        return NoticeFrame(t, msg_id, origin, dest)
    if t == FrameType.BEACON:
        return decode_beacon(buf)
    if t == FrameType.STATUS:
        if len(buf) < _STATUS_LEN:
            return None
        msg_id, state, node = struct.unpack_from("<IBI", p)
        return StatusFrame(msg_id, state, node)
    if t == FrameType.PULL:
        if len(buf) < _PULL_LEN:
            return None
        node_num, after_seq, ts_sec = struct.unpack_from("<III", p)
        return PullFrame(node_num, after_seq, ts_sec, bytes(buf[_PREFIX + 12:_PULL_LEN]))
    return None
