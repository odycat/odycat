# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/cia401/generic_io.py
"""
GenericIO — any I/O slave following the CiA 401 profile (generic I/O).

Generic: no device profile (no identity); point it at a slave whose config
maps the CiA 401 objects. In EtherCAT most I/O follows the Modular Device
Profile instead (odycat.devices.mdp); CiA 401 shows up on CANopen-derived
devices.

Digital channels (0-based, bit n = channel n) come from the first input
family mapped among 0x6000 (8-bit groups), 0x6100 (16-bit), 0x6120
(32-bit), and the first output family among 0x6200, 0x6300, 0x6320. The
families are the same channels at different widths, so only one is used.
Analog channels follow the profile's subindices (1-based): inputs 0x6401
(16-bit) or 0x6402 (32-bit), outputs 0x6411 / 0x6412, raw values.

    io = GenericIO(label="remote_io")
    io.input(3); io.set_output(0, True)
    io.analog_input(1); io.set_analog_output(2, 1200)
    @ev.change(io.analog_input_variable(1), deadband=50)
"""

from __future__ import annotations

from typing import Iterable, Optional

from odycat.device import Device, ObjectNotMappedError
from odycat.devices._channels import Channels
from odycat.signal import Signal, SignalSpec

DIGITAL_IN = (0x6000, 0x6100, 0x6120)
DIGITAL_OUT = (0x6200, 0x6300, 0x6320)
ANALOG_IN = (0x6401, 0x6402)
ANALOG_OUT = (0x6411, 0x6412)


def _family(entries: list, indexes: tuple) -> list:
    for index in indexes:
        group = [e for e in entries if int(e["index"], 16) == index and e["subindex"] > 0]
        if group:
            return sorted(group, key=lambda e: e["subindex"])
    return []


class GenericIO(Device):
    """Generic CiA 401 digital and analog I/O."""

    SUPPORT = "community"

    def __init__(self, position: Optional[int] = None, *, alias: int = 0,
                 label: Optional[str] = None, bus: Optional[str] = None):
        super().__init__(position, alias=alias, label=label, bus=bus)
        inputs, outputs = self.entries()
        self._in = Channels(self, _family(inputs, DIGITAL_IN), False, "input")
        self._out = Channels(self, _family(outputs, DIGITAL_OUT), True, "output")
        self.n_inputs = len(self._in)
        self.n_outputs = len(self._out)
        if not (self.n_inputs or self.n_outputs or self._analog(ANALOG_IN, 1, False)
                or self._analog(ANALOG_OUT, 1, True)):
            raise ValueError(f"GenericIO '{self._slave_label}': no CiA 401 objects "
                             f"(0x6000/0x6100/0x6120, 0x6200/0x6300/0x6320, 0x6401, "
                             f"0x6411) in this slave's config")

    # -- digital --------------------------------------------------------------

    def input(self, channel: int) -> bool:
        """Digital input <channel> (0-based)."""
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
        """Digital input <channel> as a variable, for odycat.Events."""
        return self._in.variable(channel)

    def output_variable(self, channel: int) -> Signal:
        """Digital output <channel> as a variable, for odycat.Events."""
        return self._out.variable(channel)

    # -- analog (subindex = channel, 1-based, raw) --------------------------------

    def _analog(self, indexes: tuple, channel: int, output: bool):
        """(entry, is_output) of an analog channel, or None if not mapped."""
        for index in indexes:
            found = self._object_map().get((index, channel))
            if found is not None and found[1] == output:
                return found
        return None

    def _analog_entry(self, indexes: tuple, channel: int, output: bool):
        found = self._analog(indexes, channel, output)
        if found is None:
            names = "/".join(f"0x{i:04X}" for i in indexes)
            raise ObjectNotMappedError(
                f"GenericIO '{self._slave_label}': analog {'output' if output else 'input'} "
                f"{channel} ({names}:{channel:02X}) is not in this slave's PDO map")
        return found

    def analog_input(self, channel: int) -> int:
        """Analog input <channel> (1-based subindex of 0x6401/0x6402), raw."""
        return self._read_field(*self._analog_entry(ANALOG_IN, channel, False))

    def analog_output(self, channel: int) -> int:
        """Analog output <channel> (0x6411/0x6412), raw, as written."""
        return self._read_field(*self._analog_entry(ANALOG_OUT, channel, True))

    def set_analog_output(self, channel: int, value: int) -> None:
        """Set analog output <channel> (0x6411/0x6412), raw."""
        self._write_field(self._analog_entry(ANALOG_OUT, channel, True)[0], int(value))

    def analog_input_variable(self, channel: int) -> Signal:
        """Analog input <channel> as a variable, for odycat.Events."""
        entry, _ = self._analog_entry(ANALOG_IN, channel, False)
        spec = SignalSpec(int(entry["index"], 16), channel, None, None)
        spec.name = f"analog_input_{channel}"
        return Signal(self, spec)
