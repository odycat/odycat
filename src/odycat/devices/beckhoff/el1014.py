# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/beckhoff/el1014.py
"""Beckhoff EL1014 — 4-channel 24 V digital input terminal."""

from typing import Optional

from odycat.device import Device
from odycat.signal import signal


class EL1014(Device):
    """Beckhoff EL1014 — 4-channel digital input.

    This device has no RX PDO (input-only). Its four inputs are one byte,
    bit n-1 = channel n. The ES1014 (pluggable wiring) is the same terminal:
    see ES1014.

    Channels are numbered 1..4, as on the terminal. Usage::

        card = EL1014(position=1)
        if card.input_1():          # (or card.input(1))
            ...
        card.inputs()               # all 4 as a number, bit n-1 = channel n

    Without (), card.input_1 is the variable itself, for odycat.Events.
    """

    # One TxPDO per channel (0x1A00..0x1A03 -> 0x6000:01, 0x6010:01, ...),
    # in SM0 (the terminal has no mailbox); a gap fills the byte.
    SUPPORT = "tested"
    TESTED_REVISIONS = (0x00130000,)

    PROFILE = {
        "vendor_id": "0x00000002",
        "product_code": "0x03F63052",
        "rxpdo": {"entries": []},
        "txpdo": [{"index": f"0x{0x1A00 + n:04X}", "sm": 0, "entries": [
                      {"index": f"0x{0x6000 + 0x10 * n:04X}", "subindex": 1,
                       "bits": 1, "label": f"input_{n + 1}"}]
                   + ([{"index": "0x0000", "subindex": 0, "bits": 4}] if n == 3 else [])}
                  for n in range(4)],
    }

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None):
        super().__init__(position, alias=alias, label=label, bus=bus,
                         txpdo_fmt="=B", rxpdo_fmt="")

    # ------------------------------------------------------------
    # Channels by number (1..4)
    # ------------------------------------------------------------

    def input(self, channel: int) -> bool:
        """Input channel <channel> (1..4)."""
        if not 1 <= channel <= 4:
            raise ValueError(f"{type(self).__name__}: channel {channel} out of range 1..4")
        return self.txpdo_bit(0, channel - 1)

    def inputs(self) -> int:
        """All 4 inputs as a number, bit n-1 = channel n."""
        return self.read_txpdo()[0] & 0x0F

    # ------------------------------------------------------------
    # Channels by name: input_1() is the value, input_1 the variable
    # ------------------------------------------------------------

    @signal(0x6000, 1)
    def input_1(self) -> bool:
        """Input channel 1"""
        ...

    @signal(0x6010, 1)
    def input_2(self) -> bool:
        """Input channel 2"""
        ...

    @signal(0x6020, 1)
    def input_3(self) -> bool:
        """Input channel 3"""
        ...

    @signal(0x6030, 1)
    def input_4(self) -> bool:
        """Input channel 4"""
        ...
