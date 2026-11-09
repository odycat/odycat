# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/_version.py
"""The OdyCAT version: the one place it is written. setup.py, the engine
build (engine/Makefile, compiled into the binary) and the docs read it from
here; a release tag (v<version>) must match it."""

__version__ = "1.0.0"

# End of the support period of this release, as YYYYMM (0 = not set):
# security fixes, within the major version, until at least then. Set when
# the release is made; the release workflow refuses a tag less than five
# years before it. Compiled into the engine (engine/Makefile).
SUPPORT_END = 0
