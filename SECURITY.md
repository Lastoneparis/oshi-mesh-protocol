# Security Policy

Report a flaw in the protocol or in the reference codecs privately to **contact@oshi-messenger.com**, or through GitHub's
private vulnerability reporting on this repository (Security tab). Please include the frame bytes or a script that
reproduces it. Do not open a public issue for a vulnerability.

We aim to acknowledge a report within a few days and will agree on a disclosure date with you. Security considerations of
the protocol itself are in [spec/OMP-v1.md, section 11](spec/OMP-v1.md#11-security-considerations).

Firmware-side issues belong to [oshi-mesh-firmware](https://github.com/Lastoneparis/oshi-mesh-firmware/security/policy).

## Encrypted reports (PGP)

Encrypt sensitive reports to the OSHI project key and send them to **contact@oshi-messenger.com**:

- Fingerprint: `9CEB A168 37CA 9FFE 024B  4671 2BE8 9080 B91F E733`
- Key: [`oshi-project-key.asc`](oshi-project-key.asc) in this repository, or https://oshi-messenger.com/.well-known/pgp-key.txt

The previous project key (`CC57 EFDF DD66 DF95 1F9C  8108 025F 383C DCCA 2600`, created 2026-09-08) is **revoked**.
Do not encrypt to it: we can no longer read messages sent to that key.
