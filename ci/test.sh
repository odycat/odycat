#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Run the OdyCAT unit tests: no hardware, no IgH userland, no root, no network.
#
#   ./ci/test.sh                      every test (pytest if installed, else unittest)
#   ./ci/test.sh -v                   verbose
#   ./ci/test.sh tests.test_monitor   one module
#
# With pytest, the run also writes reports/junit.xml for CI test reports.
# Without pytest it falls back to the stdlib runner, which is how the tests are
# meant to work on a bare build host:
#
#   python3 -m unittest discover -s tests -t tests
#
# Set PYTHON=python to use a venv interpreter (what the CI does).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}

if "$PYTHON" -c 'import pytest' >/dev/null 2>&1; then
    echo "== pytest ($("$PYTHON" -m pytest --version 2>&1 | head -1))"
    mkdir -p reports
    if [ "$#" -gt 0 ]; then
        "$PYTHON" -m pytest tests -o junit_family=xunit2 --junitxml=reports/junit.xml "$@"
    else
        "$PYTHON" -m pytest tests -o junit_family=xunit2 --junitxml=reports/junit.xml
    fi
else
    echo "== $("$PYTHON" --version) unittest (no pytest: pip install -r requirements-dev.txt for JUnit XML)"
    if [ "$#" -gt 0 ]; then
        "$PYTHON" -m unittest "$@"
    else
        "$PYTHON" -m unittest discover -s tests -t tests -v
    fi
fi
