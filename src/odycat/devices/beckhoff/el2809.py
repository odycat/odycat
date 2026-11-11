# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/beckhoff/el2809.py
"""Beckhoff EL2809 — 16-channel 24 V digital output terminal."""

from __future__ import annotations

from typing import Optional

from odycat.device import Device
from odycat.signal import signal


class EL2809(Device):
    """Beckhoff EL2809 — 16-channel digital output.

    This device has no TX PDO (output-only). The RX PDO is a single
    unsigned 16-bit word where each bit maps to one output channel.

    Channels are numbered 1..16, as on the terminal. Usage::

        card = EL2809(position=0)
        card.set_output_1(True)     # (or card.set_output(1, True))
        card.set_outputs(0xFFFF)    # all 16, bit n-1 = channel n
        card.output_1()             # as written

    Without (), card.output_1 is the variable itself, for odycat.Events.
    """

    # One RxPDO per channel (0x1600..0x160F -> 0x7000:01, 0x7010:01, ...).
    # The terminal has no mailbox and a sync manager per byte: channels
    # 1-8 in SM0, 9-16 in SM1. Packed by the engine into 2 bytes: bit n-1 =
    # channel n.
    SUPPORT = "community"

    PROFILE = {
        "vendor_id": "0x00000002",
        "product_code": "0x0AF93052",
        "rxpdo": [{"index": f"0x{0x1600 + n:04X}", "sm": n // 8, "entries": [
                      {"index": f"0x{0x7000 + 0x10 * n:04X}", "subindex": 1,
                       "bits": 1, "label": f"output_{n + 1}"}]} for n in range(16)],
        "txpdo": {"entries": []},
    }

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None):
        super().__init__(position, alias=alias, label=label, bus=bus,
                         txpdo_fmt="", rxpdo_fmt="=H")

    # ------------------------------------------------------------
    # Channels by number (1..16)
    # ------------------------------------------------------------

    @staticmethod
    def _channel(channel: int) -> int:
        if not 1 <= channel <= 16:
            raise ValueError(f"EL2809: channel {channel} out of range 1..16")
        return channel

    def output(self, channel: int) -> bool:
        """Output channel <channel> (1..16), as written."""
        return self.rxpdo_bit(0, self._channel(channel) - 1)

    def set_output(self, channel: int, state: bool) -> None:
        """Set output channel <channel> (1..16); no other output changes."""
        self.set_rxpdo_bit(0, self._channel(channel) - 1, state)

    def outputs(self) -> int:
        """All 16 outputs as written, bit n-1 = channel n."""
        return self.read_rxpdo()[0]

    def set_outputs(self, states: list[bool] | int) -> None:
        """Set all 16 outputs from a word (bit n-1 = channel n) or 16 bools."""
        if not isinstance(states, int):
            if len(states) != 16:
                raise ValueError("EL2809: need exactly 16 values")
            states = sum(int(b) << i for i, b in enumerate(states))
        self.write_rxpdo(states & 0xFFFF)

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

    @signal(0x7040, 1)
    def output_5(self) -> bool:
        """Output channel 5, as written."""
        ...

    def set_output_5(self, state: bool) -> None:
        """Set output channel 5."""
        self.set_output(5, state)

    @signal(0x7050, 1)
    def output_6(self) -> bool:
        """Output channel 6, as written."""
        ...

    def set_output_6(self, state: bool) -> None:
        """Set output channel 6."""
        self.set_output(6, state)

    @signal(0x7060, 1)
    def output_7(self) -> bool:
        """Output channel 7, as written."""
        ...

    def set_output_7(self, state: bool) -> None:
        """Set output channel 7."""
        self.set_output(7, state)

    @signal(0x7070, 1)
    def output_8(self) -> bool:
        """Output channel 8, as written."""
        ...

    def set_output_8(self, state: bool) -> None:
        """Set output channel 8."""
        self.set_output(8, state)

    @signal(0x7080, 1)
    def output_9(self) -> bool:
        """Output channel 9, as written."""
        ...

    def set_output_9(self, state: bool) -> None:
        """Set output channel 9."""
        self.set_output(9, state)

    @signal(0x7090, 1)
    def output_10(self) -> bool:
        """Output channel 10, as written."""
        ...

    def set_output_10(self, state: bool) -> None:
        """Set output channel 10."""
        self.set_output(10, state)

    @signal(0x70A0, 1)
    def output_11(self) -> bool:
        """Output channel 11, as written."""
        ...

    def set_output_11(self, state: bool) -> None:
        """Set output channel 11."""
        self.set_output(11, state)

    @signal(0x70B0, 1)
    def output_12(self) -> bool:
        """Output channel 12, as written."""
        ...

    def set_output_12(self, state: bool) -> None:
        """Set output channel 12."""
        self.set_output(12, state)

    @signal(0x70C0, 1)
    def output_13(self) -> bool:
        """Output channel 13, as written."""
        ...

    def set_output_13(self, state: bool) -> None:
        """Set output channel 13."""
        self.set_output(13, state)

    @signal(0x70D0, 1)
    def output_14(self) -> bool:
        """Output channel 14, as written."""
        ...

    def set_output_14(self, state: bool) -> None:
        """Set output channel 14."""
        self.set_output(14, state)

    @signal(0x70E0, 1)
    def output_15(self) -> bool:
        """Output channel 15, as written."""
        ...

    def set_output_15(self, state: bool) -> None:
        """Set output channel 15."""
        self.set_output(15, state)

    @signal(0x70F0, 1)
    def output_16(self) -> bool:
        """Output channel 16, as written."""
        ...

    def set_output_16(self, state: bool) -> None:
        """Set output channel 16."""
        self.set_output(16, state)
