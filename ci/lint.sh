#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Static checks that need no hardware and no IgH: byte-compile every module,
# keep the package free of test-only imports, and run ruff when it is installed.
#
#   ./ci/lint.sh
#   pip install -r requirements-dev.txt     # to get ruff; the script skips it if missing
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}

echo "== byte-compile every module"
# Every .py in the checkout, except virtualenvs and build output.
"$PYTHON" -m compileall -q -x '(^|/)(\.git|\.venv|venv|\.cache|build|dist|docs/_build)/' .

echo "== src/odycat/ must stay importable with only the standard library"
third_party=$("$PYTHON" - <<'PY'
import ast
import importlib.util
import pathlib
import sys

local = {"odycat", "_support"}


def is_stdlib(name: str) -> bool:
    """sys.stdlib_module_names exists on 3.10+; fall back to the module's origin."""
    if name in getattr(sys, "stdlib_module_names", ()):
        return True
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None or spec.origin is None:
        return False
    return spec.origin in ("built-in", "frozen") or spec.origin.startswith(sys.base_prefix)


bad = []
for p in sorted(pathlib.Path("src/odycat").rglob("*.py")):
    for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # 'from .device import ...' is local
                continue
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        for name in names:
            if name and name not in local and not is_stdlib(name):
                bad.append(f"{p}:{node.lineno}: imports {name}")
print("\n".join(bad))
PY
)
if [ -n "$third_party" ]; then
    echo "ERROR: src/odycat/ may not import third-party modules (install_requires is empty):"
    echo "$third_party"
    exit 1
fi

echo "== tests/ must not depend on pytest (unittest only)"
if grep -rn --include='*.py' -E '^\s*(import|from)\s+pytest' tests; then
    echo "ERROR: tests must run with 'python3 -m unittest discover' too"
    exit 1
fi

if "$PYTHON" -c 'import ruff' >/dev/null 2>&1; then
    echo "== ruff check"
    "$PYTHON" -m ruff check .
else
    echo "== ruff not installed, skipping 'ruff check'"
fi
