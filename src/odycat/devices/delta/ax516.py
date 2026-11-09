# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Delta AX-5 remote I/O digital modules behind the AX-500CEC00 coupler.

  AX-516AM20-0A — 16 digital inputs   (vendor 0x01DD, product 0x10512081)
  AX-516AN02-0A — 16 digital outputs  (vendor 0x01DD, product 0x10512181)

Each module is its own EtherCAT slave and maps one 1-bit entry per point,
split over two PDOs of 8 (AM: TxPDO 0x1A00 -> 0x6000:01..08, 0x1A01 ->
0x6001:01..08; AN: RxPDO 0x1600/0x1601 -> 0x7000/0x7001).

The terminals come in two columns, L0..L7 and R0..R7, and points are named
as printed. They alternate in the process data: bit 2n = L<n>, bit 2n+1 =
R<n> (checked on the modules). The engine packs the bits into 2 shm bytes,
read here as one little-endian word:

    di.l3()                 # L3 now (or di.input("L3")); di.inputs() = the word
    do.set_r0(True)         # (or do.set_output("R0", True))

Without (), di.l3 is the variable itself, for odycat.Events.

Before IgH can exchange their process data, the modules need their SII
completed once: `odycat-config prepare --position <ring position>` (see
odycat/devices/delta/sii.py).
"""

from __future__ import annotations

from typing import Iterable, Optional

from odycat.device import Device
from odycat.devices.delta import sii as _sii
from odycat.signal import signal
from odycat._config import slave_config, slave_pdo_sizes

POINTS = [("l" if bit % 2 == 0 else "r") + str(bit // 2) for bit in range(16)]
"""The printed name of each bit, in bit order: l0, r0, l1, r1, ... r7."""


def _point_pdos(pdo_base: int, obj_base: int) -> list:
    """Two PDOs of 8 one-bit points each, labelled with the printed names."""
    return [{"index": f"0x{pdo_base + p:04X}", "entries": [
                {"index": f"0x{obj_base + p:04X}", "subindex": s + 1, "bits": 1,
                 "label": POINTS[8 * p + s]} for s in range(8)]}
            for p in range(2)]


def _bit(model: str, point: str) -> int:
    """The bit of a point, from its printed name ("L3", any case)."""
    try:
        return POINTS.index(str(point).strip().lower())
    except ValueError:
        raise ValueError(f"{model}: {point!r} is not one of L0..L7, R0..R7") from None


def _check_pdos_configured(position: Optional[int], alias: int, label: Optional[str],
                           bus: Optional[str], direction: int,
                           model: str) -> None:
    sc = slave_config(position, bus, alias=alias, label=label)
    size = slave_pdo_sizes(sc)[direction]
    if size != 2:
        raise RuntimeError(
            f"{model} '{sc.get('label')}': config maps {size} bytes, expected 2. "
            f"The module needs its SII completed once: odycat-config prepare "
            f"--position <its ring position in `ethercat slaves`>.")


def _mask_from(states: Iterable[bool] | int) -> int:
    if isinstance(states, int):
        return states & 0xFFFF
    states = list(states)
    if len(states) != 16:
        raise ValueError(f"expected 16 states, got {len(states)}")
    return sum(1 << bit for bit, on in enumerate(states) if on)


class AX516AM(Device):
    """Delta AX-516AM20-0A — 16 digital inputs, L0..L7 and R0..R7."""

    SUPPORT = "tested"
    TESTED_REVISIONS = (0x01000000,)

    @classmethod
    def _prepare(cls, sii: bytes):
        """odycat-config prepare: complete the SII (odycat/devices/delta/sii.py)."""
        return _sii.plan(sii)

    PROFILE = {
        "vendor_id": "0x000001DD",
        "product_code": "0x10512081",
        "rxpdo": {"entries": []},
        "txpdo": _point_pdos(0x1A00, 0x6000),
    }

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None):
        _check_pdos_configured(position, alias, label, bus,
                               0, "AX516AM")
        super().__init__(position, alias=alias, label=label,
                         bus=bus,
                         txpdo_fmt="=H", rxpdo_fmt="")

    def input(self, point: str) -> bool:
        """Input by its printed name: input("L3")."""
        return self.txpdo_bit(0, _bit("AX516AM", point))

    def inputs(self) -> int:
        """All 16 inputs as a word: bit 2n = L<n>, bit 2n+1 = R<n>."""
        return self.read_txpdo()[0]

    # ------------------------------------------------------------
    # Each point by name: l3() is the value, l3 the variable
    # ------------------------------------------------------------

    @signal(0x6000, 1)
    def l0(self) -> bool:
        """Input L0 (bit 0)"""
        ...

    @signal(0x6000, 3)
    def l1(self) -> bool:
        """Input L1 (bit 2)"""
        ...

    @signal(0x6000, 5)
    def l2(self) -> bool:
        """Input L2 (bit 4)"""
        ...

    @signal(0x6000, 7)
    def l3(self) -> bool:
        """Input L3 (bit 6)"""
        ...

    @signal(0x6001, 1)
    def l4(self) -> bool:
        """Input L4 (bit 8)"""
        ...

    @signal(0x6001, 3)
    def l5(self) -> bool:
        """Input L5 (bit 10)"""
        ...

    @signal(0x6001, 5)
    def l6(self) -> bool:
        """Input L6 (bit 12)"""
        ...

    @signal(0x6001, 7)
    def l7(self) -> bool:
        """Input L7 (bit 14)"""
        ...

    @signal(0x6000, 2)
    def r0(self) -> bool:
        """Input R0 (bit 1)"""
        ...

    @signal(0x6000, 4)
    def r1(self) -> bool:
        """Input R1 (bit 3)"""
        ...

    @signal(0x6000, 6)
    def r2(self) -> bool:
        """Input R2 (bit 5)"""
        ...

    @signal(0x6000, 8)
    def r3(self) -> bool:
        """Input R3 (bit 7)"""
        ...

    @signal(0x6001, 2)
    def r4(self) -> bool:
        """Input R4 (bit 9)"""
        ...

    @signal(0x6001, 4)
    def r5(self) -> bool:
        """Input R5 (bit 11)"""
        ...

    @signal(0x6001, 6)
    def r6(self) -> bool:
        """Input R6 (bit 13)"""
        ...

    @signal(0x6001, 8)
    def r7(self) -> bool:
        """Input R7 (bit 15)"""
        ...


class AX516AN(Device):
    """Delta AX-516AN02-0A — 16 digital outputs, L0..L7 and R0..R7."""

    SUPPORT = "tested"
    TESTED_REVISIONS = (0x01000000,)

    @classmethod
    def _prepare(cls, sii: bytes):
        """odycat-config prepare: complete the SII (odycat/devices/delta/sii.py)."""
        return _sii.plan(sii)

    PROFILE = {
        "vendor_id": "0x000001DD",
        "product_code": "0x10512181",
        "rxpdo": _point_pdos(0x1600, 0x7000),
        "txpdo": {"entries": []},
    }

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None):
        _check_pdos_configured(position, alias, label, bus,
                               1, "AX516AN")
        super().__init__(position, alias=alias, label=label,
                         bus=bus,
                         txpdo_fmt="", rxpdo_fmt="=H")

    def output(self, point: str) -> bool:
        """Output by its printed name, as written: output("R0")."""
        return self.rxpdo_bit(0, _bit("AX516AN", point))

    def set_output(self, point: str, state: bool) -> None:
        """Set an output by its printed name: set_output("R0", True); no
        other output changes."""
        self.set_rxpdo_bit(0, _bit("AX516AN", point), state)

    def outputs(self) -> int:
        """All 16 outputs as written: bit 2n = L<n>, bit 2n+1 = R<n>."""
        return self.read_rxpdo()[0]

    def set_outputs(self, states: Iterable[bool] | int) -> None:
        """Set all 16 outputs from a word (bit 2n = L<n>, bit 2n+1 = R<n>)
        or 16 bools in bit order."""
        self.write_rxpdo(_mask_from(states))

    # ------------------------------------------------------------
    # Each point by name: r0() is the value, r0 the variable
    # ------------------------------------------------------------

    @signal(0x7000, 1)
    def l0(self) -> bool:
        """Output L0 (bit 0), as written"""
        ...

    def set_l0(self, state: bool) -> None:
        """Set output L0; no other output changes"""
        self.set_output("L0", state)

    @signal(0x7000, 3)
    def l1(self) -> bool:
        """Output L1 (bit 2), as written"""
        ...

    def set_l1(self, state: bool) -> None:
        """Set output L1; no other output changes"""
        self.set_output("L1", state)

    @signal(0x7000, 5)
    def l2(self) -> bool:
        """Output L2 (bit 4), as written"""
        ...

    def set_l2(self, state: bool) -> None:
        """Set output L2; no other output changes"""
        self.set_output("L2", state)

    @signal(0x7000, 7)
    def l3(self) -> bool:
        """Output L3 (bit 6), as written"""
        ...

    def set_l3(self, state: bool) -> None:
        """Set output L3; no other output changes"""
        self.set_output("L3", state)

    @signal(0x7001, 1)
    def l4(self) -> bool:
        """Output L4 (bit 8), as written"""
        ...

    def set_l4(self, state: bool) -> None:
        """Set output L4; no other output changes"""
        self.set_output("L4", state)

    @signal(0x7001, 3)
    def l5(self) -> bool:
        """Output L5 (bit 10), as written"""
        ...

    def set_l5(self, state: bool) -> None:
        """Set output L5; no other output changes"""
        self.set_output("L5", state)

    @signal(0x7001, 5)
    def l6(self) -> bool:
        """Output L6 (bit 12), as written"""
        ...

    def set_l6(self, state: bool) -> None:
        """Set output L6; no other output changes"""
        self.set_output("L6", state)

    @signal(0x7001, 7)
    def l7(self) -> bool:
        """Output L7 (bit 14), as written"""
        ...

    def set_l7(self, state: bool) -> None:
        """Set output L7; no other output changes"""
        self.set_output("L7", state)

    @signal(0x7000, 2)
    def r0(self) -> bool:
        """Output R0 (bit 1), as written"""
        ...

    def set_r0(self, state: bool) -> None:
        """Set output R0; no other output changes"""
        self.set_output("R0", state)

    @signal(0x7000, 4)
    def r1(self) -> bool:
        """Output R1 (bit 3), as written"""
        ...

    def set_r1(self, state: bool) -> None:
        """Set output R1; no other output changes"""
        self.set_output("R1", state)

    @signal(0x7000, 6)
    def r2(self) -> bool:
        """Output R2 (bit 5), as written"""
        ...

    def set_r2(self, state: bool) -> None:
        """Set output R2; no other output changes"""
        self.set_output("R2", state)

    @signal(0x7000, 8)
    def r3(self) -> bool:
        """Output R3 (bit 7), as written"""
        ...

    def set_r3(self, state: bool) -> None:
        """Set output R3; no other output changes"""
        self.set_output("R3", state)

    @signal(0x7001, 2)
    def r4(self) -> bool:
        """Output R4 (bit 9), as written"""
        ...

    def set_r4(self, state: bool) -> None:
        """Set output R4; no other output changes"""
        self.set_output("R4", state)

    @signal(0x7001, 4)
    def r5(self) -> bool:
        """Output R5 (bit 11), as written"""
        ...

    def set_r5(self, state: bool) -> None:
        """Set output R5; no other output changes"""
        self.set_output("R5", state)

    @signal(0x7001, 6)
    def r6(self) -> bool:
        """Output R6 (bit 13), as written"""
        ...

    def set_r6(self, state: bool) -> None:
        """Set output R6; no other output changes"""
        self.set_output("R6", state)

    @signal(0x7001, 8)
    def r7(self) -> bool:
        """Output R7 (bit 15), as written"""
        ...

    def set_r7(self, state: bool) -> None:
        """Set output R7; no other output changes"""
        self.set_output("R7", state)
