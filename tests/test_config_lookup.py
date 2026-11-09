# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Slave lookup and shm-offset resolution, against the engine's layout rules.

find_slave() implements the IgH addressing the docs describe (alias 0 =
absolute ring position, alias N = offset from the slave carrying that station
alias), and resolve_slave_shm_offset() must produce the same offsets the C
engine uses for its PDO copy loops. Both are checked without a bus: offsets
are compared against the mirror model in tests/_support.py for every config
shipped in the repo.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import (SHM_HEADER_SIZE, align_up_8, engine_offsets, repo_configs,  # noqa: E402
                      slave_bytes, word_slave)

from odycat import _config  # noqa: E402

CFG = {"bus": "eth0", "slaves": [
    word_slave("drive_a", 0),                    # alias 0: absolute position
    word_slave("io_1", 1, alias=3),              # "3:1"
    word_slave("io_2", 2, alias=3),              # "3:2"
    word_slave("drive_b", 0, alias=3),           # "3:0"
    word_slave("drive_c", 5, alias=3),           # "3:5"
]}


def dup(field, value):
    """CFG with the last two slaves' 'field' set to the same value."""
    cfg = {"slaves": [dict(s) for s in CFG["slaves"]]}
    for sl in cfg["slaves"][-2:]:
        sl[field] = value
    return cfg


class FindSlaveTests(unittest.TestCase):
    def test_by_label(self):
        for label in ("drive_a", "io_1", "drive_c"):
            with self.subTest(label):
                self.assertEqual(_config.find_slave(CFG, label=label)["label"], label)

    def test_by_address(self):
        for (alias, pos), label in [((0, 0), "drive_a"), ((3, 0), "drive_b"),
                                    ((3, 1), "io_1"), ((3, 2), "io_2"), ((3, 5), "drive_c")]:
            with self.subTest(f"{alias}:{pos}"):
                self.assertEqual(_config.find_slave(CFG, pos, alias=alias)["label"], label)

    def test_alias_alone_means_offset_zero(self):
        self.assertEqual(_config.find_slave(CFG, alias=3)["label"], "drive_b")

    def test_alias_zero_without_a_position_is_rejected(self):
        with self.assertRaises(ValueError):
            _config.find_slave(CFG, alias=0)
        self.assertEqual(_config.find_slave(CFG, 0)["label"], "drive_a")

    def test_no_match_lists_what_exists(self):
        with self.assertRaises(KeyError) as cm:
            _config.find_slave(CFG, 7, alias=3)
        self.assertIn("3:7", str(cm.exception))
        self.assertIn("drive_a", str(cm.exception))          # available slaves
        with self.assertRaises(KeyError):
            _config.find_slave(CFG, label="nope")

    def test_duplicate_label_is_an_error(self):
        with self.assertRaises(ValueError) as cm:
            _config.find_slave(dup("label", "twin"), label="twin")
        self.assertIn("matches 2 slaves", str(cm.exception))

    def test_duplicate_address_is_an_error(self):
        with self.assertRaises(ValueError):
            _config.find_slave(dup("position", 9), 9, alias=3)

    def test_needs_something_to_match_on(self):
        with self.assertRaises(ValueError) as cm:
            _config.find_slave(CFG)
        self.assertIn("position, an alias, or a label", str(cm.exception))

    def test_address_parsing_accepts_hex_strings(self):
        self.assertEqual(_config.slave_address({"alias": "0x0003", "position": "2"}), (3, 2))
        self.assertEqual(_config.slave_address({}), (0, 0))
        self.assertEqual(_config._as_int("0x10"), 16)
        self.assertEqual(_config._as_int(7), 7)
        self.assertEqual(_config._as_int(None, 5), 5)


class ShmOffsetTests(unittest.TestCase):
    def test_auto_offsets_match_engine_model(self):
        """Every slave of every repo config: Python offset == engine offset."""
        for rel, cfg in repo_configs():
            for sl, expected in zip(cfg["slaves"], engine_offsets(cfg)):
                with self.subTest(f"{rel} {sl.get('label')}"):
                    self.assertEqual(
                        _config.resolve_slave_shm_offset(cfg, label=sl.get("label")),
                        expected)

    def test_address_and_label_agree(self):
        cfg = repo_configs()[1][1]                          # demo_box.json (aliases)
        for sl in cfg["slaves"]:
            alias, pos = _config.slave_address(sl)
            with self.subTest(sl.get("label")):
                self.assertEqual(_config.resolve_slave_shm_offset(cfg, pos, alias=alias),
                                 _config.resolve_slave_shm_offset(cfg, label=sl["label"]))

    def test_offsets_are_aligned_and_never_under_the_header(self):
        for rel, cfg in repo_configs():
            with self.subTest(rel):
                for sl, off in zip(cfg["slaves"], engine_offsets(cfg)):
                    self.assertEqual(off % 8, 0)
                    self.assertGreaterEqual(off, SHM_HEADER_SIZE)
                    end = off + sum(slave_bytes(sl))
                    nxt = _config._align_up_8(end)
                    self.assertEqual(nxt, align_up_8(end))

    def test_unknown_slave_raises(self):
        with self.assertRaises(KeyError):
            _config.resolve_slave_shm_offset(CFG, label="ghost")


if __name__ == "__main__":
    unittest.main()
