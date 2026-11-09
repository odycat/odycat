# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""PDO layout rules: odycat._config against an independent model of the engine.

The engine (pdo_encoding_bits in config.c, walk_pdo_chunks in layout.c) and
odycat/_config.py must agree byte for byte: if they disagree, the Python struct formats and the
shm offsets shift every PDO by one byte and the bus still "works" while the
values are garbage. These tests compare the package against pdo_chunks /
pdo_bytes in tests/_support.py, which reimplements the engine rules straight
from the C source, and against the invariant the bench scripts rely on:

    struct.calcsize(pdo_format_from_entries(block)) == pdo_size_from_entries(block)
"""

import struct
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import (TYPE_BITS, SHM_HEADER_SIZE, align_up_8, bit_slave,  # noqa: E402
                      direction_entries,
                      mirrored_chunks, pdo_bytes, repo_configs,
                      slave_bytes)

from odycat import _config  # noqa: E402


def entry(index="0x6000", sub=1, bits=None, typ=None, label=None):
    d = {"index": index, "subindex": sub}
    if typ is not None:
        d["type"] = typ
    if bits is not None:
        d["bits"] = bits
    if label is not None:
        d["label"] = label
    return d


def bits_entries(n, **kw):
    return [entry(sub=i, bits=1, **kw) for i in range(1, n + 1)]


# name -> (entries, expected struct format)
CASES = [
    ("single u16", [entry(typ="u16")], "=H"),
    ("two u16", [entry(typ="u16"), entry("0x6001", typ="u16")], "=HH"),
    ("u8 + s8", [entry("0x6000", 1, typ="u8"), entry("0x6000", 2, typ="i8")], "=Bb"),
    ("u32 + float", [entry("0x6000", 1, typ="u32"), entry("0x6001", 1, typ="f32")], "=If"),
    ("u64", [entry(typ="u64")], "=Q"),
    ("8 x 1-bit", bits_entries(8), "=B"),
    ("16 x 1-bit", bits_entries(16), "=BB"),
    ("8 x bool", [entry("0x6000", i, typ="bool") for i in range(1, 9)], "=????????"),
    ("4 x 1-bit + 4-bit", bits_entries(4) + [entry("0x6000", 9, bits=4)], "=B"),
    ("4 x 1-bit + u8 + 4-bit", bits_entries(4) + [entry("0x6000", 9, typ="i8"),
                                                   entry("0x6000", 10, bits=4)], "=BB"),
    ("1-bit then u16", bits_entries(1) + [entry("0x6001", 1, typ="u16")]
     + bits_entries(7), "=BBB"),
    ("gap run then 1-bit run", [entry("0x0000", 0, bits=8)] + bits_entries(8), "=B"),
    ("all gaps", [entry("0x0000", 0, bits=8), entry("0x0000", 0, bits=16)], ""),
    ("no entries", [], ""),
]


class TypeTableTests(unittest.TestCase):
    def test_table_matches_the_engine(self):
        """odycat._config.TYPE_BITS, odycat.testing.TYPE_BITS and the engine's
        VALUE_TYPES table name the same types with the same sizes."""
        for name, size in TYPE_BITS.items():
            self.assertEqual(_config._entry_bits({"type": name}), size, name)
        self.assertEqual(_config.TYPE_BITS, TYPE_BITS)
        engine = (Path(__file__).resolve().parents[1] / "engine" / "config.c").read_text()
        table = engine[engine.index("VALUE_TYPES[] = {"):]
        table = table[:table.index("};")]
        found = {n: 8 * int(b) for n, b in re.findall(r'\{"(\w+)",\s*(\d+),', table)}
        self.assertEqual(found, TYPE_BITS)

    def test_unknown_type_rejected(self):
        for bad in ("H", "uint8", "u12", "", "int"):
            with self.assertRaises(ValueError, msg=bad):
                _config._entry_bits({"index": "0x6000", "type": bad})

    def test_a_whole_size_value_needs_a_type(self):
        """One way to say a 16-bit value: "type": "u16", not "bits": 16."""
        for bits in (8, 16, 32, 64):
            with self.assertRaises(ValueError, msg=bits):
                _config._entry_bits({"index": "0x6000", "bits": bits})
        self.assertEqual(_config._entry_bits({"index": "0x0000", "bits": 16}), 16)   # gap
        self.assertEqual(_config._entry_bits({"index": "0x6000", "bits": 12}), 12)   # bit field

    def test_repo_entries_have_a_type_or_bits(self):
        for rel, cfg in repo_configs():
            for sl in cfg["slaves"]:
                for key in ("txpdo", "rxpdo"):
                    for ent in direction_entries(sl, key):
                        with self.subTest(f"{rel} {sl.get('label')} {ent.get('label')}"):
                            self.assertNotIn("encoding", ent)
                            _config._entry_bits(ent)

class ChunkRulesTests(unittest.TestCase):
    def test_chunks_match_engine_model(self):
        for name, entries, _ in CASES:
            with self.subTest(name):
                mine = list(_config._pdo_chunks({"entries": entries}))
                theirs = mirrored_chunks(entries)
                self.assertEqual([len(r) for r, _ in mine],
                                 [last - first + 1 for first, last, _ in theirs])
                self.assertEqual([bits for _, bits in mine], [bits for _, _, bits in theirs])

    def test_size_and_format_agree(self):
        for name, entries, fmt in CASES:
            with self.subTest(name):
                pdo = {"entries": entries}
                got = _config.pdo_format_from_entries(pdo)
                self.assertEqual(got, fmt)
                size = _config.pdo_size_from_entries(pdo)
                self.assertEqual(struct.calcsize(got) if got else 0, size)
                self.assertEqual(size, pdo_bytes(entries))

    def test_trailing_bits_rejected(self):
        """A block must end on a byte boundary, like the engine's die()."""
        pdo = {"entries": bits_entries(3)}
        with self.assertRaises(ValueError) as cm:
            _config.pdo_size_from_entries(pdo)
        self.assertIn("byte boundary", str(cm.exception))
        with self.assertRaises(ValueError):
            _config.pdo_format_from_entries(pdo)

    def test_gap_only_run_not_mirrored(self):
        """Padding-only runs cost no shm bytes but do not shift the bit cursor."""
        pdo = {"entries": [entry("0x0000", 0, bits=8), entry("0x6000", 1, bits=4),
                           entry("0x6000", 2, bits=4)]}
        self.assertEqual(_config.pdo_size_from_entries(pdo), 1)
        fields, used = _config.pdo_entry_layout(pdo)
        self.assertEqual(used, 8)
        self.assertEqual([f["label"] for f in fields], ["0x6000:1", "0x6000:2"])
        self.assertEqual([f["bit_offset"] for f in fields], [0, 4])

    def test_a_wide_bit_field_is_raw_bytes_and_decodes_unsigned(self):
        """A 24-bit field (no type has that size) is three raw bytes in the
        struct format and decodes as an unsigned bit field."""
        pdo = {"entries": [entry(bits=24)]}
        self.assertEqual(_config.pdo_format_from_entries(pdo), "=BBB")
        field = _config.pdo_entry_layout(pdo)[0][0]
        self.assertEqual(_config.decode_entry(b"\x01\x02\x83", field), 0x830201)

    def test_repo_directions_size_format_agree(self):
        """In every repo config: struct format size == derived shm size."""
        for rel, cfg in repo_configs():
            for sl in cfg["slaves"]:
                for direction, size in zip(("in", "out"), slave_bytes(sl)):
                    with self.subTest(f"{rel} {sl.get('label')} {direction}"):
                        fmt = _config.direction_format(sl, direction)
                        self.assertEqual(struct.calcsize(fmt) if fmt else 0, size)
                        self.assertEqual(_config.slave_pdo_sizes(sl),
                                         slave_bytes(sl))


class EntryLayoutTests(unittest.TestCase):
    def test_bit_offsets_of_a_packed_block(self):
        inputs, outputs = _config.slave_entry_layout(bit_slave("bit_io", 1))
        self.assertEqual([(f["label"], f["bits"], f["bit_offset"]) for f in inputs],
                         [("din0", 1, 0), ("din1", 1, 1), ("din2", 1, 2), ("din3", 1, 3),
                          ("byte_in", 8, 4), ("nibble_in", 4, 12), ("word_in", 16, 16)])
        # Outputs are block-relative too: they start after the 4 input bytes.
        self.assertEqual([(f["label"], f["bits"], f["bit_offset"]) for f in outputs][:5],
                         [("dout0", 1, 32), ("dout1", 1, 33), ("dout2", 1, 34),
                          ("dout3", 1, 35), ("preset", 4, 36)])
        last = outputs[-1]
        self.assertEqual(last["label"], "dout11")      # gap run costs no cursor
        self.assertEqual(last["bit_offset"], 47)
        self.assertEqual(last["bit_offset"] + last["bits"], 48)   # 4 tx + 2 rx bytes

    def test_layout_covers_exactly_the_shm_size(self):
        """Entry bit offsets are block-relative: outputs follow the inputs."""
        for rel, cfg in repo_configs():
            for sl in cfg["slaves"]:
                with self.subTest(f"{rel} {sl.get('label')}"):
                    tx, rx = slave_bytes(sl)
                    inputs, outputs = _config.slave_entry_layout(sl)
                    for fields, lo, hi in ((inputs, 0, tx * 8),
                                           (outputs, tx * 8, (tx + rx) * 8)):
                        for f in fields:
                            self.assertGreaterEqual(f["bit_offset"], lo)
                            self.assertLessEqual(f["bit_offset"] + f["bits"], hi)

    def test_layout_bits_equal_the_shm_size(self):
        """pdo_entry_layout() and pdo_size_from_entries() cannot drift apart."""
        for name, entries, _ in CASES:
            with self.subTest(name):
                _, used = _config.pdo_entry_layout({"entries": entries})
                self.assertEqual(used, 8 * _config.pdo_size_from_entries({"entries": entries}))

    def test_struct_path_only_for_byte_aligned_entries(self):
        inputs, _ = _config.slave_entry_layout(bit_slave("bit_io", 1))
        by_label = {f["label"]: f for f in inputs}
        self.assertIsNotNone(_config._entry_struct(by_label["word_in"]))
        self.assertIsNone(_config._entry_struct(by_label["byte_in"]))  # bit_offset 4
        self.assertIsNone(_config._entry_struct(by_label["din0"]))     # 1 bit


if __name__ == "__main__":
    unittest.main()


def multi_slave(txpdo, rxpdo=None, **extra) -> dict:
    sl = {"position": 0, "label": "multi", "vendor_id": "0x1", "product_code": "0x2",
          "txpdo": txpdo, "rxpdo": rxpdo if rxpdo is not None else {"entries": []}}
    sl.update(extra)
    return sl


def one_bit_pdos(n, base=0x1A00, obj=0x6000):
    """n PDOs of a single 1-bit entry each, like 16-channel terminals."""
    return [{"index": f"0x{base + i:04X}",
             "entries": [entry(f"0x{obj + 0x10 * i:04X}", 1, bits=1, label=f"ch{i}")]}
            for i in range(n)]


class MultiPdoTests(unittest.TestCase):
    """txpdo/rxpdo as a list of PDOs; a direction is one run of entries."""

    AI = [{"index": f"0x{0x1A00 + ch:04X}", "entries": [
              entry(f"0x{0x6000 + 0x10 * ch:04X}", 1, bits=1, label=f"under{ch}"),
              entry(f"0x{0x6000 + 0x10 * ch:04X}", 2, bits=1, label=f"over{ch}"),
              entry("0x0000", 0, bits=6),
              entry(f"0x{0x6000 + 0x10 * ch:04X}", 17, typ="i16", label=f"value{ch}")]}
          for ch in range(4)] + [
          {"index": "0x1A10", "entries": [entry("0x6100", 1, typ="u16", label="status")]}]

    def test_more_than_two_pdos(self):
        sl = multi_slave(self.AI)
        pdos = _config.slave_pdos(sl, "in")
        self.assertEqual([n for n, _ in pdos],
                         ["txpdo[0]", "txpdo[1]", "txpdo[2]", "txpdo[3]", "txpdo[4]"])
        self.assertEqual(_config.slave_pdo_sizes(sl), (14, 0))
        self.assertEqual(_config.direction_format(sl, "in"),
                         "=BhBhBhBhH")
        self.assertEqual(_config.slave_pdo_sizes(sl), slave_bytes(sl))
        inputs, _ = _config.slave_entry_layout(sl)
        by_label = {f["label"]: f for f in inputs}
        self.assertEqual(by_label["value2"]["bit_offset"], 2 * 24 + 8)
        self.assertEqual(by_label["value2"]["block"], "txpdo[2]")
        self.assertEqual(by_label["status"]["bit_offset"], 4 * 24)
        self.assertEqual(by_label["status"]["block"], "txpdo[4]")

    def test_one_bit_per_pdo_packs_across_pdos(self):
        """Byte alignment is per direction: 16 PDOs of 1 bit are 2 bytes."""
        sl = multi_slave(one_bit_pdos(16), one_bit_pdos(16, 0x1600, 0x7000))
        self.assertEqual(_config.slave_pdo_sizes(sl), (2, 2))
        self.assertEqual(_config.slave_pdo_sizes(sl), slave_bytes(sl))
        self.assertEqual(_config.direction_format(sl, "in"), "=BB")
        inputs, outputs = _config.slave_entry_layout(sl)
        self.assertEqual([f["bit_offset"] for f in inputs], list(range(16)))
        self.assertEqual([f["bit_offset"] for f in outputs], list(range(16, 32)))
        self.assertEqual(inputs[9]["block"], "txpdo[9]")

    def test_unaligned_direction_rejected(self):
        sl = multi_slave(one_bit_pdos(3))
        with self.assertRaises(ValueError):
            _config.slave_pdo_sizes(sl)
        with self.assertRaises(ValueError):
            slave_bytes(sl)

    def test_pdos_without_entries_are_skipped(self):
        sl = multi_slave([{"index": "0x1A00", "entries": []},
                          {"index": "0x1A01", "entries": [entry(typ="u16")]}])
        self.assertEqual([n for n, _ in _config.slave_pdos(sl, "in")], ["txpdo[1]"])
        self.assertEqual(_config.slave_pdo_sizes(sl), (2, 0))

    def test_shm_offsets_follow_list_sizes(self):
        cfg = {"slaves": [multi_slave(self.AI),
                          dict(multi_slave({"entries": []}), label="next", position=1)]}
        # 14 bytes of inputs at 256 -> next slave at align8(270) = 272
        self.assertEqual(_config.resolve_slave_shm_offset(cfg, label="next"),
                         align_up_8(SHM_HEADER_SIZE + 14))

