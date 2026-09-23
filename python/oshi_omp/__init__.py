# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Oshi Lab.
"""Reference codec for the OSHI Mesh Protocol v1 (spec/OMP-v1.md) and the OB envelope (spec/TRANSPORTS.md)."""

from . import omp, wire

__all__ = ["omp", "wire"]
__version__ = "1.0.0"
