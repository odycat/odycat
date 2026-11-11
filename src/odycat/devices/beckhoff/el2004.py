# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/beckhoff/el2004.py
"""Beckhoff EL2004 — 4-channel 24 V digital output terminal."""

from __future__ import annotations

from typing import Optional

from odycat.device import Device
from odycat.signal import signal


class EL2004(Device):
    """Beckhoff EL2004 — 4-channel digital output.

    This device has no TX PDO (output-only). Its four outputs are one byte,
    bit n-1 = channel n. The ES2004 (pluggable wiring) is the same terminal:
    see ES2004.

    Channels are numbered 1..4, as on the terminal. Usage::

        card = EL2004(position=3)
        card.set_output_1(True)     # (or card.set_output(1, True))
        card.set_outputs(0x0F)      # all 4, bit n-1 = channel n
        card.output_1()             # as written

    Without (), card.output_1 is the variable itself, for odycat.Events.
    """

    # One RxPDO per channel (0x1600..0x1603 -> 0x7000:01, 0x7010:01, ...),
    # in SM0 (the terminal has no mailbox); a gap fills the byte.
    SUPPORT = "tested"
    TESTED_REVISIONS = (0x00120000,)

    PROFILE = {
        "vendor_id": "0x00000002",
        "product_code": "0x07D43052",
        "rxpdo": [{"index": f"0x{0x1600 + n:04X}", "sm": 0, "entries": [
                      {"index": f"0x{0x7000 + 0x10 * n:04X}", "subindex": 1,
                       "bits": 1, "label": f"output_{n + 1}"}]
                   + ([{"index": "0x0000", "subindex": 0, "bits": 4}] if n == 3 else [])}
                  for n in range(4)],
        "txpdo": {"entries": []},
    }

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None):
        super().__init__(position, alias=alias, label=label, bus=bus,
                         txpdo_fmt="", rxpdo_fmt="=B")

    # ------------------------------------------------------------
    # Channels by number (1..4)
    # ------------------------------------------------------------

    def _channel(self, channel: int) -> int:
        if not 1 <= channel <= 4:
            raise ValueError(f"{type(self).__name__}: channel {channel} out of range 1..4")
        return channel

    def output(self, channel: int) -> bool:
        """Output channel <channel> (1..4), as written."""
        return self.rxpdo_bit(0, self._channel(channel) - 1)

    def set_output(self, channel: int, state: bool) -> None:
        """Set output channel <channel> (1..4); no other output changes."""
        self.set_rxpdo_bit(0, self._channel(channel) - 1, state)

    def outputs(self) -> int:
        """All 4 outputs as written, bit n-1 = channel n."""
        return self.read_rxpdo()[0] & 0x0F

    def set_outputs(self, states: list[bool] | int) -> None:
        """Set all 4 outputs from a number (bit n-1 = channel n) or 4 bools."""
        if not isinstance(states, int):
            if len(states) != 4:
                raise ValueError(f"{type(self).__name__}: need exactly 4 values")
            states = sum(int(b) << i for i, b in enumerate(states))
        self.write_rxpdo(states & 0x0F)

    # ------------------------------------------------------------
    # Channels by name: output_1() is the value, output_1 the variable
    # ------------------------------------------------------------

    @signal(0x7000, 1)
    def output_1(self) -> bool:
        """Output channel 1, as written."""
        ...

    def set_output_1(self, state: bool) -> None:
        """Set output channel 1."""
        self.set_output(1, state)

    @signal(0x7010, 1)
    def output_2(self) -> bool:
        """Output channel 2, as written."""
        ...

    def set_output_2(self, state: bool) -> None:
        """Set output channel 2."""
        self.set_output(2, state)

    @signal(0x7020, 1)
    def output_3(self) -> bool:
        """Output channel 3, as written."""
        ...

    def set_output_3(self, state: bool) -> None:
        """Set output channel 3."""
        self.set_output(3, state)

    @signal(0x7030, 1)
    def output_4(self) -> bool:
        """Output channel 4, as written."""
        ...

    def set_output_4(self, state: bool) -> None:
        """Set output channel 4."""
        self.set_output(4, state)
