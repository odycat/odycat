# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_standard_io.py
"""
Generic standard-profile I/O classes: mdp.DigitalIO (ETG.5001 Modular
Device Profile) and cia401.GenericIO, on the fake engine. Channels are
found by object index, so one class fits very different layouts.
"""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import fake_engine, mini_config  # noqa: E402

from odycat import Events  # noqa: E402
from odycat.device import ObjectNotMappedError  # noqa: E402
from odycat.devices.cia401 import GenericIO  # noqa: E402
from odycat.devices.mdp import DigitalIO  # noqa: E402
from odycat._profiles import expand_slave  # noqa: E402

IF = "std0"


def profiled(device: str, label: str, position: int = 0) -> dict:
    return expand_slave({"device": device, "position": position, "label": label})


def slave(label: str, tx: list, rx: list, position: int = 0) -> dict:
    """A slave with these (index, subindex, type) entries."""
    def entries(items):
        return [{"index": f"0x{i:04X}", "subindex": s, "type": e, "label": f"o{i:04x}_{s}"}
                for i, s, e in items]
    return {"position": position, "label": label, "vendor_id": "0x1", "product_code": "0x2",
            "txpdo": {"index": "0x1A00", "entries": entries(tx)},
            "rxpdo": {"index": "0x1600", "entries": entries(rx)} if rx else {"entries": []}}


class MdpTests(unittest.TestCase):
    def test_one_object_per_channel(self):
        """Beckhoff EL1809: 0x6000:01, 0x6010:01, ... one bit each."""
        with fake_engine(mini_config(profiled("beckhoff.EL1809", "di"), bus=IF), IF) as eng:
            eng.publish(inputs={"di": struct.pack("<H", 0b1000_0000_0010_0001)})
            io = DigitalIO(label="di", bus=IF)
            self.assertEqual((io.n_inputs, io.n_outputs), (16, 0))
            self.assertEqual([io.input(n) for n in (0, 1, 5, 15)], [True, False, True, True])
            self.assertEqual(io.inputs(), 0b1000_0000_0010_0001)
            with self.assertRaises(ValueError):
                io.input(16)
            io.close()

    def test_word_entries(self):
        """EC3A-IO1632: one 16-bit word each way."""
        with fake_engine(mini_config(profiled("amsamotion.EC3A_IO1632", "io"),
                                     bus=IF), IF) as eng:
            eng.publish(inputs={"io": struct.pack("<H", 0x0104)})
            io = DigitalIO(label="io", bus=IF)
            self.assertEqual((io.n_inputs, io.n_outputs), (16, 16))
            self.assertTrue(io.input(2) and io.input(8) and not io.input(3))
            io.set_output(3, True)
            io.set_output(9, True)
            self.assertEqual(io.outputs(), (1 << 3) | (1 << 9))
            self.assertEqual(eng.read_outputs("io"), struct.pack("<H", (1 << 3) | (1 << 9)))
            io.set_outputs(0xF0F0)
            self.assertTrue(io.output(15) and not io.output(0))
            io.close()

    def test_one_bit_groups(self):
        """Delta AX-516AM: 0x6000:01..08 then 0x6001:01..08."""
        with fake_engine(mini_config(profiled("delta.AX516AM", "di"), bus=IF), IF) as eng:
            eng.publish(inputs={"di": struct.pack("<H", 1 << 9)})
            io = DigitalIO(label="di", bus=IF)
            self.assertEqual(io.n_inputs, 16)
            self.assertTrue(io.input(9))
            self.assertEqual(io.inputs(), 1 << 9)
            io.close()

    def test_signed_entries_are_not_channels(self):
        sl = slave("mix", [(0x6000, 1, "u16"), (0x6010, 0x11, "i16")], [])
        with fake_engine(mini_config(sl, bus=IF), IF):
            io = DigitalIO(label="mix", bus=IF)
            self.assertEqual(io.n_inputs, 16)                     # the analog INT16 left out
            io.close()

    def test_no_mdp_objects(self):
        sl = slave("other", [(0x2000, 0, "u16")], [(0x2300, 0, "u16")])
        with fake_engine(mini_config(sl, bus=IF), IF):
            with self.assertRaises(ValueError) as cm:
                DigitalIO(label="other", bus=IF)
            self.assertIn("0x6000", str(cm.exception))

    def test_channel_variables_for_events(self):
        with fake_engine(mini_config(profiled("amsamotion.EC3A_IO1632", "io"),
                                     bus=IF), IF) as eng:
            io = DigitalIO(label="io", bus=IF)
            ev = Events(IF)
            seen = []
            ev.rising(io.input_variable(3))(lambda e: seen.append((e.cycle, e.field.label)))
            for word in (0, 1 << 3, 1 << 3, 0):
                eng.publish(inputs={"io": struct.pack("<H", word)})
            ev.poll()
            self.assertEqual(seen, [(2, "input_3")])
            self.assertEqual(io.input_variable(3)(), False)
            ev.close()
            io.close()


class Cia401Tests(unittest.TestCase):
    def remote_io(self) -> dict:
        return slave("rio",
                     [(0x6000, 1, "u8"), (0x6000, 2, "u8"), (0x6100, 1, "u16"),   # 0x6100: same
                      (0x6401, 1, "i16"), (0x6401, 2, "i16")],                    # channels again
                     [(0x6200, 1, "u8"), (0x6411, 1, "i16")])

    def test_digital_and_analog(self):
        with fake_engine(mini_config(self.remote_io(), bus=IF), IF) as eng:
            eng.publish(inputs={"rio": struct.pack("<BBHhh", 0x01, 0x80, 0xFFFF, -120, 3000)})
            io = GenericIO(label="rio", bus=IF)
            self.assertEqual((io.n_inputs, io.n_outputs), (16, 8))  # 0x6000 family only
            self.assertTrue(io.input(0) and io.input(15) and not io.input(1))
            self.assertEqual(io.inputs(), 0x8001)
            self.assertEqual((io.analog_input(1), io.analog_input(2)), (-120, 3000))
            io.set_output(5, True)
            io.set_analog_output(1, -300)
            self.assertTrue(io.output(5))
            self.assertEqual(io.analog_output(1), -300)
            with self.assertRaises(ObjectNotMappedError):
                io.analog_input(3)
            with self.assertRaises(ObjectNotMappedError):
                io.set_analog_output(2, 1)
            io.close()

    def test_analog_variable_for_events(self):
        with fake_engine(mini_config(self.remote_io(), bus=IF), IF) as eng:
            io = GenericIO(label="rio", bus=IF)
            ev = Events(IF)
            seen = []
            ev.change(io.analog_input_variable(2), deadband=50)(lambda e: seen.append(e.value))
            for v in (0, 30, 100, 120, 400):
                eng.publish(inputs={"rio": struct.pack("<BBHhh", 0, 0, 0, 0, v)})
            ev.poll()
            self.assertEqual(seen, [100, 400])
            ev.close()
            io.close()

    def test_no_cia401_objects(self):
        sl = slave("other", [(0x2000, 0, "u16")], [])
        with fake_engine(mini_config(sl, bus=IF), IF):
            with self.assertRaises(ValueError):
                GenericIO(label="other", bus=IF)


if __name__ == "__main__":
    unittest.main()
