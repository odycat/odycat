# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""
`odycat-config prepare`: the one-off change some devices need before IgH can
use them, for example completing an SII (the slave's EEPROM description)
that lacks its sync manager and PDO sections.

A device class that needs one defines a classmethod

    _prepare(cls, sii: bytes) -> Plan

which looks at the slave's current SII and returns what would change. The
command reads the SII through the IgH `ethercat` tool, keeps a backup, and
writes only with --write, and only while no application (the engine) holds
the master. --restore writes a backup back. Runs as a normal user in the
odycat group: the udev rule gives it /dev/EtherCAT<master>.
"""

from __future__ import annotations

import datetime
import os
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SII_HEADER_BYTES = 128


@dataclass
class Plan:
    """What preparing one slave changes. `sii` is the new SII image, or None
    when the slave needs nothing."""
    sii: Optional[bytes]
    summary: list = field(default_factory=list)


def identity(sii: bytes) -> tuple:
    """(vendor id, product code, revision) from an SII header."""
    if len(sii) < 0x1C:
        raise RuntimeError(f"the SII read is {len(sii)} bytes, too short for a header")
    return struct.unpack_from("<III", sii, 0x10)


def eeprom_bytes(sii: bytes) -> int:
    """The EEPROM size the SII header declares (word 0x3E: KiBit - 1)."""
    return (struct.unpack_from("<H", sii, 0x7C)[0] + 1) * 128


class Slave:
    """One slave, by ring position, through the IgH `ethercat` tool."""

    def __init__(self, position: int, tool: str = "ethercat", master: int = 0):
        self.position, self.tool, self.master = position, tool, master

    def _run(self, args: list, data: bool = True) -> bytes:
        cmd = [self.tool, *args, "-m", str(self.master)]
        try:
            r = subprocess.run(cmd, capture_output=True, env=dict(os.environ, LC_ALL="C"))
        except FileNotFoundError:
            raise RuntimeError(f"'{self.tool}' not found: install the IgH EtherCAT master "
                               f"userland, or pass --ethercat PATH") from None
        if r.returncode:
            msg = r.stderr.decode(errors="replace").strip()
            hint = (f" Your user needs access to /dev/EtherCAT{self.master}: add it to "
                    f"the odycat group (see docs/getting-started.md)."
                    if "ermission" in msg else "")
            raise RuntimeError(f"`{' '.join(cmd)}` failed: {msg}.{hint}")
        return r.stdout

    def master_busy(self) -> bool:
        """An application (the engine, or another program) holds the master."""
        text = self._run(["master"]).decode(errors="replace")
        return any(line.split(":", 1)[1].strip() != "Idle"
                   for line in text.splitlines() if line.strip().startswith("Phase:"))

    def read_sii(self) -> bytes:
        return self._run(["sii_read", "-p", str(self.position)])

    def rescan(self) -> None:
        self._run(["rescan"])

    def write_sii(self, image: bytes) -> None:
        with tempfile.NamedTemporaryFile(suffix=".bin") as f:
            f.write(image)
            f.flush()
            self._run(["sii_write", "-p", str(self.position), f.name])


def find_class(vendor: int, product: int):
    """The device class of a slave identity, or None."""
    from odycat._scan import profiles_by_identity
    from odycat._profiles import find_device
    names = profiles_by_identity().get((vendor, product), [])
    return find_device(names[0]) if names else None


def _check_writable(slave: Slave) -> None:
    if slave.master_busy():
        raise RuntimeError(f"master {slave.master} is in use (the engine, or another "
                           f"program): stop it before writing a slave's EEPROM")


def _write_and_verify(slave: Slave, image: bytes, timeout_s: float = 15.0) -> None:
    """Write, then make IgH read the slave again: after a write IgH keeps
    serving its old copy of the SII until it rescans the bus."""
    _check_writable(slave)
    slave.write_sii(image)
    slave.rescan()
    deadline = time.monotonic() + timeout_s
    back = b""
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            back = slave.read_sii()
        except RuntimeError:              # the slave is being scanned again
            continue
        if back[:len(image)] == image:
            return
    raise RuntimeError(f"slave {slave.position}: the SII read back after the rescan "
                       f"differs from the image written ({len(back)} B read); check "
                       f"`ethercat slaves`, and restore the backup with --restore")


def restore(slave: Slave, path: Path) -> int:
    image = Path(path).read_bytes()
    current = slave.read_sii()
    if identity(image)[:2] != identity(current)[:2]:
        raise RuntimeError(f"{path} is the SII of another device "
                           f"(0x{identity(image)[0]:08X}:0x{identity(image)[1]:08X}); "
                           f"slave {slave.position} is 0x{identity(current)[0]:08X}:"
                           f"0x{identity(current)[1]:08X}")
    _write_and_verify(slave, image)
    print(f"Slave {slave.position}: {path} written back, the bus rescanned and the SII "
          f"read back. Check with: ethercat pdos -p {slave.position}")
    return 0


def prepare(slave: Slave, backup_dir: Path, write: bool) -> int:
    current = slave.read_sii()
    vendor, product, revision = identity(current)
    cls = find_class(vendor, product)
    what = f"slave {slave.position} (0x{vendor:08X}:0x{product:08X}"
    what += f", {cls.__module__.split('.')[-2]}.{cls.__name__})" if cls else ")"
    prep = getattr(cls, "_prepare", None) if cls else None
    if prep is None:
        print(f"{what}: OdyCAT knows no preparation for this device; nothing to do.")
        return 0
    plan = prep(current)
    if plan.sii is None:
        print(f"{what}: already prepared; nothing to do.")
        for line in plan.summary:
            print(f"  {line}")
        return 0
    if len(plan.sii) > eeprom_bytes(current):
        raise RuntimeError(f"{what}: the new SII ({len(plan.sii)} B) does not fit the "
                           f"EEPROM ({eeprom_bytes(current)} B)")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{cls.__name__}_pos{slave.position}_{stamp}"
    backup = backup_dir / f"{name}_orig.bin"
    new = backup_dir / f"{name}_new.bin"
    backup.write_bytes(current)
    new.write_bytes(plan.sii)
    print(f"{what}: needs its SII completed once.")
    for line in plan.summary:
        print(f"  {line}")
    print(f"  backup : {backup}")
    print(f"  new SII: {new} ({len(plan.sii)} B)")
    if not write:
        print("Nothing written. Run again with --write to program the EEPROM "
              "(the engine must be stopped).")
        return 0
    _write_and_verify(slave, plan.sii)
    print(f"Written, the bus rescanned and the SII read back. Check with: "
          f"ethercat pdos -p {slave.position}. To undo: --restore {backup}")
    return 0
