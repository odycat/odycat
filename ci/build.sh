#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Build checks for CI. The engine needs the IgH userland and libcjson-dev;
# on a runner without them the engine build is reported as SKIPPED, so a plain
# runner still gets a useful job.
#
#   ./ci/build.sh             C engine (when possible)
#   ./ci/build.sh --wheel     only dist/odycat-*.whl, the pure-Python one (needs
#                             no IgH, no compiler and no make)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}

# --- Wheel (the same steps as 'make wheel') --------------------------------
if [ "${1:-}" = "--wheel" ]; then
    rm -rf build ./*.egg-info src/*.egg-info dist/odycat-*.whl
    # The pure wheel: CI runners have no compiler for odycat._atomic
    ODYCAT_PURE=1 "$PYTHON" -m pip wheel . --no-deps --no-cache-dir -w dist
    ls dist/odycat-*.whl
    exit 0
fi

# --- C engine (engine/Makefile checks the dependencies) --------------------
if ! command -v make >/dev/null 2>&1; then
    echo "== engine: SKIPPED (no make on this runner)"
elif missing=$(make -s -C engine deps 2>&1); then
    make -C engine
    echo "   -> engine/odycat"
else
    echo "== engine: SKIPPED ($missing)"
fi
