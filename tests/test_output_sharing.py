# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_output_sharing.py
"""
Several writers on one device, and outputs cleared when a script ends.

- Writes change only the bits they target, under a cross-process lock, so
  two processes setting different bits of the same word never lose updates.
- A write fills a buffer the engine is not sending and publishes it with one
  store: the engine sends the outputs before the write or after it, never
  in between; a sync group is published in one store; a writer that dies
  before the store publishes nothing; a slave that left OP gets zeros
  until it is written again.
- Staged writes (transmit_outputs) only touch what was staged.
- When a script ends (normal exit, uncaught exception, SIGTERM) the outputs
  its Devices wrote go back to 0, other writers' bits are left alone, and
  keep_outputs_on_exit() opts out. These run a real child process against
  the fake engine's shm files.
"""

import multiprocessing
import os
import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import REPO_ROOT, fake_engine, mini_config  # noqa: E402

from odycat import Device, _shm  # noqa: E402
from odycat.device import DeviceLostError  # noqa: E402
from odycat.testing import bit_slave, word_slave  # noqa: E402

IF = "fake0"


def _count_in_nibble(shift: int, rounds: int, lost) -> None:
    """Child process: write a counter into its own 4 bits of the shared byte
    and read it straight back. Another writer's read-modify-write of the
    same byte, if not serialised, puts back a stale nibble: count those."""
    dev = Device(label="word_io", bus=IF)
    for i in range(rounds):
        v = i & 0xF
        for b in range(4):
            dev.set_rxpdo_bit(0, shift + b, bool(v >> b & 1))
        if (dev.read_rxpdo()[0] >> shift) & 0xF != v:
            lost.value += 1
    dev.keep_outputs_on_exit()          # multiprocessing children skip atexit anyway


class SharedWordTests(unittest.TestCase):

    def test_two_processes_on_one_byte_lose_no_updates(self):
        ctx = multiprocessing.get_context("fork")
        with fake_engine(bus=IF):
            lost = [ctx.Value("i", 0) for _ in range(2)]
            procs = [ctx.Process(target=_count_in_nibble, args=(shift, 5000, n))
                     for shift, n in zip((0, 4), lost)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(60)
                self.assertEqual(p.exitcode, 0)
            self.assertEqual([n.value for n in lost], [0, 0])

    def test_bit_and_field_writes_leave_other_bits_alone(self):
        with fake_engine(bus=IF) as eng:
            dev = Device(label="word_io", bus=IF)
            eng.write_outputs("word_io", (0x8001).to_bytes(2, "little"))   # another writer
            dev.set_rxpdo_bit(0, 4, True)
            self.assertEqual(eng.read_outputs("word_io"), (0x8011).to_bytes(2, "little"))
            dev.set_rxpdo_bit(0, 15, False)
            self.assertEqual(eng.read_outputs("word_io"), (0x0011).to_bytes(2, "little"))
            dev.keep_outputs_on_exit()
            dev.close()

    def test_entry_bit_writes_leave_other_bits_alone(self):
        with fake_engine(bus=IF) as eng:
            dev = Device(label="word_io", bus=IF)
            eng.write_outputs("word_io", (0x8001).to_bytes(2, "little"))   # another writer
            dev.write_entry_bit("outputs", 9, True)
            dev.write_entry_bit("outputs", 0, False)
            self.assertEqual(eng.read_outputs("word_io"), (0x8200).to_bytes(2, "little"))
            with self.assertRaises(ValueError):
                dev.write_entry_bit("outputs", 16, True)
            dev.keep_outputs_on_exit()
            dev.close()

    def test_staged_write_does_not_undo_other_writers(self):
        with fake_engine(bus=IF) as eng:
            dev = Device(label="word_io", bus=IF)
            dev.stage_rxpdo_bit(0, 2, True)
            eng.write_outputs("word_io", (0x0100).to_bytes(2, "little"))   # written meanwhile
            Device.transmit_outputs()
            self.assertEqual(eng.read_outputs("word_io"), (0x0104).to_bytes(2, "little"))
            dev.keep_outputs_on_exit()
            dev.close()

    def test_close_clears_only_what_this_object_wrote(self):
        with fake_engine(bus=IF) as eng:
            dev = Device(label="word_io", bus=IF)
            eng.write_outputs("word_io", (0x0080).to_bytes(2, "little"))   # another writer
            dev.write_entry("outputs", 0x0081)       # writes the whole 16-bit entry
            eng.write_outputs("word_io", (0x0181).to_bytes(2, "little"))   # other sets bit 8
            dev.close()
            self.assertEqual(eng.read_outputs("word_io"), b"\x00\x00")    # entry was all ours
            dev = Device(label="word_io", bus=IF)
            eng.write_outputs("word_io", (0x0180).to_bytes(2, "little"))
            dev.set_rxpdo_bit(0, 0, True)
            dev.close()
            self.assertEqual(eng.read_outputs("word_io"), (0x0180).to_bytes(2, "little"))


# Child script: point odycat at the fake engine's files, write, then end.
_CHILD = textwrap.dedent("""
    import importlib, os, signal, sys
    data, config, mode = sys.argv[1:4]
    import odycat
    for name in ("odycat._config", "odycat.bus", "odycat.device", "odycat.monitor"):
        mod = importlib.import_module(name)
        if hasattr(mod, "_data_shm_path"):
            mod._data_shm_path = lambda iface: data
        if hasattr(mod, "_config_shm_path"):
            mod._config_shm_path = lambda iface: config
    from odycat import Device
    dev = Device(label="word_io", bus="fake0")
    dev.set_rxpdo_bit(0, 0, True)
    dev.set_rxpdo_bit(0, 3, True)
    print("written", flush=True)
    if mode == "keep":
        dev.keep_outputs_on_exit()
    elif mode == "exception":
        raise RuntimeError("script failed")
    elif mode == "sigterm":
        os.kill(os.getpid(), signal.SIGTERM)
        signal.pause()
""")


class ExitCleanupTests(unittest.TestCase):

    def _run_child(self, eng, mode: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        if env.get("ODYCAT_TEST_INSTALLED") != "1":     # else the installed package (_support)
            env["PYTHONPATH"] = str(REPO_ROOT / "src")
        return subprocess.run(
            [sys.executable, "-c", _CHILD, str(eng.data_path), str(eng.config_path), mode],
            env=env, capture_output=True, text=True, timeout=30)

    def _check(self, mode: str, expect_word: int, expect_rc=None):
        with fake_engine(bus=IF) as eng:
            eng.write_outputs("word_io", (0x0100).to_bytes(2, "little"))   # another writer
            result = self._run_child(eng, mode)
            self.assertIn("written", result.stdout, result.stderr)
            if expect_rc is not None:
                self.assertEqual(result.returncode, expect_rc, result.stderr)
            word = int.from_bytes(eng.read_outputs("word_io"), "little")
            self.assertEqual(word, expect_word, f"{mode}: {word:#06x}")

    def test_normal_exit_clears_written_outputs(self):
        self._check("normal", 0x0100, 0)

    def test_uncaught_exception_clears_written_outputs(self):
        self._check("exception", 0x0100, 1)

    def test_sigterm_clears_written_outputs(self):
        self._check("sigterm", 0x0100, 128 + 15)

    def test_keep_outputs_on_exit(self):
        self._check("keep", 0x0109, 0)


def _die_inside_the_lock(label: str) -> None:
    """Child process: take the group lock, scribble over a free buffer, and
    die without publishing (the kernel releases the lock)."""
    dev = Device(label=label, bus=IF)
    dev.keep_outputs_on_exit()
    with dev._group_lock():
        dev._merge_into_buffer(2, b"\xee\xee", b"\xff\xff")
        os._exit(1)


class PublishTests(unittest.TestCase):
    """Output buffers and sync groups: x and y in the sync group "xy",
    io in a group of its own."""

    def setUp(self):
        x, y = word_slave("x", 0), word_slave("y", 1)
        x["sync_group"] = y["sync_group"] = "xy"
        self._eng = fake_engine(mini_config(x, y, bit_slave("io", 2, alias=3),
                                            bus=IF), bus=IF)
        self.eng = self._eng.__enter__()
        self.devs = {k: Device(label=k, bus=IF) for k in ("x", "y", "io")}
        for d in self.devs.values():
            d.keep_outputs_on_exit()

    def tearDown(self):
        for d in self.devs.values():
            d.close()
        self._eng.__exit__(None, None, None)

    def buffer(self, label: str, idx: int) -> bytes:
        s = self.eng.slave(label)
        off = s["out"]["buf_offset"] + idx * s["out"]["stride"] + 8
        return bytes(self.eng._mm[off: off + s["rx"]])

    def test_the_package_finds_the_buffers_the_engine_placed(self):
        for label, dev in self.devs.items():
            out = self.eng.slave(label)["out"]
            self.assertEqual((dev._group, dev._member, dev._buf_off, dev._buf_stride),
                             (out["group"], out["member"], out["buf_offset"], out["stride"]))
        self.assertEqual(self.devs["x"]._group, self.devs["y"]._group)
        self.assertNotEqual(self.devs["x"]._group, self.devs["io"]._group)
        self.assertEqual((self.devs["x"]._member, self.devs["y"]._member), (0, 1))

    def test_a_write_fills_a_buffer_the_engine_is_not_sending(self):
        self.eng.publish()                                   # the engine sends buffer 0
        self.devs["x"].write_rxpdo(0x1234)
        idx = self.eng.published_index("x")
        self.assertNotEqual(idx, 0)
        self.assertEqual(self.buffer("x", 0), bytes(2))      # untouched
        self.assertEqual(self.eng.read_outputs("x"), b"\x34\x12")
        self.assertEqual(self.devs["x"].read_rxpdo(), (0x1234,))   # read back at once
        self.assertEqual(self.eng.read_sent("x"), bytes(2))  # sent from the next cycle
        self.eng.publish()
        self.assertEqual(self.eng.read_sent("x"), b"\x34\x12")

    def test_the_engine_never_sends_a_write_in_progress(self):
        self.devs["x"].write_rxpdo(0x1111)
        self.eng.publish()
        real = Device._merge_into_buffer

        def merge_then_cycle(dev, w, data, mask):
            real(dev, w, data, mask)
            self.eng.publish()                               # the engine runs mid-write
        with mock.patch.object(Device, "_merge_into_buffer", merge_then_cycle):
            self.devs["x"].write_rxpdo(0x2222)
        self.assertEqual(self.eng.read_sent("x"), b"\x11\x11")
        self.eng.publish()
        self.assertEqual(self.eng.read_sent("x"), b"\x22\x22")

    def test_a_sync_group_is_published_in_one_store(self):
        stores = []
        real = _shm.store_word

        def cycle_then_store(buf, offset, value):
            self.eng.publish()                               # just before the store
            stores.append((self.eng.read_sent("x"), self.eng.read_sent("y")))
            real(buf, offset, value)
        self.devs["x"].stage_rxpdo(0x0101)
        self.devs["y"].stage_rxpdo(0x0202)
        with mock.patch.object(_shm, "store_word", cycle_then_store):
            Device.transmit_outputs()
        self.assertEqual(stores, [(bytes(2), bytes(2))])     # one store, nothing sent before it
        self.eng.publish()
        self.assertEqual((self.eng.read_sent("x"), self.eng.read_sent("y")),
                         (b"\x01\x01", b"\x02\x02"))

    def test_devices_in_different_groups_are_published_group_by_group(self):
        with mock.patch.object(_shm, "store_word", wraps=_shm.store_word) as store:
            self.devs["x"].stage_rxpdo(1)
            self.devs["y"].stage_rxpdo(2)
            self.devs["io"].stage_entry("dout0", True)
            Device.transmit_outputs()
        self.assertEqual(store.call_count, 2)

    def test_the_buffer_the_engine_is_sending_is_never_reused(self):
        self.devs["x"].write_rxpdo(0x0001)
        g = self.eng.slave("x")["out"]["group"]
        sending = self.eng.claim_group(g) & 3                # the engine holds this one
        for v in (0x0002, 0x0003, 0x0004, 0x0005):
            self.devs["x"].write_rxpdo(v)
            self.assertNotEqual(self.eng.published_index("x"), sending)
        self.assertEqual(self.buffer("x", sending), b"\x01\x00")
        self.assertEqual(self.eng.read_outputs("x"), b"\x05\x00")

    def test_two_objects_of_one_slave_in_one_transmit(self):
        other = Device(label="x", bus=IF)
        other.keep_outputs_on_exit()
        self.devs["x"].stage_rxpdo_bit(0, 0, True)
        other.stage_rxpdo_bit(0, 15, True)
        Device.transmit_outputs()
        self.assertEqual(self.eng.read_outputs("x"), b"\x01\x80")
        other.close()

    def test_a_writer_that_fails_before_the_store_publishes_nothing(self):
        self.devs["x"].write_rxpdo(0x0102)
        with mock.patch.object(_shm, "store_word", side_effect=OSError("killed")):
            with self.assertRaises(OSError):
                self.devs["x"].write_rxpdo(0x0304)
        self.assertEqual(self.eng.read_outputs("x"), b"\x02\x01")
        self.devs["x"].write_rxpdo(0x0506)                   # the lock was released
        self.assertEqual(self.eng.read_outputs("x"), b"\x06\x05")

    def test_a_process_killed_holding_the_lock_publishes_nothing(self):
        self.devs["x"].write_rxpdo(0x0102)
        ctx = multiprocessing.get_context("fork")
        p = ctx.Process(target=_die_inside_the_lock, args=("x",))
        p.start()
        p.join(10)
        self.assertEqual(p.exitcode, 1)
        self.assertEqual(self.eng.read_outputs("x"), b"\x02\x01")
        done = threading.Event()

        def write():
            self.devs["y"].write_rxpdo(0x0708)               # same group, same lock
            done.set()
        th = threading.Thread(target=write, daemon=True)
        th.start()
        self.assertTrue(done.wait(5), "the dead process's lock was not released")
        self.assertEqual(self.eng.read_outputs("y"), b"\x08\x07")

    def test_a_slave_that_left_op_gets_zeros_until_written_again(self):
        x = self.devs["x"]
        x.write_rxpdo(0x8001)
        self.eng.publish()
        self.eng.status(0, 2, operational=0, drop_count=1)   # x leaves OP
        self.assertEqual(self.eng.read_outputs("x"), bytes(2))
        self.eng.publish()
        self.assertEqual(self.eng.read_sent("x"), bytes(2))
        self.assertEqual(x._published_outputs(), bytes(2))
        self.eng.status(0, 8, drop_count=1)                  # back in OP
        with self.assertRaises(DeviceLostError):             # once, for the drop
            x.set_rxpdo_bit(0, 0, True)
        x.set_rxpdo_bit(0, 0, True)                          # starts from zeros
        self.assertEqual(self.eng.read_outputs("x"), b"\x01\x00")


if __name__ == "__main__":
    unittest.main()
