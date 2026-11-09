# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/mdp/digital_io.py
"""
DigitalIO — any digital I/O slave following the Modular Device Profile
(ETG.5001): inputs mapped from 0x6000-0x6FFF, outputs from 0x7000-0x7FFF.

Generic: there is no device profile (no identity); point it at a slave whose
config maps those objects (e.g. written by odycat-config scan). Channels are
found by object index, so labels and entry order in the config are free.

Channel n (0-based) is the n-th digital bit in object order: one 1-bit
object per channel (Beckhoff EL1809: 0x6000:01, 0x6010:01, ...), 1-bit
subindices (0x6000:01..08), or the bits of an unsigned word (0x6000:01 as
16 bits) all number the same way. Signed and float entries (analog values)
are not channels; read those with read_entry("0x6000:11").

    io = DigitalIO(alias=40)
    io.input(3); io.inputs()                  # value now; all as a word
    io.set_output(0, True); io.outputs()
    @ev.rising(io.input_variable(3))          # the variable, for odycat.Events
"""

from __future__ import annotations

from typing import Iterable, Optional

from odycat.device import Device
from odycat.devices._channels import Channels, is_digital
from odycat.signal import Signal


def _area(entries: list, lo: int, hi: int) -> list:
    picked = [e for e in entries if lo <= int(e["index"], 16) <= hi and is_digital(e)]
    return sorted(picked, key=lambda e: (int(e["index"], 16), e["subindex"]))


class DigitalIO(Device):
    """Generic Modular Device Profile digital I/O."""

    SUPPORT = "tested"
    TESTED_ON = ("amsamotion.EC3A_IO1632", "delta.AX516AM", "delta.AX516AN")

    def __init__(self, position: Optional[int] = None, *, alias: int = 0,
                 label: Optional[str] = None, bus: Optional[str] = None):
        super().__init__(position, alias=alias, label=label, bus=bus)
        inputs, outputs = self.entries()
        self._in = Channels(self, _area(inputs, 0x6000, 0x6FFF), False, "input")
        self._out = Channels(self, _area(outputs, 0x7000, 0x7FFF), True, "output")
        if not len(self._in) and not len(self._out):
            raise ValueError(f"DigitalIO '{self._slave_label}': no digital objects at "
                             f"0x6000-0x6FFF / 0x7000-0x7FFF in this slave's config")
        self.n_inputs = len(self._in)
        self.n_outputs = len(self._out)

    def input(self, channel: int) -> bool:
        """Digital input <channel>."""
        return self._in.read(channel)

    def inputs(self) -> int:
        """All digital inputs as a word, bit n = channel n."""
        return self._in.word()

    def output(self, channel: int) -> bool:
        """Digital output <channel>, as written."""
        return self._out.read(channel)

    def outputs(self) -> int:
        """All digital outputs as written, bit n = channel n."""
        return self._out.word()

    def set_output(self, channel: int, state: bool) -> None:
        """Set digital output <channel>; no other output changes."""
        self._out.write(channel, state)

    def set_outputs(self, states: Iterable[bool] | int) -> None:
        """Set all digital outputs from a word (bit n = channel n) or bools."""
        self._out.set_word(states)

    def input_variable(self, channel: int) -> Signal:
        """Input <channel> as a variable, for odycat.Events."""
        return self._in.variable(channel)

    def output_variable(self, channel: int) -> Signal:
        """Output <channel> as a variable, for odycat.Events."""
        return self._out.variable(channel)
