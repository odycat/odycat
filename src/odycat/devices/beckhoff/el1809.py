# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/beckhoff/el1809.py
"""Beckhoff EL1809 — 16-channel 24 V digital input terminal."""

from typing import Optional

from odycat.device import Device
from odycat.signal import signal


class EL1809(Device):
    """Beckhoff EL1809 — 16-channel digital input.

    This device has no RX PDO (input-only). The TX PDO is a single
    unsigned 16-bit word where each bit maps to one input channel.

    Channels are numbered 1..16, as on the terminal. Usage::

        card = EL1809(position=1)
        if card.input_1():          # (or card.input(1))
            ...
        card.inputs()               # all 16 as a word, bit n-1 = channel n

    Without (), card.input_1 is the variable itself, for odycat.Events.
    """

    # One TxPDO per channel (0x1A00..0x1A0F -> 0x6000:01, 0x6010:01, ...),
    # all in SM0 (the terminal has no mailbox), packed by the engine into
    # 2 bytes: bit n-1 = channel n.
    SUPPORT = "community"

    PROFILE = {
        "vendor_id": "0x00000002",
        "product_code": "0x07113052",
        "rxpdo": {"entries": []},
        "txpdo": [{"index": f"0x{0x1A00 + n:04X}", "sm": 0, "entries": [
                      {"index": f"0x{0x6000 + 0x10 * n:04X}", "subindex": 1,
                       "bits": 1, "label": f"input_{n + 1}"}]} for n in range(16)],
    }

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None):
        super().__init__(position, alias=alias, label=label, bus=bus,
                         txpdo_fmt="=H", rxpdo_fmt="")

    # ------------------------------------------------------------
    # Channels by number (1..16)
    # ------------------------------------------------------------

    def input(self, channel: int) -> bool:
        """Input channel <channel> (1..16)."""
        if not 1 <= channel <= 16:
            raise ValueError(f"EL1809: channel {channel} out of range 1..16")
        return self.txpdo_bit(0, channel - 1)

    def inputs(self) -> int:
        """All 16 inputs as a word, bit n-1 = channel n."""
        return self.read_txpdo()[0]

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

    @signal(0x6040, 1)
    def input_5(self) -> bool:
        """Input channel 5"""
        ...

    @signal(0x6050, 1)
    def input_6(self) -> bool:
        """Input channel 6"""
        ...

    @signal(0x6060, 1)
    def input_7(self) -> bool:
        """Input channel 7"""
        ...

    @signal(0x6070, 1)
    def input_8(self) -> bool:
        """Input channel 8"""
        ...

    @signal(0x6080, 1)
    def input_9(self) -> bool:
        """Input channel 9"""
        ...

    @signal(0x6090, 1)
    def input_10(self) -> bool:
        """Input channel 10"""
        ...

    @signal(0x60A0, 1)
    def input_11(self) -> bool:
        """Input channel 11"""
        ...

    @signal(0x60B0, 1)
    def input_12(self) -> bool:
        """Input channel 12"""
        ...

    @signal(0x60C0, 1)
    def input_13(self) -> bool:
        """Input channel 13"""
        ...

    @signal(0x60D0, 1)
    def input_14(self) -> bool:
        """Input channel 14"""
        ...

    @signal(0x60E0, 1)
    def input_15(self) -> bool:
        """Input channel 15"""
        ...

    @signal(0x60F0, 1)
    def input_16(self) -> bool:
        """Input channel 16"""
        ...
