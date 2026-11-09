#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Engine checks for CI (and before a release, on a developer machine):
#   - the engine builds against the IgH userland with warnings as errors;
#   - gcc's static analyzer on the config parser, the code that reads its
#     input as root;
#   - cppcheck on every engine source (ci/cppcheck.cfg: what it cannot see);
#   - the config parser alone (engine/tests/config_dump) builds; the unit
#     tests use it (tests/test_engine_config.py).
#
#   IGH=<prefix> ci/check_engine.sh       IgH userland (ci/build_igh.sh); default /usr/local
#
# Needs gcc, make, libcjson-dev, and cppcheck (pip install cppcheck) for
# the last check, which is reported as SKIPPED without it.
set -euo pipefail
cd "$(dirname "$0")/.."
IGH=${IGH:-/usr/local}

echo "== engine, warnings as errors (IGH=$IGH)"
make -s -C engine clean
CFLAGS="-O2 -Werror" make -C engine IGH="$IGH"
engine/odycat --version

echo "== gcc -fanalyzer on the config parser"
gcc -std=c11 -fanalyzer -Wall -Wextra -Werror -c engine/config.c -o /dev/null

echo "== cppcheck"
if command -v cppcheck >/dev/null 2>&1; then
    cppcheck --version
    cppcheck --quiet --std=c11 --enable=warning,portability --library=ci/cppcheck.cfg \
        --inline-suppr --error-exitcode=1 -I engine -I "$IGH/include" \
        engine/*.c engine/tests/*.c
else
    echo "   SKIPPED (no cppcheck: pip install cppcheck)"
fi

echo "== config parser test tool"
CFLAGS="-O2 -Werror" make -C engine config-dump
