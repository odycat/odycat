# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/mdp/__init__.py
"""ETG.5001 Modular Device Profile, the layout most EtherCAT I/O uses:
inputs at 0x6000 + 0x10·n, outputs at 0x7000 + 0x10·n."""

from .digital_io import DigitalIO

__all__ = ['DigitalIO']
