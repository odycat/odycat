# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
from __future__ import annotations

from typing import Iterable, Optional

from odycat.device import Device
from odycat.signal import signal


class EC3A_IO1632(Device):
    """AMSA Motion EC3A-IO1632 — 16 digital inputs IN00…IN15 and 16 digital
    outputs OUT00…OUT15, named as printed on the module.

    The TX PDO is one 16-bit word, bit n = IN<n>; the RX PDO likewise, bit
    n = OUT<n>. Usage::

        card = EC3A_IO1632(alias=40)
        card.in03()                 # IN03 now (or card.input("IN03"), card.input(3))
        card.set_out00(True)        # (or card.set_output("OUT00", True))
        card.inputs()               # all 16 as a word, bit n = IN<n>

    Without (), card.in03 is the variable itself, for odycat.Events:
    @ev.rising(card.in03).
    """

    SUPPORT = "tested"
    TESTED_REVISIONS = (0x01020109,)

    PROFILE = {
        "vendor_id": "0x00000B95",
        "product_code": "0x00001101",
        "rxpdo": {"index": "0x1600", "entries": [
            {"index": "0x7000", "subindex": 1, "type": "u16", "label": "outputs"},
        ]},
        "txpdo": {"index": "0x1A00", "entries": [
            {"index": "0x6000", "subindex": 1, "type": "u16", "label": "inputs"},
        ]},
    }

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None):
        super().__init__(position, alias=alias, label=label, bus=bus,
                         txpdo_fmt="=H", rxpdo_fmt="=H")

    # ------------------------------------------------------------
    # Any point: by its printed name ("IN03", "OUT00") or its number
    # ------------------------------------------------------------

    @staticmethod
    def _number(point: str | int, prefix: str) -> int:
        """The n of IN<n>/OUT<n>: from "IN03" (any case) or 3."""
        if isinstance(point, str):
            name = point.strip().upper()
            if not (name.startswith(prefix) and name[len(prefix):].isdigit()):
                raise ValueError(f"EC3A_IO1632: {point!r} is not one of "
                                 f"{prefix}00..{prefix}15")
            point = int(name[len(prefix):])
        if not 0 <= point <= 15:
            raise ValueError(f"EC3A_IO1632: {prefix}{point:02d} out of range "
                             f"{prefix}00..{prefix}15")
        return point

    def input(self, point: str | int) -> bool:
        """Input IN<n>: input("IN03") or input(3)."""
        return self.txpdo_bit(0, self._number(point, "IN"))

    def output(self, point: str | int) -> bool:
        """Output OUT<n>, as written: output("OUT00") or output(0)."""
        return self.rxpdo_bit(0, self._number(point, "OUT"))

    def set_output(self, point: str | int, state: bool) -> None:
        """Set output OUT<n>: set_output("OUT00", True); no other output changes."""
        self.set_rxpdo_bit(0, self._number(point, "OUT"), state)

    @signal(0x6000, 1)
    def inputs(self) -> int:
        """All 16 inputs as a word, bit n = IN<n>."""
        ...

    @signal(0x7000, 1)
    def outputs(self) -> int:
        """All 16 outputs as written, bit n = OUT<n>."""
        ...

    def set_outputs(self, states: Iterable[bool] | int) -> None:
        """Set all 16 outputs from a word (bit n = OUT<n>) or 16 bools."""
        self.set_rxpdo_field(0, _mask_from(states))

    # ------------------------------------------------------------
    # Each point by name: in03() is the value, in03 the variable
    # ------------------------------------------------------------

    @signal(0x6000, 1, bit=0)
    def in00(self) -> bool:
        """Input IN00"""
        ...

    @signal(0x6000, 1, bit=1)
    def in01(self) -> bool:
        """Input IN01"""
        ...

    @signal(0x6000, 1, bit=2)
    def in02(self) -> bool:
        """Input IN02"""
        ...

    @signal(0x6000, 1, bit=3)
    def in03(self) -> bool:
        """Input IN03"""
        ...

    @signal(0x6000, 1, bit=4)
    def in04(self) -> bool:
        """Input IN04"""
        ...

    @signal(0x6000, 1, bit=5)
    def in05(self) -> bool:
        """Input IN05"""
        ...

    @signal(0x6000, 1, bit=6)
    def in06(self) -> bool:
        """Input IN06"""
        ...

    @signal(0x6000, 1, bit=7)
    def in07(self) -> bool:
        """Input IN07"""
        ...

    @signal(0x6000, 1, bit=8)
    def in08(self) -> bool:
        """Input IN08"""
        ...

    @signal(0x6000, 1, bit=9)
    def in09(self) -> bool:
        """Input IN09"""
        ...

    @signal(0x6000, 1, bit=10)
    def in10(self) -> bool:
        """Input IN10"""
        ...

    @signal(0x6000, 1, bit=11)
    def in11(self) -> bool:
        """Input IN11"""
        ...

    @signal(0x6000, 1, bit=12)
    def in12(self) -> bool:
        """Input IN12"""
        ...

    @signal(0x6000, 1, bit=13)
    def in13(self) -> bool:
        """Input IN13"""
        ...

    @signal(0x6000, 1, bit=14)
    def in14(self) -> bool:
        """Input IN14"""
        ...

    @signal(0x6000, 1, bit=15)
    def in15(self) -> bool:
        """Input IN15"""
        ...

    @signal(0x7000, 1, bit=0)
    def out00(self) -> bool:
        """Output OUT00, as written"""
        ...

    def set_out00(self, state: bool) -> None:
        """Set output OUT00; no other output changes"""
        self.set_output(0, state)

    @signal(0x7000, 1, bit=1)
    def out01(self) -> bool:
        """Output OUT01, as written"""
        ...

    def set_out01(self, state: bool) -> None:
        """Set output OUT01; no other output changes"""
        self.set_output(1, state)

    @signal(0x7000, 1, bit=2)
    def out02(self) -> bool:
        """Output OUT02, as written"""
        ...

    def set_out02(self, state: bool) -> None:
        """Set output OUT02; no other output changes"""
        self.set_output(2, state)

    @signal(0x7000, 1, bit=3)
    def out03(self) -> bool:
        """Output OUT03, as written"""
        ...

    def set_out03(self, state: bool) -> None:
        """Set output OUT03; no other output changes"""
        self.set_output(3, state)

    @signal(0x7000, 1, bit=4)
    def out04(self) -> bool:
        """Output OUT04, as written"""
        ...

    def set_out04(self, state: bool) -> None:
        """Set output OUT04; no other output changes"""
        self.set_output(4, state)

    @signal(0x7000, 1, bit=5)
    def out05(self) -> bool:
        """Output OUT05, as written"""
        ...

    def set_out05(self, state: bool) -> None:
        """Set output OUT05; no other output changes"""
        self.set_output(5, state)

    @signal(0x7000, 1, bit=6)
    def out06(self) -> bool:
        """Output OUT06, as written"""
        ...

    def set_out06(self, state: bool) -> None:
        """Set output OUT06; no other output changes"""
        self.set_output(6, state)

    @signal(0x7000, 1, bit=7)
    def out07(self) -> bool:
        """Output OUT07, as written"""
        ...

    def set_out07(self, state: bool) -> None:
        """Set output OUT07; no other output changes"""
        self.set_output(7, state)

    @signal(0x7000, 1, bit=8)
    def out08(self) -> bool:
        """Output OUT08, as written"""
        ...

    def set_out08(self, state: bool) -> None:
        """Set output OUT08; no other output changes"""
        self.set_output(8, state)

    @signal(0x7000, 1, bit=9)
    def out09(self) -> bool:
        """Output OUT09, as written"""
        ...

    def set_out09(self, state: bool) -> None:
        """Set output OUT09; no other output changes"""
        self.set_output(9, state)

    @signal(0x7000, 1, bit=10)
    def out10(self) -> bool:
        """Output OUT10, as written"""
        ...

    def set_out10(self, state: bool) -> None:
        """Set output OUT10; no other output changes"""
        self.set_output(10, state)

    @signal(0x7000, 1, bit=11)
    def out11(self) -> bool:
        """Output OUT11, as written"""
        ...

    def set_out11(self, state: bool) -> None:
        """Set output OUT11; no other output changes"""
        self.set_output(11, state)

    @signal(0x7000, 1, bit=12)
    def out12(self) -> bool:
        """Output OUT12, as written"""
        ...

    def set_out12(self, state: bool) -> None:
        """Set output OUT12; no other output changes"""
        self.set_output(12, state)

    @signal(0x7000, 1, bit=13)
    def out13(self) -> bool:
        """Output OUT13, as written"""
        ...

    def set_out13(self, state: bool) -> None:
        """Set output OUT13; no other output changes"""
        self.set_output(13, state)

    @signal(0x7000, 1, bit=14)
    def out14(self) -> bool:
        """Output OUT14, as written"""
        ...

    def set_out14(self, state: bool) -> None:
        """Set output OUT14; no other output changes"""
        self.set_output(14, state)

    @signal(0x7000, 1, bit=15)
    def out15(self) -> bool:
        """Output OUT15, as written"""
        ...

    def set_out15(self, state: bool) -> None:
        """Set output OUT15; no other output changes"""
        self.set_output(15, state)


def _mask_from(states: Iterable[bool] | int) -> int:
    if isinstance(states, int):
        return states & 0xFFFF
    states = list(states)
    if len(states) != 16:
        raise ValueError(f"EC3A_IO1632: expected 16 states, got {len(states)}")
    return sum(1 << n for n, on in enumerate(states) if on)
