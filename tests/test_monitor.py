# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_monitor.py
"""
BusMonitor against the fake engine.

Covers the three things that are easy to get wrong: absolute bit offsets
inside the data SHM, the per-slave status table the engine publishes after the
last data block, and re-attaching after an engine restart (new inodes, new
config).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import (SHM_HEADER_SIZE, bit_slave, expected_wkc, fake_engine,   # noqa: E402
                      mini_config, word_slave)

from odycat.monitor import BusMonitor  # noqa: E402

IF = "fake0"


class LayoutTests(unittest.TestCase):
    def test_layout_matches_the_engine_blocks(self):
        with fake_engine(bus=IF) as eng:
            mon = BusMonitor(IF)
            layout = mon.layout()
            self.assertEqual(layout["cycle_us"], 1000)
            self.assertEqual([s["label"] for s in layout["slaves"]], ["word_io", "bit_io"])
            self.assertEqual([s["alias"] for s in layout["slaves"]], [0, 3])
            self.assertEqual([s["position"] for s in layout["slaves"]], [0, 1])
            self.assertEqual([s["shm_offset"] for s in layout["slaves"]],
                             [s["offset"] for s in eng.slaves])
            self.assertEqual(layout["layout_id"], eng.data_path.stat().st_ino)
            mon.close()

    def test_bit_offsets_are_absolute_in_the_data_shm(self):
        with fake_engine(bus=IF) as eng:
            mon = BusMonitor(IF)
            for sl, blk in zip(mon.layout()["slaves"], eng.slaves):
                base = blk["offset"] * 8
                for f in sl["inputs"]:
                    self.assertGreaterEqual(f["bit_offset"], base)
                    self.assertLess(f["bit_offset"], base + blk["tx"] * 8)
                for f in sl["outputs"]:
                    self.assertGreaterEqual(f["bit_offset"], base + blk["tx"] * 8)
                    self.assertLess(f["bit_offset"],
                                    base + (blk["tx"] + blk["rx"]) * 8)
            mon.close()

    def test_layout_when_engine_is_down(self):
        with fake_engine(bus=IF) as eng:
            eng.data_path.unlink()
            mon = BusMonitor(IF)
            self.assertIsNone(mon.layout())
            snap = mon.snapshot()
            self.assertEqual(snap["engine"], "stopped")
            self.assertIn("not running", snap["error"])
            self.assertIsNone(mon.sample([(0, "inputs", 0)]))
            mon.close()


class SnapshotTests(unittest.TestCase):
    def test_decodes_every_entry_of_every_slave(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x34\x12\xa5",
                                "bit_io": b"\xe7\x5a\xcd\x12"},
                        status=[(8, 1, 1), (4, 1, 0)])
            snap = BusMonitor(IF).snapshot()
            self.assertEqual(snap["engine"], "running")
            self.assertEqual(snap["slaves"][0]["inputs"], [0x1234, -91])
            self.assertEqual(snap["slaves"][0]["outputs"], [0])
            self.assertEqual(snap["slaves"][1]["inputs"],
                             [1, 1, 1, 0, 0xAE, 0x05, 0x12CD])
            self.assertEqual(snap["slaves"][1]["outputs"], [0] * 13)
            self.assertEqual(snap["slaves"][0]["status"]["al_state_name"], "OP")
            self.assertEqual(snap["slaves"][1]["status"]["al_state_name"], "SAFEOP")
            self.assertFalse(snap["slaves"][1]["status"]["operational"])
            self.assertEqual(snap["slaves"][1]["status"]["ring_pos"], 1)

    def test_bus_flags_and_counters(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(work_counter=expected_wkc(eng.cfg) - 2, cycles=4,
                        error_flags=3, status=[(8, 1, 1), (8, 1, 1)])
            bus = BusMonitor(IF).snapshot()["bus"]
            self.assertEqual(bus["cycle_count"], 4)
            self.assertEqual(bus["work_counter"], bus["expected_wkc"] - 2)
            self.assertTrue(bus["wkc_mismatch"])
            self.assertTrue(bus["slave_lost"])
            self.assertTrue(bus["link_up"])
            self.assertEqual(bus["slaves_responding"], 2)
            self.assertTrue(bus["rt_sched_fifo"])
            self.assertTrue(bus["rt_memory_locked"])
            self.assertEqual(bus["master_al_states"], 8)
            self.assertTrue(bus["has_status"])
            self.assertGreaterEqual(bus["last_cycle_age_ms"], 0)

    def test_engine_starting(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(bus_state=4)
            snap = BusMonitor(IF).snapshot()
            self.assertEqual(snap["engine"], "starting")
            self.assertFalse(snap["bus"]["wkc_mismatch"])

    def test_engine_stalled(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(stalled_for_ns=600_000_000)      # last cycle 600 ms old
            snap = BusMonitor(IF).snapshot()
            self.assertEqual(snap["engine"], "stalled")
            self.assertGreater(snap["bus"]["last_cycle_age_ms"], 500)

    def test_torn_seqlock_is_reported(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x34\x12\xa5"})
            eng.set(seq_counter=eng.get("seq_counter") + 1)     # engine mid-update
            snap = BusMonitor(IF).snapshot()
            self.assertEqual(snap["engine"], "stopped")
            self.assertIn("stopped in the middle of copying", snap["error"])

    def test_status_table_absent(self):
        cfg = {"bus": IF, "slaves": [word_slave("solo", 0)]}
        with fake_engine(cfg, IF) as eng:
            eng.set(status_offset=0, status_count=0)      # engine not in OP yet
            snap = BusMonitor(IF).snapshot()
            self.assertFalse(snap["bus"]["has_status"])
            self.assertIsNone(snap["bus"]["slaves_responding"])
            self.assertIsNone(snap["slaves"][0]["status"])
            self.assertEqual(snap["slaves"][0]["inputs"], [0, 0])

    def test_al_status_code(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(status=[(8, 1, 1), (0x12, 1, 0, 0x001E)])
            snap = BusMonitor(IF).snapshot()
            ok, down = (s["status"] for s in snap["slaves"])
            self.assertEqual((ok["al_code"], ok["al_status"]), (0, ""))
            self.assertEqual((down["al_code"], down["al_status"]),
                             (0x001E, "Invalid input configuration"))
            self.assertEqual(down["al_state_name"], "PREOP")

    def test_status_table_present_but_empty(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(status=[(8, 1, 1), (8, 1, 1)])
            eng.set(status_count=0)                  # table region there, no rows
            snap = BusMonitor(IF).snapshot()
            self.assertTrue(snap["bus"]["has_status"])   # the table offset is set
            self.assertEqual(snap["bus"]["slaves_responding"], 2)
            self.assertIsNone(snap["slaves"][0]["status"])
            self.assertIsNone(snap["slaves"][1]["status"])
            self.assertEqual(snap["slaves"][0]["inputs"], [0, 0])   # PDAs intact


class RestartTests(unittest.TestCase):
    def test_survives_an_engine_restart(self):
        with fake_engine(bus=IF) as eng:
            mon = BusMonitor(IF)
            eng.publish(inputs={"word_io": b"\x34\x12\xa5"})
            before = mon.snapshot()
            self.assertEqual(before["engine"], "running")

            eng.restart()                        # both regions unlinked + recreated
            after = mon.snapshot()
            self.assertEqual(after["engine"], "running")
            self.assertNotEqual(after["layout_id"], before["layout_id"])
            self.assertEqual(after["slaves"][0]["inputs"], [0, 0])   # fresh SHM

    def test_picks_up_a_different_config_after_restart(self):
        with fake_engine(bus=IF) as eng:
            mon = BusMonitor(IF)
            self.assertEqual([s["label"] for s in mon.layout()["slaves"]],
                             ["word_io", "bit_io"])
            eng.restart(mini_config(word_slave("only_one", 0),
                                    bit_slave("second", 1), bus=IF))
            eng.publish(status=[(8, 1, 1), (8, 1, 1)])
            snap = mon.snapshot()
            layout = mon.layout()
            self.assertEqual([s["label"] for s in layout["slaves"]],
                             ["only_one", "second"])
            self.assertEqual(layout["slaves"][1]["shm_offset"],
                             SHM_HEADER_SIZE + 8)           # word_io block is 8 B
            self.assertEqual(snap["bus"]["slaves_responding"], 2)
            self.assertEqual(snap["slaves"][1]["status"]["al_state_name"], "OP")
            self.assertEqual(len(snap["slaves"][1]["inputs"]), 7)
            self.assertEqual(len(snap["slaves"][1]["outputs"]), 13)

    def test_sample_follows_the_new_layout_after_restart(self):
        with fake_engine(bus=IF) as eng:
            mon = BusMonitor(IF)
            eng.publish(inputs={"word_io": b"\x01\x00\x00"})
            self.assertEqual(mon.sample([(0, "inputs", 0)])[3], [1])
            eng.restart(mini_config(word_slave("only_one", 0), bus=IF))
            eng.publish(inputs={"only_one": b"\x21\x00\x00"})
            layout_id, cycles, last_ns, values = mon.sample([(0, "inputs", 0)])
            self.assertEqual(layout_id, eng.data_path.stat().st_ino)
            self.assertEqual(values, [0x21])
            with self.assertRaises(IndexError):
                mon.sample([(1, "inputs", 0)])       # the old layout had two slaves
            mon.close()

    def test_monitor_started_before_the_engine(self):
        mon = BusMonitor(IF)
        self.assertEqual(mon.snapshot()["engine"], "stopped")   # nothing to attach to yet
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x08\x00\x01"})
            snap = mon.snapshot()
            self.assertEqual(snap["engine"], "running")
            self.assertEqual(snap["slaves"][0]["inputs"], [8, 1])
        mon.close()


class SampleTests(unittest.TestCase):
    """sample() is the hot path of a dashboard: a handful of decoded fields."""

    def test_sample_reads_only_selected_entries(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x34\x12\xa5",
                                "bit_io": b"\xe7\x5a\xcd\x12"}, cycles=8)
            eng.write_outputs("word_io", b"\x34\x12")
            eng.publish()                      # the monitor shows outputs as sent
            out = BusMonitor(IF).sample([(0, "inputs", 0), (1, "inputs", 4),
                                         (0, "outputs", 0)])
            self.assertIsNotNone(out)
            layout_id, cycles, last_ns, values = out
            self.assertEqual(layout_id, eng.data_path.stat().st_ino)
            self.assertEqual(cycles, 9)
            self.assertGreater(last_ns, 0)
            self.assertEqual(values, [0x1234, 0xAE, 0x1234])

    def test_sample_is_none_when_engine_is_gone(self):
        with fake_engine(bus=IF) as eng:
            mon = BusMonitor(IF)
            self.assertIsNotNone(mon.sample([(0, "inputs", 0)]))
            eng.data_path.unlink()
            self.assertIsNone(mon.sample([(0, "inputs", 0)]))
            mon.close()

    def test_sample_is_none_on_a_torn_window(self):
        with fake_engine(bus=IF) as eng:
            eng.publish()
            eng.set(seq_counter=eng.get("seq_counter") + 1)
            self.assertIsNone(BusMonitor(IF).sample([(0, "inputs", 0)]))

    def test_bad_selection_is_an_error_not_a_silent_none(self):
        with fake_engine(bus=IF):
            mon = BusMonitor(IF)
            with self.assertRaises(IndexError):
                mon.sample([(0, "inputs", 99)])
            with self.assertRaises(KeyError):
                mon.sample([(0, "nonsense", 0)])



class AlTextTests(unittest.TestCase):
    def test_describe(self):
        from odycat._al import describe, al_status_text
        self.assertEqual(describe(0, False, 0), "offline")
        self.assertEqual(describe(4, True, 0), "in SAFEOP")
        self.assertEqual(describe(0x12, True, 0x1E),
                         "in PREOP + error, AL status 0x001E (Invalid input configuration)")
        self.assertIn("in OP for less than 500 ms", describe(8, True, 0x27))
        self.assertEqual(al_status_text(0x8001), "vendor specific")
        self.assertEqual(al_status_text(0x0070), "unknown code")

if __name__ == "__main__":
    unittest.main()


