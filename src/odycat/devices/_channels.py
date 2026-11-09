# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/_channels.py
"""
Digital channels over a slave's mapped entries, for the standard-profile
I/O classes (mdp.DigitalIO, cia401.GenericIO). Channel n is the n-th bit
in object order: a 1-bit entry is one channel, an 8/16/32-bit entry is as
many channels, least significant bit first.
"""

from __future__ import annotations

from typing import Iterable

from odycat.signal import Signal, SignalSpec

DIGITAL_TYPES = {"bool", "u8", "u16", "u32", "u64"}   # unsigned (or bool) entries count as channels


def is_digital(entry: dict) -> bool:
    typ = entry.get("type")
    return not typ or typ in DIGITAL_TYPES


class Channels:
    """The digital channels of one direction of a Device."""

    def __init__(self, device, entries: list, is_output: bool, kind: str):
        self.device = device
        self.is_output = is_output
        self.kind = kind                    # "input" or "output", for names and errors
        self.entries = entries              # in channel order
        self.map = []                       # channel -> (entry, bit within it or None)
        for e in entries:
            if e["bits"] == 1:
                self.map.append((e, None))
            else:
                self.map += [(e, b) for b in range(e["bits"])]

    def __len__(self) -> int:
        return len(self.map)

    def _at(self, n: int):
        if not 0 <= n < len(self.map):
            raise ValueError(f"{type(self.device).__name__} '{self.device._slave_label}': "
                             f"{self.kind} {n} out of range 0..{len(self.map) - 1}")
        return self.map[n]

    def read(self, n: int) -> bool:
        entry, bit = self._at(n)
        v = self.device._read_field(entry, self.is_output)
        return bool(v) if bit is None else bool((int(v) >> bit) & 1)

    def write(self, n: int, state: bool) -> None:
        entry, bit = self._at(n)
        self.device._write_field_bit(entry, bit or 0, state)

    def word(self) -> int:
        """All channels as an integer, bit n = channel n."""
        out, n = 0, 0
        for e in self.entries:
            v = int(self.device._read_field(e, self.is_output))
            out |= (v & ((1 << e["bits"]) - 1)) << n
            n += e["bits"]
        return out

    def set_word(self, states: Iterable[bool] | int) -> None:
        """Set every channel from an integer (bit n = channel n) or bools."""
        if not isinstance(states, int):
            states = list(states)
            if len(states) != len(self.map):
                raise ValueError(f"expected {len(self.map)} states, got {len(states)}")
            states = sum(1 << n for n, on in enumerate(states) if on)
        n = 0
        for e in self.entries:
            self.device._write_field(e, (states >> n) & ((1 << e["bits"]) - 1))
            n += e["bits"]

    def variable(self, n: int) -> Signal:
        """The channel as a variable, for odycat.Events (ev.rising(...))."""
        entry, bit = self._at(n)
        spec = SignalSpec(int(entry["index"], 16), entry["subindex"], bit, None)
        spec.name = f"{self.kind}_{n}"
        return Signal(self.device, spec)
