# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oshi Lab.
"""The Python reference codec against vectors/omp-v1.json."""

import json
import os

import pytest

from oshi_omp import omp, wire

VEC = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "vectors", "omp-v1.json")))


@pytest.mark.parametrize("name", sorted(VEC["frames"]))
def test_every_frame_decodes_and_reencodes(name):
    raw = bytes.fromhex(VEC["frames"][name])
    f = omp.decode(raw)
    if name == "V3_internet_header":  # a header with an empty body is still a valid DATA frame
        assert isinstance(f, omp.DataFrame) and f.dest == omp.DEST_INTERNET
    assert f is not None, name
    assert f.encode() == raw


def test_fragmentation():
    frag = VEC["fragmentation"]
    body = bytes.fromhex(frag["body"])
    frames = omp.fragment(0xDEADBEEF, 0x12345678, 0x9ABCDEF0, body)
    assert [f.encode().hex() for f in frames] == frag["frames"]
    for size, count in frag["counts"].items():
        assert len(omp.fragment(1, 2, 3, bytes(int(size)))) == count
    with pytest.raises(ValueError):
        omp.fragment(1, 2, 3, bytes(omp.MAX_FRAG_DATA * omp.MAX_FRAGS + 1))


def test_bodies():
    for case in VEC["bodies"].values():
        payload, body = bytes.fromhex(case["payload"]), bytes.fromhex(case["body"])
        assert omp.encode_body(payload) == body
        assert omp.decode_body(body) == payload
    assert omp.decode_body(b"\x03abc") is None
    assert omp.decode_body(b"\x02not deflate") is None


def test_pull_signed_bytes():
    assert omp.pull_signed_bytes(0x12345678, 1790000000, 0x0A0B0C0D, 41).hex() == VEC["pull_signed_bytes"]


def test_ob_envelope_split_and_join():
    ob = VEC["ob_envelope"]
    frame = bytes.fromhex(ob["frame"])
    parts = wire.split(frame, ob["sender"], ob["seq"])
    assert [p.hex() for p in parts] == ob["datagrams"]
    joiner = wire.Joiner()
    assert joiner.push(wire.parse(parts[1])) is None
    assert joiner.push(wire.parse(parts[0])) == frame


def test_malformed_frames_are_rejected():
    v1 = bytes.fromhex(VEC["frames"]["V1_data_custody_ok"])
    assert omp.decode(b"XS" + v1[2:]) is None           # magic
    assert omp.decode(v1[:2] + b"\x21" + v1[3:]) is None  # version 2
    assert omp.decode(v1[:15] + b"\x01" + v1[16:]) is None  # idx >= count
    assert omp.decode(v1[:10]) is None                  # truncated
    assert wire.parse(b"OB\x10\x00\x00\x00\x00\x00\x22") is None  # part 2 of 2


def test_constants():
    c = VEC["constants"]
    assert (omp.MAX_FRAME, omp.DATA_HEADER, omp.MAX_FRAG_DATA, omp.MAX_FRAGS) == (
        c["max_frame"], c["data_header"], c["max_frag_data"], c["max_frags"])
