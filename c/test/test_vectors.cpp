// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Oshi Lab.
// Checks the C++ reference codec against vectors/omp-v1.json (through the generated vectors.h).
// Build and run: c++ -std=c++17 -Wall -Wextra -Werror -I.. ../oshi_omp.cpp test_vectors.cpp -o t && ./t

#include "oshi_omp.h"
#include "vectors.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

using namespace oshi;

static int failures = 0;
#define CHECK(cond)                                                                                                    \
    do {                                                                                                               \
        if (!(cond)) {                                                                                                 \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);                                                \
            failures++;                                                                                                \
        }                                                                                                              \
    } while (0)

static std::vector<uint8_t> hex(const char *s)
{
    std::vector<uint8_t> out;
    for (size_t i = 0; s[i] && s[i + 1]; i += 2) {
        unsigned v;
        std::sscanf(s + i, "%2x", &v);
        out.push_back(uint8_t(v));
    }
    return out;
}

static bool same(const uint8_t *buf, size_t len, const char *expected)
{
    std::vector<uint8_t> e = hex(expected);
    return len == e.size() && std::memcmp(buf, e.data(), len) == 0;
}

static const uint32_t MSG = 0xDEADBEEF, ORIGIN = 0x12345678, DEST = 0x9ABCDEF0;
static const uint8_t HELLO[] = {0x01, 'h', 'e', 'l', 'l', 'o'};

static void testData()
{
    uint8_t buf[OMP_MAX_FRAME];
    DataFrame f;
    f.msgId = MSG;
    f.origin = ORIGIN;
    f.dest = DEST;
    f.count = 1;
    f.flags = FLAG_CUSTODY_OK;
    f.data = HELLO;
    f.len = sizeof(HELLO);
    CHECK(same(buf, encodeData(f, buf, sizeof(buf)), VEC_V1_DATA_CUSTODY_OK));
    f.origin = 0;
    CHECK(same(buf, encodeData(f, buf, sizeof(buf)), VEC_V2_DATA_FROM_PHONE));
    f.origin = ORIGIN;
    f.dest = OMP_DEST_BROADCAST;
    f.flags = 0;
    CHECK(same(buf, encodeData(f, buf, sizeof(buf)), VEC_V3_DATA_BROADCAST));
    f.dest = OMP_DEST_INTERNET;
    f.data = nullptr;
    f.len = 0;
    CHECK(same(buf, encodeData(f, buf, sizeof(buf)), VEC_V3_INTERNET_HEADER));

    std::vector<uint8_t> v1 = hex(VEC_V1_DATA_CUSTODY_OK);
    DataFrame d;
    CHECK(decodeData(v1.data(), v1.size(), d));
    CHECK(d.msgId == MSG && d.origin == ORIGIN && d.dest == DEST && d.idx == 0 && d.count == 1);
    CHECK(d.flags == FLAG_CUSTODY_OK && d.len == sizeof(HELLO) && std::memcmp(d.data, HELLO, d.len) == 0);

    // A wrong magic, a wrong version, idx >= count or count 0 must not decode.
    std::vector<uint8_t> bad = v1;
    bad[0] = 'X';
    CHECK(!decodeData(bad.data(), bad.size(), d));
    bad = v1;
    bad[2] = 0x21;
    CHECK(!decodeData(bad.data(), bad.size(), d));
    bad = v1;
    bad[15] = 1;
    CHECK(!decodeData(bad.data(), bad.size(), d));
    bad = v1;
    bad[16] = 0;
    CHECK(!decodeData(bad.data(), bad.size(), d));
}

static void testFragments()
{
    const char *vec[] = {VEC_FRAG_0, VEC_FRAG_1, VEC_FRAG_2};
    for (int i = 0; i < 3; i++) {
        std::vector<uint8_t> v = hex(vec[i]);
        DataFrame d;
        CHECK(decodeData(v.data(), v.size(), d));
        CHECK(d.idx == i && d.count == 3 && d.len == (i < 2 ? OMP_MAX_FRAG_DATA : 36));
        for (size_t k = 0; k < d.len; k++)
            CHECK(d.data[k] == uint8_t((i * OMP_MAX_FRAG_DATA + k) & 0xFF));
        uint8_t buf[OMP_MAX_FRAME];
        CHECK(same(buf, encodeData(d, buf, sizeof(buf)), vec[i]));
    }
}

static void testControl()
{
    uint8_t buf[OMP_MAX_FRAME];
    SackFrame s;
    s.msgId = MSG;
    s.origin = ORIGIN;
    s.count = 12;
    s.bitmap = 0x0DFB;
    CHECK(same(buf, encodeSack(s, buf, sizeof(buf)), VEC_V5_SACK_MISSING_2_9));
    s.bitmap = fullBitmap(12);
    CHECK(same(buf, encodeSack(s, buf, sizeof(buf)), VEC_V5_SACK_COMPLETE));
    SackFrame sd;
    std::vector<uint8_t> v5 = hex(VEC_V5_SACK_MISSING_2_9);
    CHECK(decodeSack(v5.data(), v5.size(), sd) && sd.count == 12 && sd.bitmap == 0x0DFB);

    NoticeFrame n{MSG, ORIGIN, DEST};
    CHECK(same(buf, encodeNotice(FrameType::CUSTODY, n, buf, sizeof(buf)), VEC_V6_CUSTODY));
    CHECK(same(buf, encodeNotice(FrameType::RECEIPT, n, buf, sizeof(buf)), VEC_V6_RECEIPT));
    NoticeFrame nd;
    std::vector<uint8_t> v6 = hex(VEC_V6_RECEIPT);
    CHECK(decodeNotice(v6.data(), v6.size(), FrameType::RECEIPT, nd) && nd.dest == DEST);
    CHECK(!decodeNotice(v6.data(), v6.size(), FrameType::CUSTODY, nd));

    BeaconFrame b;
    b.caps = CAP_CUSTODIAN | CAP_GATEWAY_ONLINE;
    b.version = 0x0100;
    b.custodyFreeKb = 32;
    CHECK(same(buf, encodeBeacon(b, buf, sizeof(buf)), VEC_V7_BEACON));
    CHECK(same(buf, encodeBeacon(BeaconFrame{}, buf, sizeof(buf)), VEC_V7_PROBE));

    StatusFrame st;
    st.msgId = MSG;
    st.state = MsgState::DELIVERED;
    st.node = DEST;
    CHECK(same(buf, encodeStatus(st, buf, sizeof(buf)), VEC_V8_STATUS_DELIVERED));

    PullFrame p;
    p.nodeNum = ORIGIN;
    p.afterSeq = 41;
    p.tsSec = 1790000000;
    std::memset(p.sig, 0xAA, sizeof(p.sig));
    CHECK(same(buf, encodePull(p, buf, sizeof(buf)), VEC_V9_PULL));
    // The frame-specific tail of the signed bytes: gateway, afterSeq (the rest is the signer's envelope).
    uint8_t tail[8];
    pullSigningPayload(0x0A0B0C0D, 41, tail);
    std::vector<uint8_t> signedBytes = hex(VEC_PULL_SIGNED);
    CHECK(std::memcmp(tail, signedBytes.data() + 12, 8) == 0);
}

int main()
{
    testData();
    testFragments();
    testControl();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("all C++ vector checks passed\n");
    return 0;
}
