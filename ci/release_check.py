#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Checks the release workflow runs before anything is published.

    python3 ci/release_check.py tag vX.Y.Z [--date YYYY-MM-DD]
        The tag matches src/odycat/_version.py; CHANGELOG.md has a dated entry
        for the version; SUPPORT_END is at least five years after the date
        (default: today, UTC); SECURITY.md lists the release line.

    python3 ci/release_check.py dist DIR
        DIR holds the sdist, the pure wheel and one wheel per architecture,
        all of this version, and every wheel carries exactly the package
        files tracked in git (nothing left over from an old build).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tarfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_YEARS = 5                       # minimum support period (CRA)
ARCH_WHEELS = ("x86_64", "aarch64")


def fail(msg: str) -> None:
    print(f"ERROR: {msg}")
    sys.exit(1)


def version_info() -> tuple[str, int]:
    text = (ROOT / "src/odycat/_version.py").read_text()
    version = re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)
    support_end = int(re.search(r"^SUPPORT_END = (\d+)", text, re.M).group(1))
    return version, support_end


def check_tag(tag: str, day: date) -> None:
    version, support_end = version_info()
    if tag != f"v{version}":
        fail(f"tag {tag} does not match src/odycat/_version.py ({version}): tag v{version}")
    print(f"== tag {tag} matches the version")

    m = re.search(rf"^## \[{re.escape(version)}\] - (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    if not m:
        fail(f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' entry")
    try:
        dated = date.fromisoformat(m.group(1))
    except ValueError:
        fail(f"CHANGELOG.md entry for {version} is dated '{m.group(1)}': give the release date")
    if dated > day:
        fail(f"CHANGELOG.md dates {version} {dated}, after the release day {day}")
    print(f"== CHANGELOG.md: [{version}] - {dated}")

    need = (day.year + SUPPORT_YEARS) * 100 + day.month
    if support_end < need:
        fail(f"SUPPORT_END = {support_end} in src/odycat/_version.py: at least {need} "
             f"({SUPPORT_YEARS} years from {day:%Y-%m})")
    shown = f"{support_end // 100:04d}-{support_end % 100:02d}"
    print(f"== support until {shown}")

    major_minor = ".".join(version.split(".")[:2])
    security = (ROOT / "SECURITY.md").read_text()
    if f"{major_minor}.x" not in security or shown not in security:
        fail(f"SECURITY.md must list {major_minor}.x with its support end {shown}")
    print(f"== SECURITY.md lists {major_minor}.x until {shown}")


def check_dist(folder: Path) -> None:
    version, _ = version_info()
    tracked = subprocess.run(["git", "ls-files", "src/odycat"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.split()
    expected = {p.removeprefix("src/") for p in tracked if not p.endswith(".c")}

    sdists = list(folder.glob(f"odycat-{version}.tar.gz"))
    if len(sdists) != 1:
        fail(f"no sdist odycat-{version}.tar.gz in {folder}")
    with tarfile.open(sdists[0]) as t:
        names = {n.split("/", 1)[1] for n in t.getnames() if "/" in n}
    missing = {f"src/{p}" for p in expected} - names
    if missing:
        fail(f"sdist lacks {sorted(missing)[:5]}")
    print(f"== {sdists[0].name}")

    wheels = sorted(folder.glob(f"odycat-{version}-*.whl"))
    kinds = {"py3-none-any": None, **{a: None for a in ARCH_WHEELS}}
    for w in wheels:
        kind = "py3-none-any" if w.name.endswith("-py3-none-any.whl") else \
            next((a for a in ARCH_WHEELS if w.name.endswith(f"_{a}.whl")), None)
        if kind is None:
            fail(f"unexpected wheel {w.name}")
        if "abi3-manylinux" not in w.name and kind != "py3-none-any":
            fail(f"{w.name}: expected a cp311-abi3-manylinux wheel")
        with zipfile.ZipFile(w) as z:
            files = {n for n in z.namelist() if n.startswith("odycat/")}
        ext = {n for n in files if re.fullmatch(r"odycat/_atomic\.abi3\.so", n)}
        want = expected | ({"odycat/_atomic.abi3.so"} if kind != "py3-none-any" else set())
        if files != want:
            fail(f"{w.name}: extra {sorted(files - want)[:5]}, missing {sorted(want - files)[:5]}")
        if kind != "py3-none-any" and not ext:
            fail(f"{w.name} has no odycat._atomic")
        kinds[kind] = w.name
        print(f"== {w.name}: {len(files)} package files")
    absent = [k for k, v in kinds.items() if v is None]
    if absent:
        fail(f"missing wheels: {absent}")
    check_install_files(folder, version)


def check_install_files(folder: Path, version: str) -> None:
    """The engine binaries and the installer (install/install.sh)."""
    for arch in ARCH_WHEELS:
        name = f"odycat-engine-{version}-linux-{arch}"
        path = folder / f"{name}.tar.gz"
        if not path.exists():
            fail(f"no engine binary {path.name}")
        with tarfile.open(path) as t:
            names = set(t.getnames())
        if not {f"{name}/odycat", f"{name}/LICENSE"} <= names:
            fail(f"{path.name}: expected {name}/odycat and {name}/LICENSE, got {sorted(names)}")
        print(f"== {path.name}")

    name = f"odycat-install-{version}"     # the installer, with the files it installs
    path = folder / f"{name}.tar.gz"
    if not path.exists():
        fail(f"no installer {path.name}")
    with tarfile.open(path) as t:
        names = set(t.getnames())
        release_env = t.extractfile(f"{name}/release.env").read().decode()
    need = {f"{name}/{p}" for p in ("install.sh", "versions.env", "release.env", "lib/common.sh",
                                    "packaging/udev/99-ethercat.rules")}
    if not need <= names:
        fail(f"{path.name} lacks {sorted(need - names)}")
    if release_env.strip() != f"ODYCAT_VERSION={version}":
        fail(f"{path.name}: release.env says {release_env.strip()!r}")
    print(f"== {path.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tag")
    t.add_argument("tag")
    t.add_argument("--date", type=date.fromisoformat,
                   default=datetime.now(timezone.utc).date())
    d = sub.add_parser("dist")
    d.add_argument("folder", type=Path)
    args = ap.parse_args()
    if args.cmd == "tag":
        check_tag(args.tag, args.date)
    else:
        check_dist(args.folder)


if __name__ == "__main__":
    main()
