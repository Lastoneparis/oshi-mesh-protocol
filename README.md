# OSHI Mesh Protocol (OMP)

**An open protocol for reliable messages over LoRa meshes: fragmentation, repair, end-to-end delivery receipts and
store-and-forward, carried inside the networks people already run — Meshtastic today, MeshCore through a bridge.**

[![ci](https://github.com/Lastoneparis/oshi-mesh-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/Lastoneparis/oshi-mesh-protocol/actions/workflows/ci.yml)
[![Spec: CC BY 4.0](https://img.shields.io/badge/spec-CC%20BY%204.0-lightgrey)](LICENSE-SPEC)
[![Code: MIT](https://img.shields.io/badge/code-MIT-blue)](LICENSE)

LoRa mesh networks are good at getting a short text across kilometres with no infrastructure. They are not good at the
rest: a packet holds about 230 bytes, a message to someone offline is lost, and the sender rarely knows whether it
arrived. OMP adds that on top, without asking the existing network to change:

- **Messages up to 11,648 bytes**, split into 182-byte fragments and reassembled.
- **Selective repair**: the receiver answers with a 64-bit bitmap and only the missing fragments are sent again.
- **End-to-end receipts**: the sender learns the whole message was delivered, not just that one packet left.
- **Custody**: a message for someone offline waits on a node (in flash) and is delivered when they are heard again.
- **An internet gateway**, for nodes that can reach one.
- **Compatible by construction**: frames are opaque payloads that stock Meshtastic nodes relay without decoding, and that
  MeshCore repeaters flood like any group-channel message.

How this compares with stock Meshtastic and MeshCore, including where they are ahead:
<https://oshi-messenger.com/lora/why-oshi-mesh>.

## Contents

| | |
| --- | --- |
| [spec/OMP-v1.md](spec/OMP-v1.md) | The protocol: frames, state machines, custody, beacons, phone-to-radio protocol, gateway API, security |
| [spec/TRANSPORTS.md](spec/TRANSPORTS.md) | How OMP rides Meshtastic and MeshCore, and how bridges move it between them |
| [spec/IMPLEMENTING.md](spec/IMPLEMENTING.md) | Implementer's guide with byte-exact test vectors and a checklist |
| [vectors/omp-v1.json](vectors/omp-v1.json) | The same vectors, machine-readable |
| [c/](c/) | C++ reference codec (no dependencies, no allocation) — the one running in the OSHI Mesh firmware |
| [python/](python/) | Python reference codec, including the OB envelope for MeshCore (`pip install ./python`) |

Both codecs are checked against the vectors on every commit, and the vectors against the bytes printed in the spec.

## Quick look

```python
from oshi_omp import omp, wire

body = omp.encode_body(b'{"t":"hello from the mesh"}')          # 0x01 raw or 0x02 raw DEFLATE
frames = omp.fragment(msg_id=0xDEADBEEF, origin=0x12345678, dest=omp.DEST_BROADCAST, body=body)
payloads = [f.encode() for f in frames]                          # each one is a PRIVATE_APP (256) payload

datagrams = wire.split(payloads[0], sender=0x12345678, seq=1)    # the same frame, ready for a MeshCore channel
```

```cpp
#include "oshi_omp.h"
oshi::DataFrame f;          // msgId, origin, dest, idx, count, flags, data, len
uint8_t buf[oshi::OMP_MAX_FRAME];
size_t n = oshi::encodeData(f, buf, sizeof(buf));
```

## Who implements it

| Project | Role | Licence |
| --- | --- | --- |
| [oshi-mesh-firmware](https://github.com/Lastoneparis/oshi-mesh-firmware) | Meshtastic-compatible firmware: full OMP node (repair, custody, receipts, gateway) | GPL-3.0 |
| [oshi-meshcore-bridge](https://github.com/Lastoneparis/oshi-meshcore-bridge) | Carries OMP between Meshtastic and MeshCore (two radios, one host) | GPL-3.0 |
| OSHI messenger (iOS, Android, desktop) | End-to-end encrypted messages over OMP, on OSHI Mesh, stock Meshtastic or MeshCore radios | — |

Implemented elsewhere? Open an issue and it will be listed here.

## Status

Version 1 is stable on the wire. Tested: unit tests and vectors, simulated meshes that include unmodified Meshtastic
nodes (OMP crosses a stock CLIENT and a stock ROUTER that cannot decode it), and over the air between two Heltec V3
radios. The MeshCore binding is tested over the air between two MeshCore companion radios (up to 2 KB, both
directions, `tools/meshcore_air_test.py`). The bridge's code is tested
between a simulated Meshtastic side and real MeshCore radios over the air, both ways; a run with real radios on both
networks is next.

## Licences

- **Specification** (`spec/`, `vectors/`): [Creative Commons Attribution 4.0](LICENSE-SPEC). Implement it, copy it,
  translate it, build on it; credit "OSHI Mesh Protocol, Oshi Lab".
- **Reference code** (`c/`, `python/`, `tools/`): [MIT](LICENSE). Use it in open or closed, GPL or MIT projects —
  MeshCore, Meshtastic forks, apps, gateways.

The firmware and the bridge that also contain this code remain GPL-3.0 as a whole; the codec itself is offered here
under MIT by its authors.

## Contributing

Interoperability reports, new implementations and transport proposals (see
[TRANSPORTS.md section 4](spec/TRANSPORTS.md#4-adding-a-transport)) are welcome as issues. Changes to the wire format go
through an issue first: every change must keep existing nodes interoperable, or bump the version nibble.

OSHI and OMP are independent projects, not affiliated with Meshtastic LLC or the MeshCore project.
