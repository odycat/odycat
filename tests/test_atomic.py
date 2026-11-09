# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_atomic.py
"""
odycat._atomic, the C helper behind odycat._shm.load_word / store_word:
64-bit acquire load and release store on an aligned word of a buffer.
Skipped where the package was built without it (pure-Python install).
"""

import mmap
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401

from odycat import _shm  # noqa: E402

try:
    from odycat import _atomic
except ImportError:
    _atomic = None


@unittest.skipIf(_atomic is None, "odycat._atomic not built")
class AtomicTests(unittest.TestCase):

    def setUp(self):
        self.mm = mmap.mmap(-1, 4096)

    def tearDown(self):
        self.mm.close()

    def test_store_then_load_round_trips_every_bit(self):
        for value in (0, 1, 0x0123456789ABCDEF, 2**64 - 1):
            _atomic.store_release_u64(self.mm, 64, value)
            self.assertEqual(_atomic.load_acquire_u64(self.mm, 64), value)
            self.assertEqual(int.from_bytes(self.mm[64:72], sys.byteorder), value)

    def test_the_package_seam_uses_it(self):
        _shm.store_word(self.mm, 128, 0xA5A5)
        self.assertEqual(_shm.load_word(self.mm, 128), 0xA5A5)

    def test_misaligned_or_outside_offsets_are_refused(self):
        for off in (3, -8, 4096 - 4, 4096):
            with self.subTest(off=off), self.assertRaises(ValueError):
                _atomic.store_release_u64(self.mm, off, 1)
            with self.subTest(off=off), self.assertRaises(ValueError):
                _atomic.load_acquire_u64(self.mm, off)

    def test_values_outside_u64_are_refused(self):
        for value in (-1, 2**64):
            with self.subTest(value=value), self.assertRaises(OverflowError):
                _atomic.store_release_u64(self.mm, 0, value)

    def test_a_read_only_buffer_is_refused_for_stores(self):
        with self.assertRaises((BufferError, TypeError)):
            _atomic.store_release_u64(bytes(16), 0, 1)


if __name__ == "__main__":
    unittest.main()
