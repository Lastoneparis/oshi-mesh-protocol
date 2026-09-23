#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oshi Lab.
"""Over-the-air test of the OMP MeshCore binding (spec/TRANSPORTS.md section 2) with two MeshCore companion
radios on USB serial: radio A sends an OSHI message as OMP DATA frames in the OB envelope on #oshi-bridge,
radio B must receive, join, reassemble and get the exact bytes back.

    python3 tools/meshcore_air_test.py /dev/cu.usbserial-A /dev/cu.usbserial-B [--bytes 600]

Needs pyserial. Exit status 0 = the message crossed the air intact.
"""

import argparse
import hashlib
import os
import random
import struct
import sys
import time

import serial

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
from oshi_omp import omp, wire  # noqa: E402

CHANNEL = "#oshi-bridge"
SECRET = hashlib.sha256(CHANNEL.encode()).digest()[:16]
DATA_TYPE = 0xFF4F


class Companion:
    """MeshCore companion protocol over USB serial: app->radio '<' len16 payload, radio->app '>' len16 payload."""

    def __init__(self, port, name):
        self.name = name
        self.s = serial.Serial(port, 115200, timeout=0.2)
        time.sleep(2.0)            # the radio reboots when the port opens on some boards
        self.s.reset_input_buffer()
        self.buf = b""
        self.pushes = []
        self.pub = b""
        self.channel = None

    def send(self, payload):
        self.s.write(b"<" + struct.pack("<H", len(payload)) + payload)

    def frames(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            self.buf += self.s.read(512)
            while True:
                i = self.buf.find(b">")
                if i < 0:
                    self.buf = b""
                    break
                if len(self.buf) < i + 3:
                    self.buf = self.buf[i:]
                    break
                n = struct.unpack_from("<H", self.buf, i + 1)[0]
                if n == 0 or n > 300:
                    self.buf = self.buf[i + 1:]
                    continue
                if len(self.buf) < i + 3 + n:
                    self.buf = self.buf[i:]
                    break
                f = self.buf[i + 3:i + 3 + n]
                self.buf = self.buf[i + 3 + n:]
                yield f
            time.sleep(0.02)

    def request(self, payload, want, timeout=5.0):
        """Send one request and return the first reply whose code is in `want`; pushes are kept aside."""
        self.send(payload)
        for f in self.frames(timeout):
            if f[0] >= 0x80:
                self.pushes.append(f)
                continue
            if f[0] in want:
                return f
        raise TimeoutError(f"{self.name}: no reply {want} to {payload[:3].hex()}")

    def setup(self):
        info = self.request(bytes([1]) + bytes(7) + b"OSHI-test", {5})
        self.pub = info[4:36]
        empty = None
        for idx in range(8):
            try:
                ch = self.request(bytes([31, idx]), {18, 1})
            except TimeoutError:
                break
            if ch[0] != 18:
                break
            name = ch[2:34].split(b"\0")[0].decode(errors="replace")
            if name == CHANNEL and ch[34:50] == SECRET:
                self.channel = idx
                break
            if not name and idx > 0 and empty is None:
                empty = idx
        if self.channel is None:
            if empty is None:
                raise RuntimeError(f"{self.name}: no empty channel slot")
            r = self.request(bytes([32, empty]) + CHANNEL.encode().ljust(32, b"\0") + SECRET, {0, 1})
            if r[0] != 0:
                raise RuntimeError(f"{self.name}: SET_CHANNEL refused ({r.hex()})")
            self.channel = empty
        radio = struct.unpack_from("<IIBB", info, 48) if len(info) >= 58 else None  # freq kHz*1000, bw, sf, cr
        print(f"{self.name}: id {self.pub[:4].hex()}  #oshi-bridge in slot {self.channel}"
              + (f"  radio {radio[0] / 1000:.3f} MHz bw {radio[1] / 1000:.1f} kHz sf{radio[2]} cr{radio[3]}" if radio else ""))

    @property
    def node_id(self):
        return struct.unpack("<I", self.pub[:4])[0]

    def send_datagram(self, dg):
        r = self.request(bytes([62, self.channel, 0xFF]) + struct.pack("<H", DATA_TYPE) + dg, {0, 1})
        if r[0] != 0:
            raise RuntimeError(f"{self.name}: SEND_CHANNEL_DATA refused ({r.hex()})")

    def drain(self, timeout):
        """Pull everything the radio queued (PUSH 0x83 -> SYNC_NEXT until NO_MORE); yield channel datagrams."""
        end = time.time() + timeout
        while time.time() < end:
            self.send(bytes([10]))
            for f in self.frames(3.0):
                if f[0] >= 0x80:
                    continue
                if f[0] == 27:
                    ch, dtype, n = f[4], f[6] | f[7] << 8, f[8]
                    if ch == self.channel and dtype == DATA_TYPE:
                        yield f[9:9 + n]
                    break
                if f[0] == 10:  # no more messages
                    time.sleep(1.0)
                    break
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sender")
    ap.add_argument("receiver")
    ap.add_argument("--bytes", type=int, default=600)
    ap.add_argument("--wait", type=float, default=120)
    a = ap.parse_args()

    tx, rx = Companion(a.sender, "A"), Companion(a.receiver, "B")
    tx.setup()
    rx.setup()
    list(rx.drain(3))  # empty anything queued before the test

    body = omp.encode_body(os.urandom(a.bytes))  # random bytes do not compress: tag 0x01 + payload
    msg_id = random.randint(1, 0xFFFFFFFF)
    frames = omp.fragment(msg_id, tx.node_id, omp.DEST_BROADCAST, body)
    datagrams = []
    for seq, fr in enumerate(frames):
        datagrams += wire.split(fr.encode(), tx.node_id, seq & 0xFF)
    print(f"A: sending {len(body)} B as {len(frames)} OMP frames = {len(datagrams)} MeshCore datagrams")
    t0 = time.time()
    # Read the receiver while sending, as the app does on every MSG_WAITING push: a companion radio queues only 16
    # messages for its app (OFFLINE_QUEUE_SIZE) and drops the oldest channel messages beyond that.
    heard = []
    for dg in datagrams:
        tx.send_datagram(dg)
        heard += list(rx.drain(2.5))

    def incoming():
        yield from heard
        yield from rx.drain(a.wait)

    joiner, got = wire.Joiner(), {}
    for dg in incoming():
        part = wire.parse(dg)
        if part is None or part.sender == rx.node_id:
            continue
        joined = joiner.push(part)
        f = omp.decode(joined) if joined else None
        if isinstance(f, omp.DataFrame) and f.msg_id == msg_id:
            got[f.idx] = f.data
            print(f"B: fragment {f.idx + 1}/{f.count} after {time.time() - t0:.0f} s")
            if len(got) == f.count:
                break
    out = b"".join(got[i] for i in sorted(got)) if len(got) == len(frames) else None
    ok = out == body
    print(("PASS" if ok else "FAIL") + f": {len(got)}/{len(frames)} fragments, bytes identical = {ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
