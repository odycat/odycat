# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/cia401/__init__.py
"""CiA 401 device profile for generic I/O modules (CANopen objects 0x6000,
0x6100, 0x6120, 0x6200, 0x6300, 0x6320, 0x6401, 0x6411)."""

from .generic_io import GenericIO

__all__ = ['GenericIO']
