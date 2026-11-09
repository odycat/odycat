# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""odycat-config prepare, against a fake IgH `ethercat` tool that keeps one
slave's SII in a file; and the Delta AX-516 SII it writes."""

import contextlib
import io
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401  (the repo, or the installed package)

from odycat import _scan  # noqa: E402
from odycat.devices.delta import sii  # noqa: E402

AM, AN = 0x10512081, 0x10512181

FAKE_TOOL = """#!{python}
import os, sys
args = sys.argv[1:]
state = os.environ["FAKE_SII_DIR"]
if args[0] == "master":
    print("Master0\\n  Phase: " + open(os.path.join(state, "phase")).read())
elif args[0] == "sii_read":
    sys.stdout.buffer.write(open(os.path.join(state, "sii.bin"), "rb").read())
elif args[0] == "sii_write":                # IgH keeps serving its old copy...
    data = open(args[3], "rb").read()
    open(os.path.join(state, "eeprom.bin"), "wb").write(data)
elif args[0] == "rescan":                   # ...until the bus is scanned again
    eeprom = os.path.join(state, "eeprom.bin")
    if os.path.exists(eeprom):
        os.replace(eeprom, os.path.join(state, "sii.bin"))
else:
    sys.exit("unexpected " + " ".join(args))
"""


def header(vendor=0x1DD, product=AM, alias=2) -> bytes:
    h = bytearray(128)
    struct.pack_into("<H", h, 8, alias)
    struct.pack_into("<III", h, 0x10, vendor, product, 0x01000000)
    struct.pack_into("<H", h, 0x7C, 7)            # 1 KiB EEPROM
    return bytes(h)


class DeltaSiiTests(unittest.TestCase):
    def test_header_only_module_gets_its_sections(self):
        for product, pdo_cat in ((AM, sii.CAT_TXPDO), (AN, sii.CAT_RXPDO)):
            with self.subTest(product=hex(product)):
                orig = header(product=product) + b"\xff\xff"
                plan = sii.plan(orig)
                self.assertIsNotNone(plan.sii)
                self.assertEqual(plan.sii[:128], orig[:128])          # header kept
                types = [t for t, _ in sii.categories(plan.sii)]
                self.assertEqual(types, [sii.CAT_STRINGS, sii.CAT_GENERAL, sii.CAT_FMMU,
                                         sii.CAT_SYNCM, pdo_cat])
                self.assertEqual(plan.sii[-2:], b"\xff\xff")          # end marker
                self.assertTrue(any("station alias 2" in s for s in plan.summary))

    def test_prepared_module_needs_nothing(self):
        self.assertIsNone(sii.plan(sii.build_image(header(), AM)).sii)


class PrepareCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        tool = self.dir / "ethercat"
        tool.write_text(FAKE_TOOL.format(python=sys.executable))
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
        self.tool = str(tool)
        self.set_sii(header() + b"\xff\xff")
        self.set_phase("Idle")
        os.environ["FAKE_SII_DIR"] = str(self.dir)

    def tearDown(self):
        os.environ.pop("FAKE_SII_DIR", None)
        self.tmp.cleanup()

    def set_sii(self, data: bytes):
        (self.dir / "sii.bin").write_bytes(data)

    def set_phase(self, phase: str):
        (self.dir / "phase").write_text(phase)

    def sii(self) -> bytes:
        return (self.dir / "sii.bin").read_bytes()

    def run_cmd(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            _scan.main(["prepare", "--position", "4", "--ethercat", self.tool,
                        "--backup-dir", str(self.dir / "backup"), *extra])
        return cm.exception.code, out.getvalue()

    def test_dry_run_writes_nothing_and_keeps_a_backup(self):
        before = self.sii()
        code, out = self.run_cmd()
        self.assertEqual(code, 0)
        self.assertIn("Nothing written", out)
        self.assertEqual(self.sii(), before)
        backups = sorted(p.name for p in (self.dir / "backup").iterdir())
        self.assertEqual(len(backups), 2)
        self.assertTrue(backups[0].startswith("AX516AM_pos4_"))
        self.assertEqual((self.dir / "backup" / [b for b in backups if b.endswith("_orig.bin")][0])
                         .read_bytes(), before)

    def test_write_programs_and_reads_back(self):
        code, out = self.run_cmd("--write")
        self.assertEqual(code, 0)
        self.assertIn("read back", out)
        self.assertEqual(self.sii(), sii.build_image(header(), AM))
        code, out = self.run_cmd("--write")                     # a second run: nothing to do
        self.assertIn("already prepared", out)

    def test_no_write_while_the_master_is_in_use(self):
        self.set_phase("Operation")
        code, _ = self.run_cmd("--write")
        self.assertIn("in use", str(code))
        self.assertEqual(self.sii(), header() + b"\xff\xff")

    def test_restore_checks_the_device(self):
        self.run_cmd("--write")
        orig = next((self.dir / "backup").glob("*_orig.bin"))
        code, out = self.run_cmd("--restore", str(orig))
        self.assertEqual(code, 0)
        self.assertEqual(self.sii(), header() + b"\xff\xff")
        other = self.dir / "other.bin"
        other.write_bytes(header(vendor=0xB95, product=0x1101))
        code, _ = self.run_cmd("--restore", str(other))
        self.assertIn("another device", str(code))

    def test_device_without_preparation(self):
        self.set_sii(header(vendor=0xB95, product=0x1101))       # EC3A-IO1632
        code, out = self.run_cmd("--write")
        self.assertEqual(code, 0)
        self.assertIn("knows no preparation", out)


if __name__ == "__main__":
    unittest.main()
