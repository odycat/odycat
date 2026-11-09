# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_bus_device.py
"""
Bus and Device against a fake engine — real code paths, no EtherCAT hardware.

The fake engine owns shared memory exactly like the engine does (same
header field offsets, same PDO block offsets), so Device's seqlock reads, its
staging buffer and its bit-level entry writes are exercised for real.

IF is the fake bus name: every call passes it explicitly so these tests
never pick up a real engine that happens to be running on the build machine.
"""

import struct
import sys
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import expected_wkc, fake_engine   # noqa: E402

from odycat import Bus, Device  # noqa: E402
from odycat.testing import SLAVE_STATUS_SIZE, STATUS_FMT  # noqa: E402
from odycat.device import DeviceLostError, EngineStoppedError  # noqa: E402
from odycat._shm import RT_MEMLOCK                 # noqa: E402

IF = "fake0"


class BusTests(unittest.TestCase):
    """Bus is a read-only view of the engine header."""

    def test_header_fields(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x34\x12\xa5"}, cycles=7,
                        status=[(8, 1, 1), (8, 1, 1)])
            bus = Bus(IF)
            self.assertTrue(bus.is_op)
            self.assertTrue(bus.slaves_ok)
            self.assertEqual(bus.cycle_count, 7)
            self.assertEqual(bus.work_counter, expected_wkc(eng.cfg))
            self.assertEqual(bus.expected_wkc, expected_wkc(eng.cfg))
            self.assertTrue(bus.wkc_ok)
            self.assertGreater(bus.last_cycle_ns, 0)
            bus.close()

    def test_slaves_ok_follows_the_slave_lost_flag(self):
        with fake_engine(bus=IF) as eng:
            bus = Bus(IF)
            eng.publish()
            self.assertTrue(bus.slaves_ok)
            eng.publish(error_flags=2)                    # ERR_SLAVE_LOST
            self.assertFalse(bus.slaves_ok)
            bus.close()

    def test_wkc_mismatch_is_visible(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(work_counter=expected_wkc(eng.cfg) - 2)
            bus = Bus(IF)
            self.assertFalse(bus.wkc_ok)
            self.assertEqual(bus.work_counter, bus.expected_wkc - 2)
            bus.close()

    def test_not_operational(self):
        with fake_engine(bus=IF) as eng:
            eng.set(bus_state=0, last_cycle_ns=0)         # engine still starting
            bus = Bus(IF)
            self.assertFalse(bus.is_op)
            bus.close()

    def test_no_engine_running(self):
        with fake_engine(bus=IF) as eng:
            eng.data_path.unlink()                        # engine stopped: shm gone
            with self.assertRaises(RuntimeError) as cm:
                Bus(IF)
            self.assertIn("not running", str(cm.exception))

    def test_cycles_accumulate(self):
        with fake_engine(bus=IF) as eng:
            bus = Bus(IF)
            first = bus.cycle_count
            eng.publish(cycles=3)
            self.assertEqual(bus.cycle_count, first + 3)  # Bus re-reads, never caches
            bus.close()

    @staticmethod
    def _cycling(eng, start: int | None = None):
        """Background thread publishing one cycle per ms until stopped."""
        if start is not None:
            eng.set(cycle_count=start)
        stop = threading.Event()

        def run():
            while not stop.wait(0.001):
                eng.set(cycle_count=eng.get("cycle_count") + 1)

        th = threading.Thread(target=run, daemon=True)
        th.start()
        return stop, th

    def test_wait_cycles_counts_engine_cycles(self):
        with fake_engine(bus=IF) as eng:
            bus = Bus(IF)
            dev = Device(label="word_io", bus=IF)
            stop, th = self._cycling(eng)
            try:
                first = bus.cycle_count
                reached = bus.wait_cycles(5)
                self.assertGreaterEqual((reached - first) & 0xFFFFFFFF, 5)
                first = reached
                reached = dev.wait_cycles(3)
                self.assertGreaterEqual((reached - first) & 0xFFFFFFFF, 3)
                self.assertEqual(bus.wait_cycles(0), bus.cycle_count)
            finally:
                stop.set()
                th.join()
            dev.close()
            bus.close()

    def test_cycle_count_is_64_bit(self):
        """The 32-bit half wrapping past 0 is invisible: the count goes on."""
        with fake_engine(bus=IF) as eng:
            bus = Bus(IF)
            stop, th = self._cycling(eng, start=0xFFFFFFFE)
            try:
                reached = bus.wait_cycles(5)
                self.assertGreaterEqual(reached, 0xFFFFFFFE + 5)
            finally:
                stop.set()
                th.join()
            self.assertGreater(bus.cycle_count, 0xFFFFFFFF)
            bus.close()

    def test_a_region_from_another_layout_is_refused(self):
        cases = [({"magic": 0}, "not initialised"),
                 ({"magic": 0x12345678}, "same version"),
                 ({"layout_major": 4}, "the engine is newer"),
                 ({"header_size": 128}, "header")]
        for fields, message in cases:
            with self.subTest(fields=fields), fake_engine(bus=IF) as eng:
                eng.set(**fields)
                with self.assertRaisesRegex(RuntimeError, message):
                    Bus(IF)
                with self.assertRaisesRegex(RuntimeError, message):
                    Device(label="word_io", bus=IF)

    def test_a_block_the_engine_placed_elsewhere_is_refused(self):
        with fake_engine(bus=IF) as eng:
            entry = eng.status_offset + SLAVE_STATUS_SIZE * 0
            fields = list(STATUS_FMT.unpack_from(eng._mm, entry))
            fields[6] += 8                           # the engine says: 8 bytes later
            STATUS_FMT.pack_into(eng._mm, entry, *fields)
            with self.assertRaisesRegex(RuntimeError, "placed this slave"):
                Device(label="word_io", bus=IF)

    def test_a_newer_minor_version_is_accepted(self):
        with fake_engine(bus=IF) as eng:
            eng.set(layout_minor=7)                  # fields added in reserved space
            Bus(IF).close()

    def test_wkc_error_counters(self):
        with fake_engine(bus=IF) as eng:
            bus = Bus(IF)
            self.assertEqual((bus.wkc_errors, bus.wkc_error_streak), (0, 0))
            eng.publish(work_counter=0, cycles=3)
            self.assertEqual((bus.wkc_errors, bus.wkc_error_streak), (3, 3))
            self.assertFalse(bus.wkc_ok)
            eng.publish(cycles=1)                    # right WKC: the streak ends
            self.assertEqual((bus.wkc_errors, bus.wkc_error_streak), (3, 0))
            eng.publish(work_counter=1, cycles=2)
            self.assertEqual((bus.wkc_errors, bus.wkc_error_streak), (5, 2))
            bus.close()

    def test_skipped_cycles_without_reading_the_trace(self):
        with fake_engine(bus=IF) as eng:
            bus = Bus(IF)
            eng.skip_cycles(4)
            self.assertEqual(bus.skipped_cycles, 4)
            bus.close()

    def test_engine_info(self):
        import hashlib
        import json
        with fake_engine(bus=IF) as eng:
            info = Bus(IF).engine_info
            text = json.dumps(eng.cfg).encode("utf-8")
            self.assertEqual(info["config_sha256"], hashlib.sha256(text).hexdigest())
            self.assertEqual(info["engine_version"], "0.0.0-fake")
            self.assertEqual(info["instance_id"], eng.instance_id.hex())
            self.assertEqual(info["layout_version"], "3.0")
            self.assertIsNone(info["support_end"])

    def test_rt_flags(self):
        with fake_engine(bus=IF) as eng:
            bus = Bus(IF)
            self.assertTrue(bus.rt_scheduling)
            self.assertTrue(bus.memory_locked)
            eng.set(rt_flags=RT_MEMLOCK)
            self.assertFalse(bus.rt_scheduling)
            self.assertTrue(bus.memory_locked)
            eng.set(rt_flags=0)
            self.assertFalse(bus.memory_locked)
            bus.close()

    def test_wait_cycles_stopped_engine_raises(self):
        with fake_engine(bus=IF):                   # nothing publishes cycles
            bus = Bus(IF)
            with self.assertRaises(RuntimeError) as cm:
                bus.wait_cycles(2, timeout_s=0.05)
            self.assertIn("did not complete", str(cm.exception))
            bus.close()


class DeviceLookupTests(unittest.TestCase):
    def test_label_position_and_alias(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"bit_io": b"\x01\x00\x00\x00"})
            by_label = Device(label="bit_io", bus=IF)
            by_addr = Device(1, alias=3, bus=IF)      # bit_io is at 3:1
            self.assertEqual(by_label.read_entry("din0"), 1)
            self.assertEqual(by_addr.read_entry("din0"), 1)
            self.assertEqual([f["label"] for f in by_label.entries()[0]],
                             [f["label"] for f in by_addr.entries()[0]])
            with self.assertRaises(KeyError):
                by_label.read_entry("inputs")               # a word_io entry
            self.assertEqual(Device(label="word_io", bus=IF).read_entry("inputs"), 0)
            by_label.close()
            by_addr.close()

    def test_address_must_match_the_config(self):
        with fake_engine(bus=IF):
            with self.assertRaises(KeyError):
                Device(1, bus=IF)          # bit_io is 3:1, not 0:1
            with self.assertRaises(KeyError):
                Device(label="nope", bus=IF)

    def test_entries_are_the_config_layout(self):
        with fake_engine(bus=IF):
            dev = Device(label="bit_io", bus=IF)
            inputs, outputs = dev.entries()
            self.assertEqual([f["label"] for f in inputs],
                             ["din0", "din1", "din2", "din3", "byte_in",
                              "nibble_in", "word_in"])
            self.assertEqual([f["label"] for f in outputs],
                             [f"dout{i}" for i in range(4)] + ["preset"] +
                             [f"dout{i}" for i in range(4, 12)])
            # Outputs start where the inputs end: tx 4 B = 32 bits
            self.assertEqual([f["bit_offset"] for f in outputs],
                             [32, 33, 34, 35, 36] + list(range(40, 48)))
            dev.close()


class DeviceIoTests(unittest.TestCase):
    """word_io: tx = H inputs + b status_byte (3 B), rx = H outputs (2 B)."""

    def test_read_inputs_published_by_the_engine(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x34\x12\xa5"})
            dev = Device(label="word_io", bus=IF)
            self.assertEqual(dev.read_txpdo(), (0x1234, -91))      # 'b' is signed
            self.assertEqual(dev.read_entry("inputs"), 0x1234)
            self.assertEqual(dev.read_entry("status_byte"), -91)
            self.assertEqual(dev.read_entry("outputs"), 0)         # outputs still zero
            self.assertTrue(dev.txpdo_bit(1, 0))               # bit0 of 0xa5
            dev.close()

    def test_outputs_round_trip(self):
        with fake_engine(bus=IF) as eng:
            dev = Device(label="word_io", bus=IF)
            dev.write_rxpdo(0x1234)
            self.assertEqual(eng.read_outputs("word_io"), b"\x34\x12")
            self.assertEqual(dev.read_rxpdo(), (0x1234,))
            self.assertEqual(dev.read_entry("outputs"), 0x1234)
            dev.write_entry("outputs", 0x0102)
            self.assertEqual(eng.read_outputs("word_io"), b"\x02\x01")
            dev.set_rxpdo_field(0, 0)
            self.assertEqual(dev.read_entry("outputs"), 0)
            self.assertEqual(eng.read_inputs("word_io"), b"\x00\x00\x00")  # inputs kept
            dev.close()

    def test_writes_land_in_this_slaves_block(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"bit_io": b"\x11\x22\x33\x44"})
            dev = Device(label="word_io", bus=IF)
            dev.write_entry("outputs", 0x1234)
            self.assertEqual(eng.read_outputs("word_io"), b"\x34\x12")
            self.assertEqual(eng.read_inputs("bit_io"), b"\x11\x22\x33\x44")
            self.assertEqual(eng.read_outputs("bit_io"), b"\x00\x00")
            dev.close()

    def test_mixed_bit_and_byte_entries(self):
        """byte_in sits at bit 4 (straddling), nibble_in at 12, word_in at 16."""
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"bit_io": b"\xe7\x5a\xcd\x12"})
            dev = Device(label="bit_io", bus=IF)
            self.assertEqual([dev.read_entry(f"din{i}") for i in range(4)],
                             [1, 1, 1, 0])
            self.assertEqual(dev.read_entry("byte_in"), 0xae)
            self.assertEqual(dev.read_entry("nibble_in"), 0x05)
            self.assertEqual(dev.read_entry("word_in"), 0x12cd)
            dev.close()

    def test_bit_level_writes_keep_neighbours(self):
        """bit_io rx: dout0..3 + preset in byte 0, dout4..11 in byte 1 (2 B)."""
        with fake_engine(bus=IF) as eng:
            dev = Device(label="bit_io", bus=IF)
            dev.write_rxpdo(0, 0)
            dev.write_entry("dout5", 1)          # byte 1, bit 1
            dev.write_entry("dout2", 1)          # byte 0, bit 2
            dev.write_entry("preset", 0b1010)    # byte 0, bits 4..7
            self.assertEqual(eng.read_outputs("bit_io"), b"\xa4\x02")
            self.assertEqual(dev.read_entry("dout5"), 1)
            self.assertEqual(dev.read_entry("dout2"), 1)
            self.assertEqual(dev.read_entry("preset"), 0b1010)
            self.assertEqual(dev.read_entry("dout0"), 0)
            dev.set_rxpdo_bit(1, 1, False)       # rx byte 1, bit 1
            self.assertEqual(eng.read_outputs("bit_io"), b"\xa4\x00")
            self.assertEqual(dev.read_entry("preset"), 0b1010)   # neighbour kept
            dev.close()

    def test_writing_an_input_is_refused(self):
        with fake_engine(bus=IF):
            dev = Device(label="bit_io", bus=IF)
            with self.assertRaises(ValueError) as cm:
                dev.write_entry("din0", 1)
            self.assertIn("input", str(cm.exception))
            with self.assertRaises(ValueError):
                dev.stage_entry("din0", 1)
            with self.assertRaises(ValueError) as cm:
                dev.write_entry("dout0", 2)              # entry is 1 bit wide
            self.assertIn("out of range", str(cm.exception))
            with self.assertRaises(KeyError):
                dev.write_entry("no_such_entry", 1)
            dev.close()

    def test_seqlock_refuses_a_torn_window(self):
        """An engine stuck in the middle of its input copy (sequence odd, no
        cycle for 10 cycles, at least 50 ms) is an error, after that time."""
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x34\x12\xa5"})
            eng.set(seq_counter=eng.get("seq_counter") + 1)     # engine mid-update
            dev = Device(label="word_io", bus=IF)
            t0 = time.monotonic()
            with self.assertRaises(RuntimeError) as cm:
                dev.read_txpdo()
            self.assertGreaterEqual(time.monotonic() - t0, 0.05)
            self.assertIn("stopped in the middle of copying", str(cm.exception))
            dev.read_entry("outputs")             # outputs are not seqlock protected
            dev.close()

    def test_seqlock_waits_for_a_preempted_engine(self):
        """An engine preempted inside its copy (sequence odd for a while) makes
        the reader wait, not fail: it reads the new inputs once the copy ends."""
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x34\x12\xa5"})
            dev = Device(label="word_io", bus=IF)
            base = eng.get("seq_counter")
            eng.set(seq_counter=base + 1)                         # copy starts...
            off = eng.slave("word_io")["offset"]

            def finish():
                time.sleep(0.03)                                  # ...the engine is preempted
                eng._mm[off:off + 3] = b"\x78\x56\x01"
                eng.set(seq_counter=base + 2)
            threading.Thread(target=finish).start()
            self.assertEqual(dev.read_entry("inputs"), 0x5678)
            dev.close()

    def test_seqlock_keeps_waiting_while_cycles_advance(self):
        """Odd for longer than the limit, but the engine is still running cycles:
        no error (only a stuck engine raises)."""
        with fake_engine(bus=IF) as eng:
            eng.publish(inputs={"word_io": b"\x34\x12\xa5"})
            dev = Device(label="word_io", bus=IF)
            base = eng.get("seq_counter")
            eng.set(seq_counter=base + 1)

            def run_cycles():
                for _ in range(15):                               # 150 ms > 50 ms
                    time.sleep(0.01)
                    eng.set(cycle_count=eng.get("cycle_count") + 1)
                eng.set(seq_counter=base + 2)
            threading.Thread(target=run_cycles).start()
            self.assertEqual(dev.read_entry("inputs"), 0x1234)
            dev.close()

    def test_staged_writes_wait_for_transmit(self):
        with fake_engine(bus=IF) as eng:
            a = Device(label="word_io", bus=IF)
            b = Device(label="bit_io", bus=IF)
            a.stage_entry("outputs", 7)
            b.stage_entry("dout0", 1)
            self.assertEqual(eng.read_outputs("word_io"), b"\x00\x00")
            self.assertEqual(eng.read_outputs("bit_io"), b"\x00\x00")
            self.assertEqual(a.read_entry("outputs"), 0)         # nothing in shm yet
            Device.transmit_outputs()
            self.assertEqual(eng.read_outputs("word_io"), b"\x07\x00")
            self.assertEqual(eng.read_outputs("bit_io"), b"\x01\x00")
            with self.assertRaises(ValueError):
                a.stage_entry("outputs", 0x1_0000)               # out of range
            a.close()
            b.close()

    def test_stage_rxpdo_then_transmit(self):
        with fake_engine(bus=IF) as eng:
            dev = Device(label="word_io", bus=IF)
            dev.stage_rxpdo(0x1230)
            dev.stage_rxpdo_bit(0, 7, True)
            self.assertEqual(eng.read_outputs("word_io"), b"\x00\x00")
            Device.transmit_outputs()
            self.assertEqual(eng.read_outputs("word_io"), struct.pack("<H", 0x12b0))
            dev.stage_rxpdo_field(0, 0x00FF)     # modifies the live value now
            Device.transmit_outputs()
            self.assertEqual(eng.read_outputs("word_io"), b"\xff\x00")
            dev.close()

    def test_close_is_idempotent(self):
        with fake_engine(bus=IF):
            dev = Device(label="word_io", bus=IF)
            dev.close()
            dev.close()

    def test_input_only_slave(self):
        cfg = {"bus": IF, "slaves": [
            {"label": "in_only", "alias": 0, "position": 0, "vendor_id": 0,
             "product_code": 0,
             "txpdo": {"entries": [{"index": "0x6000", "subindex": 1,
                                    "type": "u8", "label": "sw"}]},
             "rxpdo": {"entries": []}}]}
        with fake_engine(cfg, IF) as eng:
            eng.publish(inputs={"in_only": b"\x2a"})
            dev = Device(label="in_only", bus=IF)
            self.assertEqual(dev.read_entry("sw"), 0x2a)
            self.assertEqual(dev.read_rxpdo(), ())
            with self.assertRaises(ValueError):
                dev.write_entry("sw", 1)
            dev.close()

    def test_output_only_slave(self):
        cfg = {"bus": IF, "slaves": [
            {"label": "out_only", "alias": 0, "position": 0, "vendor_id": 0,
             "product_code": 0,
             "txpdo": {"entries": []},
             "rxpdo": {"entries": [{"index": "0x7000", "subindex": 1,
                                     "type": "u16", "label": "outputs"}]}}]}
        with fake_engine(cfg, IF) as eng:
            dev = Device(label="out_only", bus=IF)
            self.assertEqual(dev.read_txpdo(), ())           # no inputs at all
            self.assertEqual(dev.read_rxpdo(), (0,))
            dev.write_entry("outputs", 0x0102)
            self.assertEqual(eng.read_outputs("out_only"), b"\x02\x01")
            self.assertEqual(dev.read_entry("outputs"), 0x0102)
            with self.assertRaises(KeyError):
                dev.write_entry("inputs", 1)                 # no such entry
            dev.close()

    def test_multi_pdo_slave(self):
        """Three input PDOs and 16 one-bit output PDOs, read and written
        through entries and through the joined struct formats."""
        def ent(idx, sub, label, **kw):
            return dict({"index": idx, "subindex": sub, "label": label}, **kw)
        cfg = {"bus": IF, "slaves": [
            {"label": "multi", "alias": 0, "position": 0, "vendor_id": 0,
             "product_code": 0,
             "txpdo": [
                 {"index": "0x1A00", "entries": [ent("0x6041", 0, "status", type="u16")]},
                 {"index": "0x1A01", "entries": [ent("0x6064", 0, "pos", type="i32")]},
                 {"index": "0x1A02", "entries": [ent("0x603F", 0, "error", type="u16")]}],
             "rxpdo": [{"index": f"0x{0x1600 + ch:04X}",
                        "entries": [ent(f"0x{0x7000 + 0x10 * ch:04X}", 1, f"do{ch}", bits=1)]}
                       for ch in range(16)]}]}
        with fake_engine(cfg, IF) as eng:
            eng.publish(inputs={"multi": struct.pack("<HiH", 0x1237, -5, 0x7500)})
            dev = Device(label="multi", bus=IF)
            self.assertEqual(dev.read_txpdo(), (0x1237, -5, 0x7500))
            self.assertEqual(dev.read_entry("pos"), -5)
            self.assertEqual(dev.read_entry("error"), 0x7500)
            dev.write_entry("do0", 1)
            dev.write_entry("do9", 1)
            self.assertEqual(eng.read_outputs("multi"), b"\x01\x02")
            self.assertEqual(dev.read_rxpdo(), (0x01, 0x02))
            dev.close()

    def test_repeated_staged_writes_last_wins(self):
        with fake_engine(bus=IF) as eng:
            a = Device(label="bit_io", bus=IF)
            b = Device(label="word_io", bus=IF)
            a.stage_entry("dout0", 1)
            a.stage_entry("dout0", 0)          # same entry twice: last value wins
            a.stage_entry("dout1", 1)          # and a neighbour in the same byte
            b.stage_entry("outputs", 7)
            Device.transmit_outputs()
            self.assertEqual(eng.read_outputs("bit_io"), b"\x02\x00")
            self.assertEqual(eng.read_outputs("word_io"), b"\x07\x00")
            self.assertEqual(a.read_entry("dout0"), 0)
            self.assertEqual(a.read_entry("dout1"), 1)
            a.close()
            b.close()

    def test_close_discards_untransmitted_staged_writes(self):
        with fake_engine(bus=IF) as eng:
            dev = Device(label="word_io", bus=IF)
            dev.stage_entry("outputs", 0xBEEF)
            dev.close()                      # close never auto-flushes staged data
            self.assertEqual(eng.read_outputs("word_io"), b"\x00\x00")


class DeviceLinkTests(unittest.TestCase):
    """DeviceLostError from the slave status table (al_state, op, drop_count)."""

    @staticmethod
    def _index(eng, label: str) -> int:
        return [s["label"] for s in eng.cfg["slaves"]].index(label)

    def test_no_error_while_in_op_or_before_first_refresh(self):
        with fake_engine(bus=IF) as eng:
            dev = Device(label="word_io", bus=IF)
            dev.read_txpdo()                                   # al_state 0: not refreshed yet
            eng.status(self._index(eng, "word_io"), 8)
            dev.write_rxpdo(1)
            self.assertEqual(dev.read_rxpdo(), (1,))
            dev.close()

    def test_out_of_op_raises_on_every_access(self):
        with fake_engine(bus=IF) as eng:
            i = self._index(eng, "word_io")
            eng.status(i, 8)
            dev = Device(label="word_io", bus=IF)
            eng.status(i, 4, operational=0, drop_count=1)      # SAFEOP, engine counted the drop
            with self.assertRaises(DeviceLostError) as cm:
                dev.read_txpdo()
            self.assertEqual(cm.exception.drops, 1)
            self.assertIn("word_io", str(cm.exception))
            for call in (dev.read_txpdo, lambda: dev.write_rxpdo(0),
                         lambda: dev.read_entry("outputs"),
                         lambda: dev.write_entry("outputs", 1)):
                with self.assertRaises(DeviceLostError) as cm:
                    call()                                     # still out of OP
                self.assertEqual(cm.exception.drops, 0)
            eng.status(i, 8, drop_count=1)                     # back in OP
            dev.write_rxpdo(2)
            self.assertEqual(dev.read_rxpdo(), (2,))
            dev.close()

    def test_short_drop_is_reported_once_even_if_already_back(self):
        with fake_engine(bus=IF) as eng:
            i = self._index(eng, "word_io")
            eng.status(i, 8, drop_count=5)                     # drops before the object existed
            dev = Device(label="word_io", bus=IF)
            dev.read_txpdo()                                   # not reported
            eng.status(i, 8, drop_count=7)                     # two drops between calls
            with self.assertRaises(DeviceLostError) as cm:
                dev.write_rxpdo(3)
            self.assertEqual(cm.exception.drops, 2)
            self.assertIn("back in OP", str(cm.exception))
            self.assertEqual(eng.read_outputs("word_io"), b"\x00\x00")   # write not done
            dev.write_rxpdo(3)                                 # reported once, then fine
            self.assertEqual(eng.read_outputs("word_io"), b"\x03\x00")
            dev.close()

    def test_offline_slave_raises_until_back(self):
        with fake_engine(bus=IF) as eng:
            i = self._index(eng, "word_io")
            eng.status(i, 8)
            dev = Device(label="word_io", bus=IF)
            eng.status(i, 0, online=0, operational=0, drop_count=1)   # cable out: no AL state
            with self.assertRaises(DeviceLostError) as cm:
                dev.read_txpdo()
            self.assertIn("now offline", str(cm.exception))
            with self.assertRaises(DeviceLostError) as cm:
                dev.read_txpdo()                               # still offline: keeps raising
            self.assertIn("is out of OP, offline", str(cm.exception))
            eng.status(i, 8, drop_count=1)
            dev.read_txpdo()
            dev.close()

    def test_slave_the_bus_started_without(self):
        # never reached OP (degraded start): no drop counted, the AL status
        # code says why, reads and writes raise until it reaches OP
        with fake_engine(bus=IF) as eng:
            i = self._index(eng, "word_io")
            eng.status(i, 0x14, operational=0, al_code=0x0027)   # SAFEOP + error
            dev = Device(label="word_io", bus=IF)
            for call in (dev.read_txpdo, lambda: dev.write_rxpdo(1)):
                with self.assertRaises(DeviceLostError) as cm:
                    call()
                self.assertEqual(cm.exception.drops, 0)
                self.assertEqual(cm.exception.al_code, 0x0027)
                self.assertIn("is out of OP, in SAFEOP + error, AL status 0x0027 "
                              "(Freerun not supported)", str(cm.exception))
            eng.status(i, 8)                                   # joins: no drop to report
            dev.write_rxpdo(1)
            self.assertEqual(eng.read_outputs("word_io"), b"\x01\x00")
            dev.close()

    def test_drop_count_wraps(self):
        with fake_engine(bus=IF) as eng:
            i = self._index(eng, "word_io")
            eng.status(i, 8, drop_count=0xFFFF)
            dev = Device(label="word_io", bus=IF)
            eng.status(i, 8, drop_count=0)
            with self.assertRaises(DeviceLostError) as cm:
                dev.read_txpdo()
            self.assertEqual(cm.exception.drops, 1)
            dev.close()



class EngineStoppedTests(unittest.TestCase):
    """A script must not carry on with frozen inputs once the engine is gone."""

    def _index(self, eng, label):
        return [s["label"] for s in eng.cfg["slaves"]].index(label)

    def test_clean_stop_is_seen_at_the_next_access(self):
        with fake_engine(bus=IF) as eng:
            eng.status(self._index(eng, "word_io"), 8)
            dev = Device(label="word_io", bus=IF)
            bus = Bus(IF)
            dev.read_txpdo()
            eng.set(bus_state=0)                      # the engine's shutdown mark
            for call in (dev.read_txpdo, lambda: dev.write_rxpdo(1)):
                with self.assertRaisesRegex(EngineStoppedError, "stopped"):
                    call()
            self.assertFalse(bus.is_op)
            self.assertFalse(bus.slaves_ok)
            dev.close()
            bus.close()

    def test_killed_or_hung_engine_is_seen_by_its_cycle_time(self):
        with fake_engine(bus=IF) as eng:
            eng.status(self._index(eng, "word_io"), 8)
            dev = Device(label="word_io", bus=IF)
            eng.publish(stalled_for_ns=150_000_000)   # within the margin
            dev.read_txpdo()
            eng.publish(stalled_for_ns=300_000_000)   # no cycle for 300 ms
            with self.assertRaisesRegex(EngineStoppedError, "has not run a cycle"):
                dev.read_txpdo()
            eng.publish()                             # it was only stalled
            dev.read_txpdo()
            dev.close()

    def test_no_attaching_to_a_region_left_by_a_dead_engine(self):
        with fake_engine(bus=IF) as eng:
            eng.publish(stalled_for_ns=1_000_000_000)
            with mock.patch("odycat._shm.pid_alive", return_value=False):
                for make in (lambda: Bus(IF), lambda: Device(label="word_io", bus=IF)):
                    with self.assertRaisesRegex(RuntimeError, "no longer running"):
                        make()
            with self.assertRaisesRegex(RuntimeError, "has not run a cycle"):
                Bus(IF)

    def test_attaching_from_a_container(self):
        """In its own PID namespace a script does not see the engine's
        process: fresh cycles say the engine runs; no cycles, that it is gone."""
        with fake_engine(bus=IF) as eng:
            eng.status(self._index(eng, "word_io"), 8)
            with mock.patch("odycat._shm.pid_alive", return_value=False):
                bus = Bus(IF)
                dev = Device(label="word_io", bus=IF)
                dev.read_txpdo()
                dev.close()
                bus.close()
                eng.publish(last_cycle_ns=0)            # still starting: not attached
                with self.assertRaisesRegex(RuntimeError, "from a container"):
                    Bus(IF)

if __name__ == "__main__":
    unittest.main()


