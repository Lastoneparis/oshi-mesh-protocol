# OMP transports and bridging

OMP frames (see [OMP-v1.md](OMP-v1.md)) are transport-independent byte strings of at most 200 bytes. This document
specifies how they ride each LoRa mesh network OSHI uses today, and how a frame crosses from one network to another.

Status: Meshtastic binding implemented and tested over the air. MeshCore binding implemented in
[oshi-meshcore-bridge](https://github.com/Lastoneparis/oshi-meshcore-bridge) and in the OSHI apps, and **tested over the
air** on 2026-09-23 between two Heltec V3 radios running MeshCore companion firmware 1.17.1 (869.618 MHz, BW 62.5 kHz,
SF8): 600 bytes (4 frames, 7 datagrams) and 2,000 bytes (11 frames, 22 datagrams) received byte for byte, in both
directions, with [`tools/meshcore_air_test.py`](../tools/meshcore_air_test.py). The bridge's code was then run between a
simulated Meshtastic side and the same two MeshCore radios: messages crossed both ways byte for byte
([oshi-mesh-firmware `tools/oshi/bridge_air_test.py`](https://github.com/Lastoneparis/oshi-mesh-firmware/blob/oshi/tools/oshi/bridge_air_test.py)).
A run with real radios on both networks is next.

## 1. Meshtastic

Specified in [OMP-v1.md section 2](OMP-v1.md#2-transport): portnum `PRIVATE_APP` (256), secondary channel `OSHI` with the
published PSK, one frame per packet.

### 1.1 A phone on a stock Meshtastic radio

A radio running stock Meshtastic firmware does not implement OMP. An app paired with one implements the sender and
receiver side itself:

- It adds the `OSHI` channel to the radio (first disabled slot 1-7, never replacing an existing channel) through the
  standard admin `set_channel`.
- It sends DATA frames with **`origin` = the radio's own node number**. `origin = 0` is reserved for a phone handing
  frames to an OMP-aware radio, which stamps itself; a stock radio stamps nothing.
- It sends every fragment as a broadcast packet on the `OSHI` channel, paced at least 2.5 s apart.
- It reassembles DATA frames it receives, keyed by `(origin, msgId)`.

OMP-aware nodes treat these frames like any other broadcast DATA and hand the whole message to their phone.

## 2. MeshCore

A LoRa radio listens to one network at a time: MeshCore uses its own radio settings and packet format, so a Meshtastic
radio never hears MeshCore traffic. OMP rides MeshCore as **group-channel datagrams** (`PAYLOAD_TYPE_GRP_DATA`).

| Item | Value |
| --- | --- |
| Channel name | `#oshi-bridge` |
| Channel secret (128-bit) | SHA-256(`"#oshi-bridge"`)[0:16] = `485a8d4bcd50fd075ea87d7ee19f4135` |
| Datagram `data_type` | `0xFF4F` (MeshCore's unregistered development range `0xFF00`-`0xFFFE`) |
| Routing | flood (path length `0xFF`) |
| Maximum datagram used | 160 bytes (MeshCore allows 167: `MAX_FRAME_SIZE` 176 - 9) |

A MeshCore node that does not carry the `#oshi-bridge` channel cannot decrypt these datagrams and never shows them.
MeshCore repeaters flood `GRP_DATA` exactly like `GRP_TXT`, whether or not they can decrypt it (MeshCore `src/Mesh.cpp`),
subject to their hop limits and region settings: a repeater that denies unscoped floods will not carry them.

### 2.1 The OB envelope

An OMP frame (up to 200 bytes) does not fit one datagram, so it is wrapped and split:

```
0-1  "OB" (4F 42)
2    version << 4          version 1, low nibble 0
3-6  sender u32 LE         who put the frame on MeshCore: a bridge's Meshtastic node number, or an app's own id
7    seq u8                per-sender sequence number of the OMP frame
8    part << 4 | total     part index (0-based), number of parts (1..15)
9..  slice of the OMP frame
```

A 200-byte frame travels as two datagrams of 160 and 58 bytes. Receivers:

- ignore datagrams whose marker or version do not match, or with `total = 0` or `part >= total`;
- ignore their own `sender`;
- join parts by `(sender, seq)`, dropping incomplete sets after 90 seconds;
- then decode the joined bytes as an OMP frame.

Test vectors: [`vectors/omp-v1.json`](../vectors/omp-v1.json), section `ob_envelope`.

### 2.2 An app on a MeshCore companion radio

Over the companion protocol (MeshCore `examples/companion_radio/MyMesh.cpp`; BLE Nordic UART service
`6E400001-B5A3-F393-E0A9-E50E24DCCA9E`, or USB serial framed as `<` / `>` + length LE16):

| Step | Command (app -> radio) | Reply |
| --- | --- | --- |
| Start | `01` + 7 reserved bytes + app name | `05` SELF_INFO (public key at bytes 4-35) |
| Find the channel | `1F idx` for idx 0..7 | `12` CHANNEL_INFO: idx, name (32, NUL-padded), secret (16) |
| Add it if absent | `20 idx` + name (32) + secret (16), **only in an empty slot**, never over a channel the owner set | `00` OK |
| Send | `3E idx FF 4F FF` + datagram | `00` OK |
| Receive | on push `83`, send `0A` until reply `0A` (no more) | `1B` CHANNEL_DATA_RECV: snr, 0, 0, channel idx, path length, data type LE16, length, data |

The app uses the first four bytes of the radio's public key (little-endian) as its `origin` and envelope `sender`.

**Drain the radio as datagrams arrive.** A companion radio queues at most 16 messages for its app
(`OFFLINE_QUEUE_SIZE`) and, when full, drops the **oldest channel messages** first. An app must answer every `83` push
with `0A` requests right away. Measured: a 2,000-byte message (22 datagrams) read only after the sender finished lost 6
datagrams (4 of 11 frames); read as it arrived, it was complete. While the phone is away from its radio, only about
16 datagrams (roughly 1.2 KB of OMP) survive; OMP's repair round recovers the rest from an OMP-aware sender.

## 3. Bridging between networks

A bridge holds one radio on each network and repeats OMP frames between them. It can be a dedicated host
([oshi-meshcore-bridge](https://github.com/Lastoneparis/oshi-meshcore-bridge)) or a phone paired with one radio of each.

Rules every bridge follows:

1. **Only broadcast DATA is repeated by a phone bridge.** Unicast OMP belongs to the sender's SACK / custody / RECEIPT
   machinery; a dedicated bridge may proxy it (see oshi-meshcore-bridge), a phone does not.
2. **Frames keep `msgId`, `idx` and `count`.** An OMP-aware radio re-stamps the `origin` of frames its phone hands it, so
   loop suppression must not key on `origin`: key on `msgId:idx:count`, remember it for 30 minutes, and never repeat a
   frame twice.
3. **Airtime is budgeted per radio** (oshi-meshcore-bridge defaults to 5 % per hour) and frames are paced at least
   2.5 s apart.
4. **A bridge announces itself** on its Meshtastic side with an OMP BEACON carrying `CAP_BRIDGE` (bit 2), so OSHI nodes
   accept the RECEIPTs it sends on behalf of the far side.

## 4. Adding a transport

Any network that carries at least 160 bytes per packet, or can carry the OB envelope, can carry OMP. A new binding must
state: how frames are addressed and encrypted on that network, how a node ignores its own frames, the maximum payload,
and the pacing. Proposals are welcome as issues on this repository.

---

This document is licensed under [CC BY 4.0](../LICENSE-SPEC). Copyright (c) 2026 Oshi Lab.
