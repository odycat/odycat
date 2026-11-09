# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/beckhoff/__init__.py
"""Beckhoff terminals."""

from .el1809 import EL1809
from .el2809 import EL2809

__all__ = ['EL1809', 'EL2809']
