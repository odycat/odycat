# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""The engine's config parser (engine/config.c), through engine/tests/config_dump.

The engine reads the config as root, and the Python package reads the same
text: every field must be read exactly, or refused. Skipped where the parser
cannot be built (no compiler or no libcjson-dev).
"""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"
DUMP = ENGINE / "tests" / "config_dump"

BASE = {
    "bus": "bus0",
    "cycle_us": 1000,
    "slaves": [{
        "position": 0,
        "label": "io",
        "vendor_id": "0x00000002",
        "product_code": "0x07113052",
        "txpdo": {"index": "0x1A00", "entries": [
            {"index": "0x6000", "subindex": 1, "type": "u16"}]},
        "rxpdo": {"index": "0x1600", "entries": [
            {"index": "0x7000", "subindex": 1, "type": "u8"},
            {"index": "0x0000", "subindex": 0, "bits": 8}]},
    }],
}


def setUpModule():
    try:
        r = subprocess.run(["make", "-s", "-C", str(ENGINE), "config-dump"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise unittest.SkipTest(f"cannot build the config parser: {e}")
    if r.returncode != 0 or not DUMP.exists():
        raise unittest.SkipTest("cannot build the config parser (needs a C compiler "
                                "and libcjson-dev): " + r.stderr.strip()[-200:])


class EngineConfig(unittest.TestCase):
    def run_text(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "c.json")
            p.write_bytes(text if isinstance(text, bytes) else text.encode())
            return subprocess.run([str(DUMP), str(p)], capture_output=True, text=True,
                                  timeout=30)

    def parsed(self, cfg):
        r = self.run_text(json.dumps(cfg))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def refused(self, cfg_or_text, *needles):
        text = cfg_or_text if isinstance(cfg_or_text, (str, bytes)) else json.dumps(cfg_or_text)
        r = self.run_text(text)
        self.assertEqual(r.returncode, 1, f"accepted: {r.stdout[:200]}")
        for n in needles:
            self.assertIn(n, r.stderr)
        return r.stderr

    def slave(self, **changes):
        cfg = copy.deepcopy(BASE)
        cfg["slaves"][0].update(changes)
        return cfg

    def entry(self, direction="txpdo", **changes):
        cfg = copy.deepcopy(BASE)
        cfg["slaves"][0][direction]["entries"][0].update(changes)
        return cfg

    # --- what is accepted, and read as the Python package reads it ---------

    def test_base(self):
        s = self.parsed(BASE)["slaves"][0]
        self.assertEqual((s["vendor_id"], s["product_code"]), (2, 0x07113052))
        self.assertEqual(s["txpdo"]["pdos"][0]["entries"][0],
                         {"index": 0x6000, "subindex": 1, "bits": 16})

    def test_entry_index_as_number(self):
        # Python reads a number as the object index: it must not become a gap.
        s = self.parsed(self.entry(index=0x6000))["slaves"][0]
        self.assertEqual(s["txpdo"]["pdos"][0]["entries"][0]["index"], 0x6000)

    def test_subindex_as_hex_string(self):
        s = self.parsed(self.entry(subindex="0x11"))["slaves"][0]
        self.assertEqual(s["txpdo"]["pdos"][0]["entries"][0]["subindex"], 17)

    def test_alias_and_position_forms(self):
        s = self.parsed(self.slave(alias="0x3C", position="1"))["slaves"][0]
        self.assertEqual((s["alias"], s["position"]), (60, 1))

    def test_dc_settings(self):
        dc = {"assign_activate": 768, "sync0_cycle_ns": 2000000,
              "sync0_shift_ns": -50000, "sync1_cycle_ns": 0, "sync1_shift_ns": 0}
        s = self.parsed(self.slave(dc_sync=dc))["slaves"][0]
        self.assertEqual(s["dc"], {**dc, "assign_activate": 0x0300})
        s = self.parsed(self.slave(dc_sync=0))["slaves"][0]
        self.assertEqual(s["dc"]["assign_activate"], 0x0300)

    def test_longest_label(self):
        self.assertEqual(self.parsed(self.slave(label="a" * 63))["slaves"][0]["label"], "a" * 63)

    # --- integers: exact and in range, or refused --------------------------

    def test_identity_must_be_hex(self):
        self.refused(self.slave(vendor_id="abc"), "'vendor_id'")
        self.refused(self.slave(vendor_id="0x2 "), "'vendor_id'")
        self.refused(self.slave(product_code="0x1FFFFFFFF"), "'product_code'")
        self.refused(self.slave(product_code=5), "'product_code'")

    def test_pdo_index(self):
        cfg = copy.deepcopy(BASE)
        cfg["slaves"][0]["txpdo"]["index"] = "0x1Azz"
        self.refused(cfg, "txpdo index")

    def sms(self, cfg, key="txpdo"):
        return [p["sm"] for p in self.parsed(cfg)["slaves"][0][key]["pdos"]]

    def test_sync_managers(self):
        self.assertEqual((self.sms(BASE), self.sms(BASE, "rxpdo")), ([3], [2]))   # defaults
        # No mailbox: outputs in SM0, inputs in SM1 (a number or a string)
        cfg = copy.deepcopy(BASE)
        cfg["slaves"][0]["rxpdo"]["sm"] = 0
        cfg["slaves"][0]["txpdo"]["sm"] = "1"
        self.assertEqual((self.sms(cfg), self.sms(cfg, "rxpdo")), ([1], [0]))
        for bad in (16, -1, "x", 1.5):
            cfg = copy.deepcopy(BASE)
            cfg["slaves"][0]["txpdo"]["sm"] = bad
            self.refused(cfg, "txpdo 'sm'")
        cfg = copy.deepcopy(BASE)
        cfg["slaves"][0]["txpdo"]["sm"] = 2
        self.refused(cfg, "txpdo and rxpdo are both in SM2")
        # An empty direction does not hold its default sync manager
        cfg["slaves"][0]["rxpdo"] = {"entries": []}
        self.assertEqual(self.sms(cfg), [2])

    def channels(self, sms):
        """Outputs of one bit per channel, channel n in sms[n]."""
        cfg = copy.deepcopy(BASE)
        cfg["slaves"][0]["txpdo"] = {"entries": []}
        cfg["slaves"][0]["rxpdo"] = [
            {"index": f"0x{0x1600 + n:04X}", "sm": sm, "entries": [
                {"index": f"0x{0x7000 + 0x10 * n:04X}", "subindex": 1, "bits": 1}]}
            for n, sm in enumerate(sms)]
        return cfg

    def test_a_direction_over_two_sync_managers(self):
        # 16 outputs, a byte in SM0 and a byte in SM1
        cfg = self.channels([0] * 8 + [1] * 8)
        self.assertEqual(self.sms(cfg, "rxpdo"), [0] * 8 + [1] * 8)
        self.assertEqual(self.parsed(cfg)["slaves"][0]["rxpdo"]["size"], 2)

    def test_each_sync_manager_holds_whole_bytes(self):
        self.refused(self.channels([0] * 4 + [1] * 12),
                     "rxpdo[4] starts SM1, but the PDOs of SM0 before it end 4 bit(s) "
                     "into a byte", '"bits":4')

    def test_the_pdos_of_a_sync_manager_follow_each_other(self):
        self.refused(self.channels([0] * 8 + [1] * 8 + [0] * 8),
                     "rxpdo[16] is in SM0, after PDOs of SM1")
        # A PDO without entries is not assigned: its "sm" does not count
        cfg = self.channels([0] * 8 + [1] * 8)
        cfg["slaves"][0]["rxpdo"].append({"index": "0x1610", "sm": 0, "entries": []})
        self.assertEqual(self.sms(cfg, "rxpdo"), [0] * 8 + [1] * 8)

    def test_entry_fields(self):
        self.refused(self.entry(index="0x60zz"), "'index'")
        self.refused(self.entry(index=0x10000), "'index'")
        self.refused(self.entry(index=True), "'index'")
        self.refused(self.entry(subindex=300), "'subindex'")
        self.refused(self.entry(subindex=1.5), "'subindex'")
        self.refused(self.entry(subindex="one"), "'subindex'")
        for bits in (300, 3.7, 0, -1, "x"):
            cfg = self.entry(bits=bits)
            del cfg["slaves"][0]["txpdo"]["entries"][0]["type"]
            self.refused(cfg, "'bits'")

    def test_address(self):
        for alias in (70000, 3.5, -1, "0x1zz", True):
            self.refused(self.slave(alias=alias), "'alias'")
        for pos in (-1, 2.5, 1e10, "x"):
            self.refused(self.slave(position=pos), "'position'")

    def test_master(self):
        for m in (1.5, 256, -1, "0"):
            self.refused({**BASE, "master": m}, "'master'")

    def test_dc_values(self):
        for key, value in (("assign_activate", "zz"), ("assign_activate", 0x10000),
                           ("sync0_shift_ns", 3e9), ("sync1_cycle_ns", -1),
                           ("sync1_shift_ns", 0.5), ("sync0_cycle_ns", "fast")):
            self.refused(self.slave(dc_sync={key: value}), key)
        self.refused(self.slave(dc_sync=-5), "sync0_cycle_ns")

    # --- names are not cut ------------------------------------------------

    def test_long_names(self):
        self.refused(self.slave(label="a" * 64), "label", "too long")
        self.refused({**BASE, "bus": "b" * 64}, "bus name", "too long")
        self.refused({**BASE, "shm_group": "g" * 64}, "shm_group", "too long")

    # --- one JSON text, read the same way by the engine and by Python -----

    def test_duplicate_keys(self):
        text = json.dumps(BASE)
        self.refused(text.replace('"bus": "bus0"', '"bus": "bus0", "bus": "bus1"'),
                     '"bus" twice')
        self.refused(text.replace('"subindex": 1,', '"subindex": 1, "subindex": 2,', 1),
                     '"subindex" twice')

    def test_whole_text_is_one_json_value(self):
        self.refused(json.dumps(BASE) + " {}", "parse error")
        self.refused(json.dumps(BASE["slaves"]), "must be a JSON object")
        self.refused({**BASE, "slaves": [5]}, "must be an object")

    def test_file(self):
        self.refused(b"", "bytes")
        self.refused(json.dumps(BASE).encode() + b"\0", "NUL")
        r = subprocess.run([str(DUMP), str(ENGINE)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a regular file", r.stderr)


if __name__ == "__main__":
    unittest.main()
