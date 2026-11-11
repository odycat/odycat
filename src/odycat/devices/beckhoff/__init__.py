# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/beckhoff/__init__.py
"""Beckhoff terminals. ES terminals (pluggable wiring) have classes of
their own, made from their EL terminal's."""

from .el1014 import EL1014
from .el1809 import EL1809
from .el2004 import EL2004
from .el2809 import EL2809
from .es1014 import ES1014
from .es2004 import ES2004

__all__ = ['EL1014', 'EL1809', 'EL2004', 'EL2809', 'ES1014', 'ES2004']
