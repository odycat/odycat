# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""The run directory and the checks on the regions Python attaches to.

The engine keeps its regions as files in its run directory ($ODYCAT_RUN_DIR,
else /run/odycat). odycat._config.open_shm refuses a region the engine did
not create: not a regular file, a symbolic link, open to all users, in a
directory others can write to, or owned by someone other than root or the
directory's owner. Otherwise another local user could plant a region and
feed a script fake inputs.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _support  # noqa: E402,F401  (puts src/ on the path)

from odycat import _config  # noqa: E402


class RunDirTests(unittest.TestCase):

    def test_default_and_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ODYCAT_RUN_DIR", None)
            self.assertEqual(_config.run_dir(), "/run/odycat")
            self.assertEqual(_config._data_shm_path("eth0"), "/run/odycat/eth0_shared")
        with mock.patch.dict(os.environ, {"ODYCAT_RUN_DIR": "/run/user/1000/odycat"}):
            self.assertEqual(_config._config_shm_path("eth0"),
                             "/run/user/1000/odycat/eth0_config")
            self.assertEqual(_config._trace_shm_path("eth0"),
                             "/run/user/1000/odycat/eth0_trace")

    def test_detect_bus_scans_the_run_dir(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"ODYCAT_RUN_DIR": d}):
            with self.assertRaisesRegex(RuntimeError, "no running engine detected"):
                _config.detect_bus()
            Path(d, "enp5s0_config").write_bytes(b"")
            self.assertEqual(_config.detect_bus(), "enp5s0")


class RegionCheckTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        os.chmod(self.dir, 0o750)
        self.region = self.dir / "eth0_shared"
        self.region.write_bytes(bytes(64))
        os.chmod(self.region, 0o660)

    def tearDown(self):
        self._tmp.cleanup()

    def open(self, path=None):
        fd = _config.open_shm(str(path or self.region), os.O_RDONLY)
        os.close(fd)

    def test_a_region_left_by_the_engine_opens(self):
        self.open()

    def test_a_region_open_to_all_users_is_refused(self):
        os.chmod(self.region, 0o664)
        with self.assertRaisesRegex(RuntimeError, "open to all users"):
            self.open()

    def test_a_symbolic_link_is_refused(self):
        link = self.dir / "eth1_shared"
        link.symlink_to(self.region)
        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            self.open(link)

    def test_a_run_dir_others_can_write_is_refused(self):
        os.chmod(self.dir, 0o770)
        with self.assertRaisesRegex(RuntimeError, "writable by other users"):
            self.open()

    def test_a_directory_is_refused(self):
        (self.dir / "eth2_shared").mkdir(mode=0o750)
        with self.assertRaises((RuntimeError, OSError)):
            self.open(self.dir / "eth2_shared")

    def test_a_region_owned_by_someone_else_is_refused(self):
        real_fstat = os.fstat

        def fstat_as_other_user(fd):
            st = real_fstat(fd)
            fields = list(st)
            fields[4] = st.st_uid + 4242                    # st_uid
            return os.stat_result(fields)

        with mock.patch.object(_config.os, "fstat", fstat_as_other_user):
            with self.assertRaisesRegex(RuntimeError, "not to the engine"):
                self.open()


if __name__ == "__main__":
    unittest.main()
