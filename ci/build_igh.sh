#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Builds the IgH EtherCAT Master userland (ecrt.h and libethercat, no kernel
# modules, no tool) into a prefix: what CI needs to compile and check the
# engine. A machine that runs a bus installs IgH in full instead
# (docs/getting-started.md).
#
#   ci/build_igh.sh [PREFIX]        default: ~/.cache/igh-<IgH version>
#
# Needs git, autoconf, automake, libtool, make and a C compiler.
set -euo pipefail

# The pin shared with the installer (install/versions.env): IGH_VERSION,
# IGH_COMMIT, IGH_REPO.
. "$(dirname "$0")/../install/versions.env"
PREFIX=${1:-$HOME/.cache/igh-$IGH_VERSION}

if [ -f "$PREFIX/include/ecrt.h" ] && ls "$PREFIX"/lib/libethercat.so* >/dev/null 2>&1; then
    echo "== IgH $IGH_VERSION userland already in $PREFIX"
    exit 0
fi

SRC=$(mktemp -d)
trap 'rm -rf "$SRC"' EXIT
echo "== IgH $IGH_VERSION userland -> $PREFIX"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$IGH_VERSION" "$IGH_REPO" "$SRC"
cd "$SRC"
got=$(git rev-parse HEAD)
[ "$got" = "$IGH_COMMIT" ] || { echo "IgH $IGH_VERSION is commit $got, not the pinned $IGH_COMMIT" >&2; exit 1; }
./bootstrap
# no systemd unit: CI machines with systemd would install it outside PREFIX
./configure --prefix="$PREFIX" --disable-kernel --disable-tool --with-systemdsystemunitdir=no
make -j"$(nproc)"
make install
ls "$PREFIX/include/ecrt.h" "$PREFIX"/lib/libethercat.so*
