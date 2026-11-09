# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""pytest setup: make 'odycat' (src/) and the tests/_support helper
importable no matter which directory pytest is started from. The test modules
themselves stay dependency-free (plain unittest), so 'python -m unittest
discover -s tests -t tests' works too on a machine without pytest.
"""

import os
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

_paths = [TESTS_DIR]
if os.environ.get("ODYCAT_TEST_INSTALLED") != "1":     # see _support.py
    _paths.append(REPO_ROOT / "src")
for _p in _paths:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
