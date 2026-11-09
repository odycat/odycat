# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Entry encode/decode: the bit maths that moves values in and out of PDO bytes.

_config.decode_entry() and Device._encode_entry() are the only place where a
PDO byte becomes a Python value, so a wrong shift shows up as "that device
reads a funny number" on real hardware. These tests drive them directly on a
plain bytearray laid out with the same rules the engine uses (see
test_pdo_layout.py), including entries that straddle a byte boundary.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import bit_slave, slave_bytes, word_slave  # noqa: E402

from odycat import _config  # noqa: E402
from odycat.device import Device  # noqa: E402

SIGNED = ("i8", "i16", "i32", "i64")


def sample(field, salt=1):
    """A value that must survive encode -> decode for this field."""
    bits, enc = field["bits"], field.get("type") or ""
    if bits == 1:
        return bool((salt + field["bit_offset"]) % 2)
    if enc == "f32":
        return 1.5
    if enc == "f64":
        return -2.25
    if enc in SIGNED and field["bit_offset"] % 8 == 0:
        return -3                       # signed only when struct-decoded
    return ((0xA5 << (salt + field["bit_offset"])) % (1 << bits)) or 1


def encode_all(slave):
    """(buffer, {label: value}, fields) with every entry written once."""
    inputs, outputs = _config.slave_entry_layout(slave)
    tx, rx = slave_bytes(slave)
    buf = bytearray(tx + rx)
    values = {}
    for field in inputs + outputs:
        value = sample(field, salt=len(field["label"]))
        Device._encode_entry(buf, field["bit_offset"], field, value)
        values[field["label"]] = value
    return buf, values, inputs + outputs


class RoundTripTests(unittest.TestCase):
    def test_every_entry_of_a_word_slave(self):
        buf, values, fields = encode_all(word_slave("word_io", 0))
        self.assertEqual(len(buf), 5)                    # 3 tx bytes + 2 rx bytes
        for field in fields:
            with self.subTest(field["label"]):
                self.assertEqual(_config.decode_entry(buf, field), values[field["label"]])

    def test_every_entry_of_a_bit_slave(self):
        buf, values, fields = encode_all(bit_slave("bit_io", 0))
        self.assertEqual(len(fields), 20)
        for field in fields:
            with self.subTest(field["label"]):
                self.assertEqual(_config.decode_entry(buf, field), values[field["label"]])

    def test_buffers_are_the_size_the_engine_mirrors(self):
        for slave in (word_slave("w", 0), bit_slave("b", 0)):
            tx, rx = slave_bytes(slave)
            buf, _, _ = encode_all(slave)
            self.assertEqual(len(buf), tx + rx)
            self.assertEqual(len(buf),
                             _config.pdo_size_from_entries(slave["txpdo"])
                             + _config.pdo_size_from_entries(slave["rxpdo"]))

    def test_narrow_write_keeps_its_neighbours(self):
        buf, values, fields = encode_all(bit_slave("bit_io", 0))
        before = bytes(buf)
        target = next(f for f in fields if f["label"] == "dout6")      # 1 bit wide
        Device._encode_entry(buf, target["bit_offset"], target,
                             not values[target["label"]])
        changed = [i for i, (a, b) in enumerate(zip(before, bytes(buf))) if a != b]
        self.assertEqual(changed, [target["bit_offset"] // 8])
        for field in fields:
            if field is target:
                continue
            with self.subTest(field["label"]):
                self.assertEqual(_config.decode_entry(buf, field), values[field["label"]])


def bits_pdo(*widths):
    return {"entries": [{"index": "0x6000", "subindex": i + 1, "bits": w}
                        for i, w in enumerate(widths)]}


class BitFieldTests(unittest.TestCase):
    def test_entry_straddling_a_byte(self):
        fields, used = _config.pdo_entry_layout(bits_pdo(6, 6, 4))
        self.assertEqual(used, 16)
        self.assertEqual([f["bit_offset"] for f in fields], [0, 6, 12])
        values = (0x3F, 0x2A, 0xF)
        buf = bytearray(2)
        for field, value in zip(fields, values):
            Device._encode_entry(buf, field["bit_offset"], field, value)
        self.assertEqual(bytes(buf), b"\xbf\xfa")
        for field, value in zip(fields, values):
            self.assertEqual(_config.decode_entry(buf, field), value)

    def test_unaligned_multibyte_entry_becomes_a_bit_field(self):
        """One leading bit pushes the 16-bit entry off a byte boundary."""
        pdo = {"entries": [{"index": "0x6000", "subindex": 1, "bits": 1},
                           {"index": "0x6001", "subindex": 1, "type": "u16"}]
               + [{"index": "0x6002", "subindex": i, "bits": 1} for i in range(1, 8)]}
        fields, _ = _config.pdo_entry_layout(pdo)
        word = fields[1]
        self.assertEqual(word["bit_offset"], 1)
        self.assertIsNone(_config._entry_struct(word))       # no struct fast path
        buf = bytearray(3)
        Device._encode_entry(buf, word["bit_offset"], word, 0xABCD)
        self.assertEqual(bytes(buf), b"\x9a\x57\x01")       # 0xABCD shifted left by 1
        self.assertEqual(_config.decode_entry(buf, word), 0xABCD)

    def test_signed_values_only_for_struct_decoded_entries(self):
        fields, used = _config.pdo_entry_layout({"entries": [
            {"index": "0x6000", "subindex": 1, "type": "i16"},
            {"index": "0x6000", "subindex": 2, "bits": 4},
            {"index": "0x6000", "subindex": 3, "bits": 12}]})
        self.assertEqual(used, 32)
        aligned, _nibble, packed = fields
        self.assertEqual([f["bit_offset"] for f in fields], [0, 16, 20])
        buf = bytearray(4)
        Device._encode_entry(buf, aligned["bit_offset"], aligned, -300)
        self.assertEqual(_config.decode_entry(buf, aligned), -300)
        self.assertEqual(bytes(buf[0:2]), b"\xd4\xfe")       # little-endian, like the engine
        with self.assertRaises(ValueError):                  # bit fields are unsigned
            Device._encode_entry(buf, packed["bit_offset"], packed, -1)
        Device._encode_entry(buf, packed["bit_offset"], packed, 0xFFF)
        self.assertEqual(_config.decode_entry(buf, packed), 0xFFF)
        self.assertEqual(_config.decode_entry(buf, aligned), -300)   # neighbours intact

    def test_out_of_range_and_bad_type(self):
        one, _seven, word = _config.pdo_entry_layout({"entries": bits_pdo(1, 7)["entries"] + [
            {"index": "0x6001", "subindex": 1, "type": "u16"}]})[0]
        buf = bytearray(4)
        for bad in (2, -1, 3):
            with self.subTest(bad):
                with self.assertRaises(ValueError) as cm:
                    Device._encode_entry(buf, one["bit_offset"], one, bad)
                self.assertIn("out of range", str(cm.exception))
        with self.assertRaises(ValueError) as cm:               # a u16 cannot hold it
            Device._encode_entry(buf, word["bit_offset"], word, 0x10000)
        self.assertIn("bad value 65536", str(cm.exception))
        byte = _config.pdo_entry_layout({"entries": [
            {"index": "0x6000", "subindex": 1, "type": "u8"}]})[0][0]
        with self.assertRaises(ValueError) as cm:
            Device._encode_entry(buf, byte["bit_offset"], byte, 999)
        self.assertIn("bad value", str(cm.exception))
        with self.assertRaises(ValueError):
            Device._encode_entry(buf, byte["bit_offset"], byte, "not a number")

    def test_decode_from_an_absolute_bit_base(self):
        """monitor.py decodes out of the whole shm image, not out of a block."""
        fields, _ = _config.pdo_entry_layout({"entries": [
            {"index": "0x6000", "subindex": 1, "type": "u8"},
            {"index": "0x6000", "subindex": 2, "type": "u16"}]})
        buf = bytes(100) + b"\x12\x34\x56\x78" + bytes(7)
        self.assertEqual([_config.decode_entry(buf, f, bit_base=800) for f in fields],
                         [0x12, 0x5634])


if __name__ == "__main__":
    unittest.main()

