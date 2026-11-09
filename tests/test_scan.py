# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_scan.py
"""
odycat-config scan and the EtherCATInfo parser, against `ethercat` output
recorded from the demo box (tests/data/demo_box): ECT60 drive, CATIO-A,
EC3A-IO1632 and a Delta AX-5 coupler with a DI and a DO module.
"""

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401  (puts the repo on sys.path)

from odycat import _scan, _esi as esi              # noqa: E402
from odycat._config import slave_entry_layout       # noqa: E402
from odycat._profiles import expand_config, expand_slave, uses_profiles   # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "demo_box"


def recorded(args: list) -> str:
    """The recorded output for an `ethercat` command line."""
    if args == ["slaves"]:
        name = "slaves.txt"
    elif args == ["master"]:
        name = "master.txt"
    elif args[:2] == ["slaves", "-v"]:
        name = f"slaves_v_{args[3]}.txt"
    elif args[0] == "upload":
        raise RuntimeError("`ethercat upload` failed: not recorded")
    elif args[0] in ("xml", "sdos"):
        name = f"{args[0]}_{args[2]}.txt"
    else:
        raise AssertionError(f"unexpected command {args}")
    path = DATA / name
    if not path.exists():
        raise RuntimeError(f"`ethercat {' '.join(args)}` failed: no such slave")
    return path.read_text(encoding="utf-8", errors="replace")


def bus():
    return _scan.read_bus(recorded)


def layout_key(slave: dict) -> list:
    return [[(f["index"].upper(), f["subindex"], f["bits"], f["bit_offset"], f["type"])
             for f in side] for side in slave_entry_layout(slave)]




class EsiTests(unittest.TestCase):
    def test_bus_document(self):
        dev, = esi.parse(recorded(["xml", "-p", "4"]), types_trusted=False)
        self.assertFalse(dev.types_trusted)
        self.assertEqual((dev.vendor_id, dev.product_code), (0x1DD, 0x10512081))
        self.assertEqual([p.index for p in dev.assigned("tx", sm=3)], [0x1A00, 0x1A01])
        self.assertEqual(sum(len(p.entries) for p in dev.pdos), 16)
        coupler, = esi.parse(recorded(["xml", "-p", "3"]), types_trusted=False)
        self.assertEqual([(p.index, p.sm) for p in coupler.pdos], [(0x1A00, 0)])

    def test_eeprom_strings_that_are_not_xml(self):
        # `ethercat xml` copies the slave's EEPROM strings unescaped: control
        # bytes, "&" and "<" in names must not stop the scan
        doc = recorded(["xml", "-p", "4"])
        names = re.findall(r"<Name>((?!<!\[CDATA\[)[^<]*)</Name>", doc)
        bad = ["\x05", "In & Out", "A<B", "\x07Analog\x01Input"]
        broken = doc
        for name, new in zip(names, bad):
            broken = broken.replace(f"<Name>{name}</Name>", f"<Name>{new}</Name>", 1)
        broken = broken.replace("<![CDATA[", "<![CDATA[\x06", 1)
        with self.assertRaises(Exception):
            esi.parse(broken, types_trusted=False)
        dev, = esi.parse(esi.igh_document(broken), types_trusted=False)
        good, = esi.parse(esi.igh_document(doc), types_trusted=False)
        got = [dev.name] + [p.name for p in dev.pdos] + [e.name for p in dev.pdos
                                                          for e in p.entries]
        self.assertIn("In & Out", got)
        self.assertIn("A<B", got)
        self.assertIn("\ufffdAnalog\ufffdInput", got)
        self.assertTrue(dev.name.startswith("\ufffd"))
        self.assertEqual([good], esi.parse(doc, types_trusted=False))   # clean: unchanged
        self.assertEqual([(p.index, p.sm, [e[:3] for e in p.entries]) for p in dev.pdos],
                         [(p.index, p.sm, [e[:3] for e in p.entries]) for p in good.pdos])
        self.assertEqual(_scan._snake("\ufffd"), "")    # label falls back to obj_iiii_ss

    def test_sync_types(self):
        def run(args):
            self.assertEqual(args, ["upload", "-p", "6", "0x1C32", "4", "--type", "uint16"])
            return "0x0004 4\n"
        self.assertEqual(_scan.read_sync_types(run, "6"), 4)

        def fails(args):
            raise RuntimeError("SDO transfer aborted")
        self.assertIsNone(_scan.read_sync_types(fails, "6"))

    def test_not_ethercatinfo(self):
        with self.assertRaises(ValueError):
            esi.parse("<Other/>")

    def test_entry_type(self):
        enc = esi.entry_type
        self.assertEqual(enc("DINT", 32), ({"type": "i32"}, False))
        self.assertEqual(enc("uint16", 16), ({"type": "u16"}, False))
        self.assertEqual(enc("REAL", 32), ({"type": "f32"}, False))
        self.assertEqual(enc("bool", 1), ({"bits": 1}, False))
        self.assertEqual(enc("BOOL", 8), ({"type": "bool"}, False))
        self.assertEqual(enc("type 002e", 16), ({"type": "u16"}, True))   # unknown: guessed
        self.assertEqual(enc("", 4), ({"bits": 4}, False))
        self.assertEqual(enc("DINT", 16), ({"type": "u16"}, True))        # size mismatch


class ParseTests(unittest.TestCase):
    def test_slaves(self):
        rows = _scan.parse_slaves(recorded(["slaves"]))
        self.assertEqual([(r, a, o) for r, a, o, _, _ in rows],
                         [(0, 60, 0), (1, 50, 0), (2, 40, 0), (3, 1, 0), (4, 2, 0), (5, 3, 0)])
        self.assertEqual(rows[0][3:], ("PREOP", "ECT60V202(COE)"))

    def test_verbose_and_sdos(self):
        info = _scan.parse_slave_verbose(recorded(["slaves", "-v", "-p", "0"]))
        self.assertEqual(info, {"dc": True, "protocols": ["CoE", "FoE"]})
        od = _scan.parse_sdos(recorded(["sdos", "-p", "1"]))
        self.assertEqual(od[(0x2010, 0)], ("int16", 16, "Analog Input"))

    def test_master_interface(self):
        mac = _scan.parse_master_mac(recorded(["master"]))
        self.assertEqual(mac, "02:00:00:00:00:01")             # locally administered, not the bench NIC
        with tempfile.TemporaryDirectory() as d:
            for name, addr in (("lo", "00:00:00:00:00:00"), ("enp5s0", mac.upper())):
                Path(d, name).mkdir()
                Path(d, name, "address").write_text(addr + "\n")
            self.assertEqual(_scan.interface_for_mac(mac, d), "enp5s0")
            self.assertIsNone(_scan.interface_for_mac("aa:bb:cc:dd:ee:ff", d))


class BuildTests(unittest.TestCase):
    def test_profiles_are_used(self):
        cfg, report = _scan.build_config(bus(), bus="enp5s0", short=True)
        sl = cfg["slaves"]
        self.assertEqual([s.get("device") for s in sl],
                         ["leadshine.ECT60", None, "amsamotion.EC3A_IO1632", None,
                          "delta.AX516AM", "delta.AX516AN"])
        self.assertEqual([(s.get("alias"), s["position"]) for s in sl],
                         [(60, 0), (50, 0), (40, 0), (1, 0), (2, 0), (3, 0)])
        coupler = sl[3]
        self.assertEqual((coupler["vendor_id"], coupler["product_code"]),
                         ("0x000001dd", "0x10518000"))
        self.assertEqual((coupler["txpdo"], coupler["rxpdo"]), ({"entries": []}, {"entries": []}))
        self.assertTrue(any("SM0" in line for line in report))
        self.assertEqual(len({s["label"] for s in sl}), 6)

    def test_default_writes_the_classes_out(self):
        """The default config needs no expansion (the engine binary takes it)
        and is exactly what `expand` makes of the --short one."""
        full, report = _scan.build_config(bus(), bus="enp5s0")
        short, _ = _scan.build_config(bus(), bus="enp5s0", short=True)
        self.assertFalse(uses_profiles(full))
        expanded = expand_config(short)
        for s in expanded["slaves"]:
            s.pop("device", None)
        self.assertEqual(full, expanded)
        ect60 = full["slaves"][0]
        self.assertEqual(list(ect60)[:6], ["alias", "position", "name", "label",
                                           "vendor_id", "product_code"])
        self.assertNotIn("dc_sync", ect60)                   # free-run unless asked
        self.assertTrue(any("supports DC" in line for line in report))
        io = {f["label"] for d in slave_entry_layout(full["slaves"][2]) for f in d}
        self.assertEqual(io, {"inputs", "outputs"})                 # class names
        self.assertTrue(any("ECT60, written out" in line for line in report))

    def test_report_shows_the_support_tier(self):
        _, report = _scan.build_config(bus(), bus="x", short=True)
        line = next(r for r in report if "leadshine.ECT60" in r)
        self.assertIn("support: tested", line)
        self.assertFalse(any("not the one tested" in r for r in report))

    def test_slave_to_prepare_is_reported(self):
        slaves = bus()
        i = next(n for n, s in enumerate(slaves) if s.device.product_code == 0x10512081)
        _, report = _scan.build_config(slaves, bus="x", short=True)
        self.assertNotIn("prepared once", "\n".join(report))       # prepared on the demo box
        slaves[i] = slaves[i]._replace(device=slaves[i].device._replace(sms=()))
        _, report = _scan.build_config(slaves, bus="x", short=True)
        self.assertIn(f"odycat-config prepare --position {slaves[i].ring}", "\n".join(report))

    def test_untested_revision_is_reported(self):
        slaves = bus()
        ect60 = slaves[0]
        slaves[0] = ect60._replace(device=ect60.device._replace(revision=0x203))
        _, report = _scan.build_config(slaves, bus="x", short=True)
        self.assertIn("revision 0x00000203 is not the one tested: "
                      "tested (revision 0x00000202)", "\n".join(report))

    def test_without_profiles_matches_the_device_classes(self):
        """Written from the bus alone, every slave has the layout, sizes and
        signedness its hand-made device class has."""
        cfg, report = _scan.build_config(bus(), bus="x", use_profiles=False)
        sl = cfg["slaves"]
        for i, dev in ((0, "leadshine.ECT60"), (2, "amsamotion.EC3A_IO1632"),
                       (4, "delta.AX516AM"), (5, "delta.AX516AN")):
            ref = expand_slave({"device": dev, "position": 0, "label": "x"})
            self.assertEqual(layout_key(sl[i]), layout_key(ref), dev)
        ect60 = {f["label"] for d in slave_entry_layout(sl[0]) for f in d}
        self.assertTrue({"statusword", "position_actual_value", "target_velocity"} <= ect60)
        self.assertTrue(any("supports DC" in line for line in report))
        self.assertTrue(any("typed by size only" in line for line in report))   # EC3A

    def test_positions_and_duplicate_aliases(self):
        slaves = bus()
        cfg, _ = _scan.build_config(slaves, bus="x", positions=True)
        self.assertEqual([(s.get("alias", 0), s["position"]) for s in cfg["slaves"]],
                         [(0, r) for r in range(6)])
        dup = [slaves[0], slaves[1]._replace(alias=60, offset=0)]
        cfg, report = _scan.build_config(dup, bus="x")
        self.assertEqual([s["position"] for s in cfg["slaves"]], [0, 1])
        self.assertIn("ring position", report[0])

    def test_cia402_second_axis(self):
        self.assertEqual(_scan._cia402(0x6864, 0), ("position_actual_value_ax2", "i32"))
        self.assertIsNone(_scan._cia402(0x7000, 1))

    def test_format_round_trips(self):
        cfg, _ = _scan.build_config(bus(), bus="x", use_profiles=False)
        text = _scan.format_config(cfg)
        self.assertEqual(json.loads(text), cfg)
        self.assertIn('{ "index": "0x6041", "subindex": 0, "type": "u16", "label": '
                      '"statusword" }', text)


class UpdateTests(unittest.TestCase):
    """odycat-config scan --update: the config brought up to date with the bus."""

    def scanned(self) -> dict:
        return _scan.build_config(bus(), bus="enp5s0")[0]

    def edited(self) -> dict:
        """A scan output after the user worked on it."""
        cfg = self.scanned()
        cfg["history_s"] = 30
        cfg["require_rt"] = True
        drive = cfg["slaves"][0]
        drive["label"] = "axis_x"
        drive["sdos"] = [{"index": "0x6081", "subindex": 0, "type": "u32",
                          "value": 50000, "label": "profile_velocity"}]
        drive["dc_sync"] = 0
        cfg["slaves"][2]["label"] = "panel_io"
        cfg["slaves"][2]["txpdo"]["entries"][0]["label"] = "buttons"
        return cfg

    def update(self, old, **kw):
        return _scan.update_config(json.loads(json.dumps(old)), bus(), **kw)

    def test_unchanged_bus_keeps_every_entry_as_written(self):
        old = self.edited()
        cfg, report = self.update(old)
        self.assertEqual(cfg, old)
        self.assertEqual(report[-1], "6 kept, 0 added, 0 replaced, 0 removed")

    def test_new_slave_is_added_with_a_label_of_its_own(self):
        old = self.edited()
        del old["slaves"][2]                               # EC3A at 40:0 not in the config
        old["slaves"][1]["label"] = "ec3a_io1632"          # its generated label is taken
        cfg, report = self.update(old)
        self.assertEqual(len(cfg["slaves"]), 6)
        added = cfg["slaves"][2]
        self.assertEqual((added["alias"], added["position"]), (40, 0))
        self.assertEqual(added["label"], "ec3a_io1632_2")
        self.assertEqual(cfg["slaves"][0], old["slaves"][0])      # the others kept
        self.assertIn("ADDED", next(r for r in report if "40:0" in r))
        self.assertEqual(report[-1], "5 kept, 1 added, 0 replaced, 0 removed")

    def test_other_device_at_the_same_address_is_replaced(self):
        old = self.edited()
        old["slaves"][2]["product_code"] = "0x00009999"
        cfg, report = self.update(old)
        self.assertEqual(cfg["slaves"][2]["product_code"], "0x00001101")
        self.assertNotEqual(cfg["slaves"][2]["label"], "panel_io")
        line = next(r for r in report if "40:0" in r)
        self.assertIn("REPLACED 'panel_io' at 40:0", line)
        self.assertEqual(report[-1], "5 kept, 0 added, 1 replaced, 0 removed")

    def test_slave_gone_from_the_bus_is_removed(self):
        old = self.edited()
        old["slaves"].append({"alias": 70, "position": 0, "label": "spare",
                              "vendor_id": "0x2", "product_code": "0x3",
                              "txpdo": {"entries": []}, "rxpdo": {"entries": []}})
        cfg, report = self.update(old)
        self.assertNotIn("spare", [e["label"] for e in cfg["slaves"]])
        self.assertIn("[  -] REMOVED 'spare' at 70:0", "\n".join(report))
        self.assertEqual(report[-1], "6 kept, 0 added, 0 replaced, 1 removed")

    def test_device_entries_match_through_their_class(self):
        old = _scan.build_config(bus(), bus="enp5s0", short=True)[0]
        old["slaves"][0]["label"] = "axis_x"
        cfg, report = self.update(old)
        self.assertEqual(cfg, old)
        self.assertEqual(cfg["slaves"][0], {"device": "leadshine.ECT60", "alias": 60,
                                            "position": 0, "label": "axis_x"})

    def test_ring_positions(self):
        old = _scan.build_config(bus(), bus="enp5s0", positions=True)[0]
        del old["slaves"][4]
        cfg, report = self.update(old)
        self.assertEqual([e["position"] for e in cfg["slaves"]], [0, 1, 2, 3, 4, 5])
        self.assertEqual(cfg["slaves"][4]["position"], 4)      # added at its ring position
        self.assertNotIn("alias", cfg["slaves"][4])

    def test_top_level_settings_are_kept_unless_given(self):
        old = self.edited()
        old["bus"] = "eth7"
        old["master"] = 1
        cfg, _ = self.update(old)
        self.assertEqual(list(cfg), list(old))
        self.assertEqual((cfg["bus"], cfg["master"], cfg["history_s"]), ("eth7", 1, 30))
        cfg, _ = self.update(old, bus="eth1", cycle_us=500)
        self.assertEqual((cfg["bus"], cfg["cycle_us"]), ("eth1", 500))


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        runners = []

        def runner(tool, master=0):
            runners.append(master)
            return recorded
        self.runners = runners
        with mock.patch.object(_scan, "ethercat_runner", runner), \
                mock.patch.object(_scan, "interface_for_mac", lambda mac: "enp5s0"), \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                _scan.main(list(argv))
        return cm.exception.code, out.getvalue(), err.getvalue()

    def test_stdout_and_file(self):
        code, out, err = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["bus"], "bus0")              # not the NIC name
        self.assertNotIn('"device"', out)
        self.assertIn('"device": "leadshine.ECT60"', self.run_cli("scan", "--short")[1])
        self.assertIn("6 slave(s)", err)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "cfg.json")
            self.assertEqual(self.run_cli("scan", "-o", str(path))[0], 0)
            self.assertEqual(json.loads(path.read_text())["cycle_us"], 1000)
            code, _, err = self.run_cli("scan", "-o", str(path))
            self.assertEqual(code, 1)
            self.assertIn("--force", err)
            self.assertEqual(self.run_cli("scan", "-o", str(path), "--force")[0], 0)

    def test_master_index(self):
        code, out, err = self.run_cli("scan", "--master", "1")
        self.assertEqual(code, 0)
        self.assertEqual(self.runners, [1])
        cfg = json.loads(out)
        self.assertEqual(list(cfg)[:3], ["bus", "master", "cycle_us"])
        self.assertEqual(cfg["master"], 1)
        self.assertIn("on master 1", err)
        self.assertNotIn('"master"', self.run_cli("scan")[1])      # 0 is the default

    def test_update(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "machine.json")
            cfg = json.loads(self.run_cli("scan")[1])
            cfg["master"] = 2
            cfg["slaves"][0]["label"] = "axis_x"
            del cfg["slaves"][3]
            path.write_text(json.dumps(cfg))
            before = path.read_text()
            code, out, err = self.run_cli("scan", "--update", str(path))
            self.assertEqual(code, 0)
            self.assertEqual(self.runners, [2])              # the config's master
            new = json.loads(out)
            self.assertEqual(new["slaves"][0]["label"], "axis_x")
            self.assertEqual(len(new["slaves"]), 6)
            self.assertIn("ADDED", err)
            self.assertIn("5 kept, 1 added, 0 replaced, 0 removed", err)
            self.assertEqual(path.read_text(), before)       # the input is not changed
            self.assertEqual(self.run_cli("scan", "--update", str(path), "-o", str(path))[0], 1)
            self.assertEqual(self.run_cli("scan", "--update", str(path), "-o", str(path),
                                          "--force")[0], 0)
            self.assertEqual(len(json.loads(path.read_text())["slaves"]), 6)
            code, _, err = self.run_cli("scan", "--update", str(Path(d, "missing.json")))
            self.assertIn("cannot read", str(code))

    def test_expand(self):
        cfg = {"bus": "eth1", "cycle_us": 1000, "slaves": [
            {"device": "amsamotion.EC3A-IO1632", "alias": 40, "label": "io"},
            {"position": 3, "label": "plain", "vendor_id": "0x1", "product_code": "0x2",
             "txpdo": {"entries": []}, "rxpdo": {"entries": []}}]}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "config.json")
            path.write_text(json.dumps(cfg))
            code, out, err = self.run_cli("expand", str(path))
            self.assertEqual(code, 0)
            full = json.loads(out)
            self.assertEqual(full, expand_config(cfg))              # as the profiles give it
            self.assertEqual(full["slaves"][0]["product_code"], "0x00001101")
            self.assertEqual(full["slaves"][1], cfg["slaves"][1])  # written out: unchanged
            target = Path(d, "machine.json")
            self.assertEqual(self.run_cli("expand", str(path), "-o", str(target))[0], 0)
            self.assertEqual(json.loads(target.read_text()), full)
            code, _, err = self.run_cli("expand", str(path), "-o", str(target))
            self.assertEqual(code, 1)
            self.assertIn("--force", err)

            path.write_text(json.dumps({"slaves": [{"device": "amsamotion.NOPE", "label": "x"}]}))
            code, _, _ = self.run_cli("expand", str(path))
            self.assertIn("NOPE", str(code))                        # sys.exit(message)

            path.write_text(json.dumps({"slaves": [cfg["slaves"][1]]}))
            code, out, err = self.run_cli("expand", str(path))
            self.assertEqual(code, 0)
            self.assertIn("names no device classes", err)

    def test_python_m_odycat_says_how_to_start_the_engine(self):
        import os
        import subprocess
        env = dict(os.environ)
        if env.get("ODYCAT_TEST_INSTALLED") != "1":     # else the installed package (_support)
            env["PYTHONPATH"] = str(Path(_support.REPO_ROOT, "src"))
        r = subprocess.run([sys.executable, "-m", "odycat"], capture_output=True, text=True,
                           env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("sudo odycat machine.json", r.stderr)
        self.assertIn("odycat-config expand", r.stderr)

    def test_runner_passes_the_master(self):
        run = _scan.ethercat_runner("echo", master=2)
        self.assertEqual(run(["slaves", "-v", "-p", "3"]).split(), ["slaves", "-v", "-p", "3", "-m", "2"])


if __name__ == "__main__":
    unittest.main()


def _csp_drive(sync_types):
    """A drive with only a fixed CSP PDO set (no 0x6060/0x6061)."""
    E = esi.EsiEntry
    rx = esi.EsiPdo("rx", 0x1701, "", 2, True, True, (
        E(0x6040, 0, 16, "", ""), E(0x607A, 0, 32, "", ""), E(0x60B8, 0, 16, "", ""),
        E(0x60FE, 1, 32, "", "")))
    tx = esi.EsiPdo("tx", 0x1B01, "", 3, True, True, (
        E(0x603F, 0, 16, "", ""), E(0x6041, 0, 16, "", ""), E(0x6064, 0, 32, "", ""),
        E(0x60B9, 0, 16, "", ""), E(0x60BA, 0, 32, "", ""), E(0x60BC, 0, 32, "", ""),
        E(0x60FD, 0, 32, "", "")))
    dev = esi.EsiDevice(0x12345678, 0x715, 1, "Test Drive", "Test Drive", (), (rx, tx),
                        (), False)
    return _scan.BusSlave(6, 3, 1, "PREOP", True, True, dev, {}, "", sync_types)


class DriveScanTests(unittest.TestCase):
    def entry(self, sync_types):
        entry, lines = _scan._slave_entry(_csp_drive(sync_types), {}, False, False, set())
        return entry, "\n".join(lines)

    def test_no_free_run_writes_dc_sync(self):
        entry, report = self.entry(0x0004)
        self.assertEqual(entry["dc_sync"], 0)
        self.assertIn("does not run in free-run (0x1C32:04 = 0x0004)", report)
        self.assertNotIn("runs in free-run. For CSP", report)

    def test_free_run_only_notes(self):
        for st in (0x0005, None, 0):          # free-run allowed, unknown, nonsense
            entry, report = self.entry(st)
            self.assertNotIn("dc_sync", entry)
            self.assertIn("runs in free-run. For CSP", report)

    def test_neither_free_run_nor_sync0(self):
        entry, report = self.entry(0x0002)    # SM-synchronous only
        self.assertNotIn("dc_sync", entry)
        self.assertIn("will refuse SAFEOP", report)

    def test_no_modes_of_operation(self):
        entry, report = self.entry(0x0004)
        self.assertIn("no modes of operation (0x6060/0x6061) in the PDO map: probably a "
                      "CSP PDO set", report)
        labels = [e["label"] for e in entry["txpdo"]["entries"]]
        self.assertIn("touch_probe_pos2_pos", labels)
        tp2 = next(e for e in entry["txpdo"]["entries"] if e["label"] == "touch_probe_pos2_pos")
        self.assertEqual(tp2["type"], "i32")
        self.assertNotIn("typed by size only", report)
