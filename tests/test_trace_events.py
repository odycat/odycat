# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_trace_events.py
"""
Trace and Events against the fake engine's trace region.

The fake engine writes one slot per published cycle and engine events the
way trace_slot() / trace_event() in engine/trace.c do, so these tests run
the real reader and dispatcher code.
"""

import io
import struct
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import fake_engine, mini_config   # noqa: E402

from odycat import Device, Events  # noqa: E402
from odycat.events import MultiPoll, TraceOverrunError  # noqa: E402
from odycat.trace import Trace  # noqa: E402
from odycat import trace as tr                                  # noqa: E402

IF = "fake0"


def word(eng, value: int, status=None, cycles: int = 1) -> None:
    """One cycle with word_io's 16-bit input set to value."""
    eng.publish(inputs={"word_io": struct.pack("<Hb", value, 0)}, status=status,
                cycles=cycles)


def cfg_with(history_s: float) -> dict:
    cfg = mini_config(bus=IF)
    cfg["history_s"] = history_s
    return cfg


class TraceTests(unittest.TestCase):
    def test_one_slot_per_cycle(self):
        with fake_engine(bus=IF) as eng:
            t = Trace(IF)
            self.assertEqual(t.head, 0)
            for v in (1, 2, 3):
                word(eng, v)
            slots, lost = t.read(after=0)
            self.assertEqual(lost, 0)
            self.assertEqual([s.cycle for s in slots], [1, 2, 3])
            self.assertEqual([t.value(s, "word_io", "inputs") for s in slots], [1, 2, 3])
            self.assertEqual(t.read(after=3), ([], 0))
            self.assertEqual(t.history_s, 10.0)
            t.close()

    def test_outputs_and_bits(self):
        with fake_engine(bus=IF) as eng:
            t = Trace(IF)
            dev = Device(label="word_io", bus=IF)
            dev.write_entry("outputs", 0x8001)
            word(eng, 0b100)
            s = t.slot(1)
            self.assertEqual(t.value(s, "word_io", "outputs"), 0x8001)
            self.assertTrue(t.value(s, dev, "outputs", bit=15))
            self.assertFalse(t.value(s, "word_io", "inputs", bit=1))
            self.assertTrue(t.value(s, 0, "inputs", bit=2))
            self.assertIs(t.value(s, "bit_io", "din0"), False)
            dev.close()
            t.close()

    def test_field_errors(self):
        with fake_engine(bus=IF):
            t = Trace(IF)
            with self.assertRaises(KeyError) as cm:
                t.field("word_io", "nope")
            self.assertIn("status_byte", str(cm.exception))
            with self.assertRaises(KeyError):
                t.field("missing", "inputs")
            with self.assertRaises(ValueError):
                t.field("word_io", "inputs", bit=16)
            t.close()

    def test_lost_cycles_are_counted(self):
        with fake_engine(cfg_with(0.005), IF) as eng:         # 5 slots
            t = Trace(IF)
            self.assertEqual(t.slot_count, 5)
            for v in range(8):
                word(eng, v)
            slots, lost = t.read(after=0)
            self.assertEqual(lost, 3)
            self.assertEqual([s.cycle for s in slots], [4, 5, 6, 7, 8])
            self.assertIsNone(t.slot(3))
            self.assertEqual(t.oldest, 4)
            slots, lost = t.read(after=0, limit=2)
            self.assertEqual(([s.cycle for s in slots], lost), ([4, 5], 3))
            t.close()

    def test_not_in_op_bitmap(self):
        with fake_engine(bus=IF) as eng:
            t = Trace(IF)
            word(eng, 0)
            word(eng, 0, status=[(8, 1, 1), (4, 1, 0)])       # bit_io in SAFEOP
            s1, s2 = t.read(after=0)[0]
            self.assertTrue(s1.in_op(1))
            self.assertTrue(s2.in_op(0))
            self.assertFalse(s2.in_op(1))
            t.close()

    def test_engine_events(self):
        with fake_engine(bus=IF) as eng:
            t = Trace(IF)
            eng.engine_event(tr.ENGINE_START, arg=3, cycle=0)
            word(eng, 0)
            eng.engine_event(tr.SLAVE_LOST, slave=1, arg=4)
            events, lost = t.events()
            self.assertEqual(lost, 0)
            self.assertEqual([e.name for e in events], ["engine_start", "slave_lost"])
            self.assertIsNone(events[0].slave)
            self.assertEqual((events[1].slave, events[1].cycle, events[1].arg), (1, 1, 4))
            self.assertEqual(t.events(after=2), ([], 0))
            t.close()

    def test_skipped_cycles(self):
        """The engine was late by full periods: the counter in the trace header
        and a cycles_skipped event, which Events delivers like any engine event."""
        with fake_engine(bus=IF) as eng:
            t = Trace(IF)
            ev = Events(IF)
            seen = []
            ev.engine(tr.CYCLES_SKIPPED)(lambda e: seen.append((e.name, e.cycle, e.arg)))
            self.assertEqual(t.skipped_cycles, 0)
            word(eng, 0)
            eng.skip_cycles(3)
            word(eng, 0)
            eng.skip_cycles(2)
            word(eng, 0)
            self.assertEqual(t.skipped_cycles, 5)
            ev.poll()
            self.assertEqual(seen, [("cycles_skipped", 2, 3), ("cycles_skipped", 3, 2)])
            ev.close()
            t.close()

    def test_trace_off(self):
        with fake_engine(cfg_with(0), IF):
            with self.assertRaises(RuntimeError) as cm:
                Trace(IF)
            self.assertIn("history_s 0", str(cm.exception))


class EventsTests(unittest.TestCase):
    def test_edges_from_one_bit(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            seen = []
            ev.rising("word_io", "inputs", bit=0)(lambda e: seen.append(("up", e.cycle)))
            ev.falling("word_io", "inputs", bit=0)(lambda e: seen.append(("down", e.cycle)))
            word(eng, 1)                                  # baseline: already high
            self.assertEqual(ev.poll(), 0)
            for v in (0, 0, 1, 3, 2):
                word(eng, v)
            ev.poll()
            self.assertEqual(seen, [("down", 2), ("up", 4), ("down", 6)])
            self.assertEqual(ev.cycle, 6)
            ev.close()

    def test_change_with_deadband_and_event_fields(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            got = []

            @ev.change("word_io", "inputs", deadband=10)
            def moved(e):
                got.append(e)

            for v in (100, 105, 111, 115, 90):
                word(eng, v)
            ev.poll()
            self.assertEqual([(e.old, e.value) for e in got], [(100, 111), (111, 90)])
            e = got[0]
            self.assertEqual((e.cycle, e.slave, e.field.label), (3, "word_io", "inputs"))
            self.assertGreater(e.time_ns, 0)
            self.assertGreater(e.realtime_ns, 0)
            ev.close()

    def test_debounce_ignores_short_glitches(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            seen = []
            ev.rising("word_io", "inputs", bit=0, debounce=3)(lambda e: seen.append(e.cycle))
            for v in (0, 1, 1, 0, 1, 1, 1, 1):           # cycles 1..8
                word(eng, v)
            ev.poll()
            self.assertEqual(seen, [7])                   # held 3 cycles from cycle 5
            ev.close()

    def test_when_with_hold_and_rearm(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            seen = []
            ev.when("word_io", "inputs", lambda v: v > 50, hold=2)(lambda e: seen.append(e.cycle))
            for v in (0, 60, 10, 60, 70, 80, 0, 90, 90):  # cycles 1..9
                word(eng, v)
            ev.poll()
            self.assertEqual(seen, [5, 9])
            ev.close()

    def test_handler_without_argument(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            calls = []
            ev.change("word_io", "inputs")(lambda: calls.append(1))
            word(eng, 0)
            word(eng, 1)
            self.assertEqual(ev.poll(), 1)
            self.assertEqual(calls, [1])
            ev.close()

    def test_raise_keeps_the_rest_queued(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            seen = []

            @ev.change("word_io", "inputs")
            def handler(e):
                seen.append(e.value)
                if e.value == 2:
                    raise ValueError("boom")

            for v in (0, 1, 2, 3):
                word(eng, v)
            with self.assertRaises(ValueError):
                ev.poll()
            self.assertEqual(seen, [1, 2])
            ev.poll()
            self.assertEqual(seen, [1, 2, 3])
            ev.close()

    def test_log_and_stop(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF, poll_s=0.001)
            seen = []

            @ev.change("word_io", "inputs", on_error="log")
            def logged(e):
                seen.append(e.value)
                raise ValueError("logged")

            @ev.change("word_io", "status_byte", on_error="stop")
            def stopper(e):
                raise RuntimeError("stop here")

            word(eng, 0)
            word(eng, 1)
            eng.publish(inputs={"word_io": struct.pack("<Hb", 2, 5)})
            err = io.StringIO()
            with redirect_stderr(err):
                ev.run()                                  # returns: stopper stopped it
            self.assertEqual(seen, [1, 2])
            self.assertIn("logged", err.getvalue())
            self.assertIn("stop here", err.getvalue())
            ev.close()

    def test_device_loss_pauses_and_rebaselines(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            seen = []
            ev.change("bit_io", "word_in")(lambda e: seen.append(("change", e.value)))
            ev.lost("bit_io")(lambda e: seen.append(("lost", e.cycle)))
            ev.back("bit_io")(lambda e: seen.append(("back", e.cycle)))

            def bits(v, status=None):
                eng.publish(inputs={"bit_io": bytes([0, 0]) + struct.pack("<H", v)},
                            status=status)

            ok, safeop = [(8, 1, 1), (8, 1, 1)], [(8, 1, 1), (4, 1, 0)]
            bits(1, ok)                                   # 1: baseline
            bits(2, ok)                                   # 2: change
            bits(3, safeop)                               # 3: lost, paused
            bits(4, safeop)                               # 4
            bits(4, ok)                                   # 5: back, new baseline
            bits(5, ok)                                   # 6: change
            ev.poll()
            self.assertEqual(seen, [("change", 2), ("lost", 3), ("back", 5), ("change", 5)])
            ev.close()

    def test_overrun_raises_or_calls_handler(self):
        with fake_engine(cfg_with(0.005), IF) as eng:     # 5 slots
            ev = Events(IF)
            seen = []
            ev.change("word_io", "inputs")(lambda e: seen.append(e.value))
            for v in range(8):
                word(eng, v)
            with self.assertRaises(TraceOverrunError) as cm:
                ev.poll()
            self.assertEqual(cm.exception.overrun.cycles, 3)
            ev.poll()                                     # restarted from current values
            self.assertEqual(seen, [4, 5, 6, 7])          # slot 4 (value 3) is the new baseline

            overruns = []
            ev.overrun()(overruns.append)
            for v in range(20, 30):
                word(eng, v)
            ev.poll()
            self.assertEqual([o.cycles for o in overruns], [5])
            self.assertEqual(seen, [4, 5, 6, 7, 26, 27, 28, 29])
            ev.close()

    def test_engine_event_handlers_and_order(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            seen = []
            ev.engine(tr.WKC_WRONG)(lambda e: seen.append(("wkc", e.cycle)))
            ev.engine()(lambda e: seen.append((e.name, e.cycle)))
            ev.change("word_io", "inputs")(lambda e: seen.append(("change", e.cycle)))
            word(eng, 0)
            word(eng, 1)
            eng.engine_event(tr.WKC_WRONG, arg=1)         # in cycle 2
            ev.poll()
            self.assertEqual(seen, [("wkc", 2), ("wkc_wrong", 2), ("change", 2)])
            ev.close()

    def test_start_replays_cycles_and_engine_events(self):
        with fake_engine(bus=IF) as eng:
            eng.engine_event(tr.ENGINE_START, cycle=0)
            word(eng, 0)
            word(eng, 1)
            eng.engine_event(tr.WKC_WRONG, cycle=2)
            word(eng, 2)
            seen = []
            late = Events(IF)                              # from now on: nothing
            late.engine()(lambda e: seen.append(("late", e.name)))
            ev = Events(IF, start=2)                       # cycle 2 is the baseline
            ev.engine()(lambda e: seen.append((e.name, e.cycle)))
            ev.change("word_io", "inputs")(lambda e: seen.append(("change", e.cycle)))
            late.poll()
            ev.poll()
            self.assertEqual(seen, [("wkc_wrong", 2), ("change", 3)])
            ev.close()
            late.close()

    def test_bad_on_error_and_deadband_on_non_numeric(self):
        with fake_engine(bus=IF):
            ev = Events(IF)
            with self.assertRaises(ValueError):
                ev.change("word_io", "inputs", on_error="ignore")(print)
            ev.close()



class ScanStyleTests(unittest.TestCase):
    """ev.sleep() / ev.wait_cycles(): handlers run inside the wait at the end
    of the script's own loop, in its thread."""

    @staticmethod
    def engine_running(eng, values, period_s=0.001):
        """A thread publishing one cycle per period with the next value."""
        stop = threading.Event()

        def run():
            for v in values:
                if stop.is_set():
                    return
                word(eng, v)
                time.sleep(period_s)
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return stop, th

    def test_sleep_dispatches_while_waiting(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF, poll_s=0.001)
            seen = []
            ev.change("word_io", "inputs")(lambda e: seen.append(e.value))
            word(eng, 0)
            word(eng, 1)
            t0 = time.monotonic()
            ev.sleep(0.03)
            self.assertGreaterEqual(time.monotonic() - t0, 0.03)
            self.assertEqual(seen, [1])
            word(eng, 2)
            ev.sleep(0)                          # just dispatch
            self.assertEqual(seen, [1, 2])
            ev.close()

    def test_wait_cycles_counts_and_dispatches(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF, poll_s=0.001)
            seen = []
            ev.rising("word_io", "inputs", bit=0)(lambda e: seen.append(e.cycle))
            stop, th = self.engine_running(eng, [0, 1, 0, 1] + [0] * 50)
            try:
                start = eng.get("cycle_count")
                reached = ev.wait_cycles(10)
                self.assertGreaterEqual(reached - start, 10)
                self.assertEqual(len(seen), 2)   # both rising edges, none missed
            finally:
                stop.set()
                th.join()
            with self.assertRaises(RuntimeError):  # engine stopped: no cycles
                ev.wait_cycles(2, timeout_s=0.05)
            ev.close()

    def test_stop_ends_a_scan_loop(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF, poll_s=0.001)
            ev.rising("word_io", "inputs", bit=0)(lambda: ev.stop())
            word(eng, 0)
            loops = 0
            while not ev.stopped and loops < 100:
                loops += 1
                if loops == 3:
                    word(eng, 1)
                ev.sleep(0.001)
            self.assertTrue(ev.stopped)
            self.assertEqual(loops, 3)
            ev.close()

    def test_a_handler_cannot_wait_for_events(self):
        with fake_engine(bus=IF) as eng:
            ev = Events(IF)
            ev.change("word_io", "inputs")(lambda e: ev.sleep(0.01))
            word(eng, 0)
            word(eng, 1)
            with self.assertRaises(RuntimeError) as cm:
                ev.sleep(0)
            self.assertIn("inside a handler", str(cm.exception))
            ev.close()

    def test_one_dispatching_thread(self):
        with fake_engine(bus=IF):
            ev = Events(IF)
            ev.poll()                            # this thread dispatches now
            errors = []

            def other():
                try:
                    ev.poll()
                except RuntimeError as e:
                    errors.append(str(e))
            th = threading.Thread(target=other)
            th.start()
            th.join()
            self.assertEqual(len(errors), 1)
            self.assertIn("second thread", errors[0])
            ev.close()

    def test_warning_when_nothing_dispatches(self):
        with fake_engine(bus=IF):
            err = io.StringIO()
            with redirect_stderr(err):
                quiet = Events(IF, warn_after_s=0.05)
                quiet.change("word_io", "inputs")(print)
                quiet.poll()                     # dispatched: no warning
                ev = Events(IF, warn_after_s=0.05)
                ev.change("word_io", "inputs")(print)
                time.sleep(0.2)
            self.assertEqual(err.getvalue().count("OdyCAT events:"), 1)
            self.assertIn("ev.sleep", err.getvalue())
            ev.close()
            quiet.close()



class MultiPollTests(unittest.TestCase):
    """Several Events (one per bus) waited on together. The fake engine is a
    single bus, so two Events on it stand in for two buses."""

    def two(self, eng):
        a, b = Events(IF, poll_s=0.001), Events(IF, poll_s=0.002)
        seen = []
        a.change("word_io", "inputs")(lambda e: seen.append(("a", e.value)))
        b.rising("word_io", "inputs", bit=0)(lambda e: seen.append(("b", e.value)))
        word(eng, 0)
        return a, b, seen

    def test_sleep_dispatches_every_bus_in_order(self):
        with fake_engine(bus=IF) as eng:
            a, b, seen = self.two(eng)
            ev = MultiPoll(a, b)
            self.assertEqual(ev.poll_s, 0.001)
            ev.sleep(0)                                  # baselines
            word(eng, 1)
            t0 = time.monotonic()
            ev.sleep(0.02)
            self.assertGreaterEqual(time.monotonic() - t0, 0.02)
            self.assertEqual(seen, [("a", 1), ("b", True)])
            word(eng, 2)
            self.assertEqual(ev.poll(), 1)
            a.close()
            b.close()

    def test_stop_from_either_bus(self):
        with fake_engine(bus=IF) as eng:
            a, b, _ = self.two(eng)
            b.rising("word_io", "inputs", bit=1)(lambda: b.stop())
            ev = MultiPoll(a, b)
            ev.sleep(0)
            self.assertFalse(ev.stopped)
            word(eng, 2)
            loops = 0
            while not ev.stopped and loops < 50:
                loops += 1
                ev.sleep(0.001)
            self.assertTrue(ev.stopped and a.stopped is False and b.stopped)
            ev.stop()
            self.assertTrue(a.stopped)
            a.close()
            b.close()

    def test_no_wait_cycles_and_no_nesting(self):
        with fake_engine(bus=IF) as eng:
            a, b, _ = self.two(eng)
            ev = MultiPoll(a, b)
            with self.assertRaises(TypeError) as cm:
                ev.wait_cycles(1)
            self.assertIn("each bus counts its own cycles", str(cm.exception))
            b.change("word_io", "status_byte")(lambda e: ev.sleep(0))
            ev.sleep(0)
            eng.publish(inputs={"word_io": struct.pack("<Hb", 0, 3)})
            with self.assertRaises(RuntimeError) as cm:
                ev.sleep(0)
            self.assertIn("inside a handler", str(cm.exception))
            a.close()
            b.close()

    def test_arguments(self):
        with fake_engine(bus=IF):
            a = Events(IF)
            for bad in ((), (a, a), (a, "b")):
                with self.assertRaises((TypeError, ValueError)):
                    MultiPoll(*bad)
            a.close()


if __name__ == "__main__":
    unittest.main()
