# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Helpers for this repository's unit tests: put src/ on sys.path
and load the configs shipped in the repository. The fake engine and the
mirrored layout rules are public, in odycat.testing; they are re-exported
here so the test modules import everything from one place.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
_SRC = REPO_ROOT / "src"                                   # 'odycat'
# ODYCAT_TEST_INSTALLED=1 tests the installed package (a built wheel, as
# the release workflow does) instead of src/.
INSTALLED = os.environ.get("ODYCAT_TEST_INSTALLED") == "1"
if not INSTALLED and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from odycat.testing import *  # noqa: E402,F401,F403
from odycat.testing import __all__ as _testing_all  # noqa: E402

if INSTALLED:
    import odycat  # noqa: E402
    if Path(odycat.__file__).resolve().is_relative_to(_SRC):
        raise ImportError(f"ODYCAT_TEST_INSTALLED=1, but odycat comes from {_SRC}")

__all__ = [*_testing_all, "REPO_ROOT", "load_config", "repo_configs"]

REPO_CONFIGS = ("examples/config.json", "examples/demo_box.json")


def load_config(relpath: str) -> dict:
    """Load a config file shipped in the repository (e.g. 'examples/demo_box.json')."""
    return json.loads((REPO_ROOT / relpath).read_text(encoding="utf-8"))


def repo_configs() -> list[tuple[str, dict]]:
    """Every config shipped in the repo, as the engine gets it (device
    profiles filled in, as `odycat-config expand` writes them)."""
    from odycat._profiles import expand_config
    return [(rel, expand_config(load_config(rel))) for rel in REPO_CONFIGS]
