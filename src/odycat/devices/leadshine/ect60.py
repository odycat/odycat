# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Leadshine ECT60 — EtherCAT closed-loop (hybrid) stepper drive, CiA 402.

Identity: vendor 0x00000A88, product 0x0A880002.
Supported modes (0x6502 = 0x1AD): PP, PV, PT, HM, CSP, CSV. No torque
modes: the torque API of Servo raises ObjectNotMappedError unless 0x6071/0x6077 are
mapped, which this drive does not offer.

The default PDO map only carries controlword/mode/target position, so the
config remaps both PDOs to add velocity, digital I/O and the error code:

  RxPDO 0x1600 (to drive, SHM "rxpdo"):
    0x6040 controlword  u16 | 0x6060 mode       i8  | 0x607A target pos i32
    0x60FF target vel   i32 | 0x60FE:01 outputs u32
  TxPDO 0x1A00 (from drive, SHM "txpdo"):
    0x6041 statusword   u16 | 0x6061 mode disp. i8  | 0x6064 actual pos i32
    0x606C actual vel   i32 | 0x60FD inputs     u32 | 0x603F error code u16

Objects are found by index, so the entry order in the config is free.
The drive runs in free-run unless the config asks for DC with
"dc_sync": <Sync0 period in ns> (0 = the bus cycle); CSP/CSV need it, and
the engine should then run with real-time scheduling.

Usage::

    drv = ECT60(alias=60)
    drv.set_mode("PP")
    drv.enable()
    drv.move_to(10000)          # one revolution at 10000 steps/rev
"""

from __future__ import annotations

from odycat.devices.cia402.servo import Servo, DIGITAL_INPUTS, DIGITAL_OUTPUTS
from odycat.signal import signal


class ECT60(Servo):
    """Leadshine ECT60 single-axis stepper drive."""

    # DC settings used when the config turns DC on ("dc_sync": <Sync0 ns>)
    SUPPORT = "tested"
    TESTED_REVISIONS = (0x00000202,)

    DC_SYNC = {"assign_activate": "0x0300"}

    PROFILE = {
        "vendor_id": "0x00000A88",
        "product_code": "0x0A880002",
        "rxpdo": {"index": "0x1600", "entries": [
            {"index": "0x6040", "subindex": 0, "type": "u16", "label": "controlword"},
            {"index": "0x6060", "subindex": 0, "type": "i8", "label": "modes_of_operation"},
            {"index": "0x607A", "subindex": 0, "type": "i32", "label": "target_position"},
            {"index": "0x60FF", "subindex": 0, "type": "i32", "label": "target_velocity"},
            {"index": "0x60FE", "subindex": 1, "type": "u32", "label": "digital_outputs"},
        ]},
        "txpdo": {"index": "0x1A00", "entries": [
            {"index": "0x6041", "subindex": 0, "type": "u16", "label": "statusword"},
            {"index": "0x6061", "subindex": 0, "type": "i8", "label": "mode_display"},
            {"index": "0x6064", "subindex": 0, "type": "i32", "label": "position_actual_value"},
            {"index": "0x606C", "subindex": 0, "type": "i32", "label": "velocity_actual_value"},
            {"index": "0x60FD", "subindex": 0, "type": "u32", "label": "digital_inputs"},
            {"index": "0x603F", "subindex": 0, "type": "u16", "label": "error_code"},
        ]},
    }

    # Inputs: 0x60FD (digital_inputs() is the whole word)

    @signal(DIGITAL_INPUTS, bit=0)
    def negative_limit_switch(self) -> bool:
        """0x60FD bit 0: negative limit switch."""
        ...

    @signal(DIGITAL_INPUTS, bit=1)
    def positive_limit_switch(self) -> bool:
        """0x60FD bit 1: positive limit switch."""
        ...

    @signal(DIGITAL_INPUTS, bit=2)
    def home_switch(self) -> bool:
        """0x60FD bit 2: home switch."""
        ...

    # The I/O connector: OUT2, OUT1, COM+, COM-, IN3, IN4, IN5, IN6, named as
    # printed. Inputs are 0x60FD bits 15+n; outputs 0x60FE:01 bits 15+n, which
    # only take effect if the matching bit is set in the 0x60FE:02 bit mask
    # and the output function (0x2005) allows it.

    @staticmethod
    def _number(point: str | int, prefix: str, valid: tuple) -> int:
        """The n of IN<n>/OUT<n>: from "IN3" (any case) or 3."""
        if isinstance(point, str):
            name = point.strip().upper()
            point = int(name[len(prefix):]) if (
                name.startswith(prefix) and name[len(prefix):].isdigit()) else -1
        if point not in valid:
            raise ValueError(f"ECT60: the connector has "
                             f"{', '.join(f'{prefix}{v}' for v in valid)}, not {point!r}")
        return point

    def input(self, point: str | int) -> bool:
        """Input IN3..IN6: input("IN3") or input(3)."""
        n = self._number(point, "IN", (3, 4, 5, 6))
        return bool((self.digital_inputs() >> (15 + n)) & 1)

    def output(self, point: str | int) -> bool:
        """Output OUT1/OUT2, as written: output("OUT1") or output(1)."""
        n = self._number(point, "OUT", (1, 2))
        return bool((self.read_object(DIGITAL_OUTPUTS, 1) >> (15 + n)) & 1)

    def set_output(self, point: str | int, state: bool) -> None:
        """Set output OUT1/OUT2: set_output("OUT1", True)."""
        n = self._number(point, "OUT", (1, 2))
        self.write_object_bit(DIGITAL_OUTPUTS, 15 + n, state, 1)

    @signal(DIGITAL_INPUTS, bit=18)
    def in3(self) -> bool:
        """Input IN3 (0x60FD bit 18)"""
        ...

    @signal(DIGITAL_INPUTS, bit=19)
    def in4(self) -> bool:
        """Input IN4 (0x60FD bit 19)"""
        ...

    @signal(DIGITAL_INPUTS, bit=20)
    def in5(self) -> bool:
        """Input IN5 (0x60FD bit 20)"""
        ...

    @signal(DIGITAL_INPUTS, bit=21)
    def in6(self) -> bool:
        """Input IN6 (0x60FD bit 21)"""
        ...

    @signal(DIGITAL_OUTPUTS, 1, bit=16)
    def out1(self) -> bool:
        """Output OUT1, as written (0x60FE:01 bit 16)"""
        ...

    def set_out1(self, state: bool) -> None:
        """Set output OUT1"""
        self.set_output(1, state)

    @signal(DIGITAL_OUTPUTS, 1, bit=17)
    def out2(self) -> bool:
        """Output OUT2, as written (0x60FE:01 bit 17)"""
        ...

    def set_out2(self, state: bool) -> None:
        """Set output OUT2"""
        self.set_output(2, state)
