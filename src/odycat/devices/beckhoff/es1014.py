# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/beckhoff/es1014.py
"""Beckhoff ES1014 — the EL1014 with pluggable wiring."""

from .el1014 import EL1014


class ES1014(EL1014):
    """Beckhoff ES1014 — 4-channel digital input, pluggable wiring.

    The same terminal as the EL1014, and the same class: it reports the
    EL1014's identity and name on the bus, so `odycat-config scan` writes
    beckhoff.EL1014 for it (and says it also matches beckhoff.ES1014).
    Either name works in a config. Usage::

        card = ES1014(position=1)
        card.input_1(); card.input(1); card.inputs()
    """

    SUPPORT = "tested"
    TESTED_REVISIONS = (0x00130000,)
    PROFILE = EL1014.PROFILE
