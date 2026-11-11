# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/beckhoff/es2004.py
"""Beckhoff ES2004 — the EL2004 with pluggable wiring."""

from .el2004 import EL2004


class ES2004(EL2004):
    """Beckhoff ES2004 — 4-channel digital output, pluggable wiring.

    The same terminal as the EL2004, and the same class: it reports the
    EL2004's identity and name on the bus, so `odycat-config scan` writes
    beckhoff.EL2004 for it (and says it also matches beckhoff.ES2004).
    Either name works in a config. Usage::

        card = ES2004(position=6)
        card.set_output_1(True); card.set_outputs(0x0F); card.outputs()
    """

    SUPPORT = "tested"
    TESTED_REVISIONS = (0x00120000,)
    PROFILE = EL2004.PROFILE
