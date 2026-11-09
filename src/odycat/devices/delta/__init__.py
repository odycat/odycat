# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/delta/__init__.py
"""Delta Electronics AX-5 remote I/O."""

from .ax516 import AX516AM, AX516AN

__all__ = ['AX516AM', 'AX516AN']
