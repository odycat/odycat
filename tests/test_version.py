# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# tests/test_version.py
"""The version is written once (odycat/_version.py) and everything that
reports one reads it from there: the package, setup.py, the engine build and
the docs."""

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _support import REPO_ROOT  # noqa: E402

import odycat  # noqa: E402

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class VersionTests(unittest.TestCase):

    def test_the_package_exposes_a_semver_version(self):
        self.assertRegex(odycat.__version__, SEMVER)

    def test_nothing_else_writes_a_version_of_its_own(self):
        setup = (REPO_ROOT / "setup.py").read_text()
        self.assertNotRegex(setup, r'version\s*=\s*"')
        self.assertIn("_version.py", (REPO_ROOT / "engine" / "Makefile").read_text())
        self.assertIn("_version.py", (REPO_ROOT / "docs" / "conf.py").read_text())

    @unittest.skipIf(shutil.which("make") is None, "no make")
    def test_the_engine_build_uses_the_same_version(self):
        out = subprocess.run(["make", "-s", "-C", str(REPO_ROOT / "engine"), "version"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, odycat.__version__)


if __name__ == "__main__":
    unittest.main()
