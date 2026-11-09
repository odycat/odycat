# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_signals.py
"""
Device variables (odycat.signal): reading is named after the thing
(io.in03()), without () it is the variable itself (for Events), writing
keeps its verb (io.set_out03(True)); using a variable as a value raises.
"""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import fake_engine, mini_config   # noqa: E402

from odycat import Events  # noqa: E402
from odycat.device import ObjectNotMappedError  # noqa: E402
from odycat.devices.amsamotion import EC3A_IO1632  # noqa: E402
from odycat.devices.beckhoff import EL1809, EL2809  # noqa: E402
from odycat.devices.delta import AX516AM, AX516AN  # noqa: E402
from odycat.devices.leadshine import ECT60  # noqa: E402
from odycat.signal import Signal  # noqa: E402
from odycat._profiles import device_classes, expand_slave   # noqa: E402
from odycat.signal import SignalSpec                         # noqa: E402

IF = "sig0"


def box(*devices):
    """A fake bus with these (device class, label) slaves, expanded from the classes."""
    return mini_config(*[expand_slave({"device": qualified(d), "position": i, "label": label})
                         for i, (d, label) in enumerate(devices)], bus=IF)


def qualified(model: str) -> str:
    """"vendor.Model" for a model name ("ECT60" -> "leadshine.ECT60")."""
    return next(k for k in device_classes() if k.endswith("." + model))


class DeclarationTests(unittest.TestCase):
    def test_every_declared_signal_is_in_the_class_profile(self):
        """A typo in an @signal index fails here, not on a machine."""
        for cls in device_classes().values():
            mapped = set()
            for key in ("txpdo", "rxpdo"):
                blk = cls.PROFILE.get(key) or []
                for pdo in blk if isinstance(blk, list) else [blk]:
                    for e in pdo.get("entries", []):
                        mapped.add((int(e["index"], 16), e["subindex"]))
            own = {n: v for n, v in vars(cls).items() if isinstance(v, SignalSpec)}
            for name, spec in own.items():
                self.assertIn((spec.index, spec.subindex), mapped,
                              f"{cls.__name__}.{name}: {spec.describe()} not in PROFILE")

    def test_point_methods_are_in_the_source(self):
        """Per-point methods are real class attributes (IDEs see them), named
        as printed on the device (input_<n>/output_<n> where only a number
        is printed)."""
        delta = [f"{c}{i}" for c in "lr" for i in range(8)]
        expected = {
            EC3A_IO1632: [f"in{n:02d}" for n in range(16)]
                         + [f"{p}out{n:02d}" for n in range(16) for p in ("", "set_")],
            AX516AM: delta,
            AX516AN: delta + [f"set_{n}" for n in delta],
            EL1809: [f"input_{n}" for n in range(1, 17)],
            EL2809: [f"{p}output_{n}" for n in range(1, 17) for p in ("", "set_")],
            ECT60: ["in3", "in4", "in5", "in6", "out1", "out2", "set_out1", "set_out2"],
        }
        for cls, names in expected.items():
            for name in names:
                self.assertIn(name, vars(cls), f"{cls.__name__}.{name}")
        self.assertNotIn("input_0", vars(EC3A_IO1632))
        self.assertNotIn("input_0", vars(AX516AM))
        self.assertFalse([n for c in device_classes().values() for n in vars(c)
                          if n.startswith("get_")])


class ReadTests(unittest.TestCase):
    def test_ec3a_by_name_and_number(self):
        with fake_engine(box(("EC3A_IO1632", "io")), IF) as eng:
            eng.publish(inputs={"io": struct.pack("<H", 0b1000_0000_0000_1001)})
            io = EC3A_IO1632(label="io", bus=IF)
            self.assertIs(io.in00(), True)
            self.assertIs(io.in03(), True)
            self.assertIs(io.in01(), False)
            self.assertEqual([io.input(n) for n in (0, 3, 15)], [True, True, True])
            self.assertEqual([io.input(n) for n in ("IN00", "in03", "IN15")], [True] * 3)
            self.assertEqual(io.inputs(), 0b1000_0000_0000_1001)
            io.set_out05(True)
            io.set_output("OUT07", True)
            self.assertTrue(io.out05() and io.output(7) and not io.out06())
            self.assertEqual(io.outputs(), (1 << 5) | (1 << 7))
            io.set_outputs([True] + [False] * 15)
            self.assertEqual(io.outputs(), 1)
            for bad in (16, "IN16", "OUT00", "X3"):
                with self.assertRaises(ValueError, msg=bad):
                    io.input(bad)
            io.close()

    def test_delta_one_bit_entries(self):
        with fake_engine(box(("AX516AM", "di"), ("AX516AN", "do")), IF) as eng:
            eng.publish(inputs={"di": struct.pack("<H", 1 << 9)})
            di = AX516AM(label="di", bus=IF)
            do = AX516AN(label="do", bus=IF)
            # bit 2n = L<n>, bit 2n+1 = R<n> (checked on the modules)
            self.assertTrue(di.r4() and di.input("R4") and not di.l4())
            self.assertEqual(di.inputs(), 1 << 9)
            do.set_l6(True)
            self.assertTrue(do.l6() and do.output("l6"))
            self.assertEqual(do.outputs(), 1 << 12)
            do.set_output("R0", True)
            self.assertEqual(do.outputs(), (1 << 12) | (1 << 1))
            with self.assertRaises(ValueError):
                do.set_output(3, True)                      # a name, not a number
            di.close()
            do.close()

    def test_beckhoff_numbering_from_1(self):
        with fake_engine(box(("EL1809", "di"), ("EL2809", "do")), IF) as eng:
            eng.publish(inputs={"di": struct.pack("<H", 1)})       # channel 1
            di = EL1809(label="di", bus=IF)
            do = EL2809(label="do", bus=IF)
            self.assertTrue(di.input_1() and di.input(1) and not di.input_2())
            do.set_output_16(True)
            self.assertTrue(do.output_16() and do.output(16))
            self.assertEqual(do.outputs(), 1 << 15)
            di.close()
            do.close()

    def test_drive_state_and_values(self):
        with fake_engine(box(("ECT60", "drv")), IF) as eng:
            sw = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 10)
            eng.publish(inputs={"drv": struct.pack("<HbiiIH", sw, 9, -1234, 500, 0b101, 0)})
            drv = ECT60(label="drv", bus=IF)
            self.assertEqual(drv.statusword(), sw)
            self.assertTrue(drv.is_operation_enabled() and drv.is_target_reached())
            self.assertFalse(drv.is_fault())
            self.assertEqual((drv.position_actual_value(), drv.velocity_actual_value(),
                              drv.mode_display()), (-1234, 500, 9))
            self.assertTrue(drv.negative_limit_switch() and drv.home_switch())
            self.assertFalse(drv.positive_limit_switch())
            drv.set_target_velocity(300)
            self.assertEqual(drv.target_velocity(), 300)
            with self.assertRaises(ObjectNotMappedError) as cm:
                drv.torque_actual_value()                 # the ECT60 has no 0x6077
            self.assertIn("0x6077", str(cm.exception))
            drv.close()

    def test_signals_find_objects_whatever_the_labels(self):
        """A config written from the bus (--no-profiles) names entries
        differently; signals go by object index."""
        sl = expand_slave({"device": "amsamotion.EC3A_IO1632", "position": 0, "label": "io"})
        sl["txpdo"]["entries"][0]["label"] = "in_gen_di"
        sl["rxpdo"]["entries"][0]["label"] = "out_gen_do"
        with fake_engine(mini_config(sl, bus=IF), IF) as eng:
            eng.publish(inputs={"io": struct.pack("<H", 1 << 4)})
            io = EC3A_IO1632(label="io", bus=IF)
            self.assertTrue(io.in04())
            io.close()


class GuardTests(unittest.TestCase):
    def test_a_variable_is_not_a_value(self):
        with fake_engine(box(("EC3A_IO1632", "io")), IF):
            io = EC3A_IO1632(label="io", bus=IF)
            for use in (lambda: bool(io.in03), lambda: io.in03 == True,  # noqa: E712
                        lambda: io.in03 != 0, lambda: io.in03 > 0,
                        lambda: int(io.in03), lambda: not io.in03):
                with self.assertRaises(TypeError) as cm:
                    use()
                self.assertIn("in03()", str(cm.exception))
            self.assertIsInstance(io.in03, Signal)
            self.assertEqual(io.in03, io.in03)              # same variable
            self.assertNotEqual(io.in03, io.in04)
            self.assertEqual(len({io.in03, io.in03, io.in04}), 2)
            self.assertIn("io.in03", repr(io.in03))
            io.close()


class EventsTests(unittest.TestCase):
    def test_watch_variables(self):
        with fake_engine(box(("EC3A_IO1632", "io"), ("ECT60", "drv")), IF) as eng:
            io = EC3A_IO1632(label="io", bus=IF)
            drv = ECT60(label="drv", bus=IF)
            ev = Events(IF)
            seen = []
            ev.rising(io.in03)(lambda e: seen.append(("in3", e.cycle, e.field.label)))
            ev.when(drv.position_actual_value, lambda v: v > 1000)(
                lambda e: seen.append(("far", e.cycle, e.value)))
            ev.rising(drv.is_fault)(lambda: seen.append(("fault",)))

            def cycle(word, pos, sw=0):
                eng.publish(inputs={"io": struct.pack("<H", word),
                                    "drv": struct.pack("<HbiiIH", sw, 0, pos, 0, 0, 0)})
            cycle(0, 0)
            cycle(1 << 3, 500)
            cycle(1 << 3, 1500, sw=1 << 3)
            ev.poll()
            self.assertEqual(seen, [("in3", 2, "in03"), ("far", 3, 1500), ("fault",)])
            ev.close()
            io.close()
            drv.close()

    def test_device_from_another_bus_is_refused(self):
        """A device of another engine (bus) would silently map to the
        slave at the same config position here: it must be refused."""
        with fake_engine(box(("EC3A_IO1632", "io")), IF):
            io = EC3A_IO1632(label="io", bus=IF)
            ev = Events(IF)
            io._bus_name = "other0"                    # as if created for another bus
            for register in (lambda: ev.rising(io.in03), lambda: ev.lost(io),
                             lambda: ev.change(io, "digital_inputs")):
                with self.assertRaises(ValueError) as cm:
                    register()
                self.assertIn("other0", str(cm.exception))
            io._bus_name = IF
            ev.rising(io.in03)(print)                # same bus: fine
            ev.close()
            io.close()

    def test_value_instead_of_variable(self):
        with fake_engine(box(("EC3A_IO1632", "io"), ("ECT60", "drv")), IF):
            io = EC3A_IO1632(label="io", bus=IF)
            drv = ECT60(label="drv", bus=IF)
            ev = Events(IF)
            with self.assertRaises(TypeError) as cm:
                ev.rising(io.in03())
            self.assertIn("without ()", str(cm.exception))
            with self.assertRaises(TypeError):
                ev.when(drv.position_actual_value(), lambda v: v > 1000)
            with self.assertRaises(KeyError):                     # not mapped on the ECT60
                ev.change(drv.torque_actual_value)
            with self.assertRaises(TypeError):
                ev.rising(io.in03, bit=2)
            ev.close()
            io.close()
            drv.close()



class DcTests(unittest.TestCase):
    """DC only when the config asks for it ("dc_sync"), and clear messages
    when it matters."""

    def drive(self, dc=None):
        entry = {"device": "leadshine.ECT60", "position": 0, "label": "drv"}
        if dc is not None:
            entry["dc_sync"] = dc
        return mini_config(expand_slave(entry), bus=IF)

    def test_cyclic_modes_need_dc(self):
        with fake_engine(self.drive(), IF):
            drv = ECT60(label="drv", bus=IF)
            for mode in ("CSP", "CSV", 10):
                with self.assertRaises(RuntimeError) as cm:
                    drv.set_mode(mode)
                self.assertIn('"dc_sync"', str(cm.exception))
                self.assertIn("'drv'", str(cm.exception))
            with self.assertRaises(RuntimeError) as cm:          # PP passes the DC check
                drv.set_mode("PP", timeout_s=0.01)               # (then the fake engine
            self.assertNotIn("dc_sync", str(cm.exception))       # does not answer)
            drv.close()

    def test_number_form_sets_the_sync0_period(self):
        with fake_engine(self.drive(10_000_000), IF):
            drv = ECT60(label="drv", bus=IF)
            self.assertEqual(drv._settle_cycles, 12)             # 2 + one 10 ms Sync0
            with self.assertRaises(RuntimeError) as cm:          # DC on: the check passes
                drv.set_mode("CSP", timeout_s=0.01)
            self.assertNotIn("dc_sync", str(cm.exception))
            drv.close()

    def test_drop_explains_dc_without_rt(self):
        for rt_flags, hinted in ((0, True), (3, False)):
            with fake_engine(self.drive(0), IF) as eng:
                drv = ECT60(label="drv", bus=IF)
                eng.set(rt_flags=rt_flags)
                eng.status(0, 4, operational=0, drop_count=1)
                with self.assertRaises(Exception) as cm:
                    drv.statusword()
                self.assertEqual("real-time scheduling" in str(cm.exception), hinted)
                drv.close()
        with fake_engine(self.drive(), IF) as eng:                 # free-run: no hint
            drv = ECT60(label="drv", bus=IF)
            eng.set(rt_flags=0)
            eng.status(0, 4, operational=0, drop_count=1)
            with self.assertRaises(Exception) as cm:
                drv.statusword()
            self.assertNotIn("real-time", str(cm.exception))
            drv.close()


if __name__ == "__main__":
    unittest.main()
