#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
#
# OdyCAT installer: the IgH EtherCAT Master (kernel modules through DKMS,
# userland in /usr/local) and the OdyCAT engine, on Debian 13, Ubuntu LTS
# (24.04, 26.04), Raspberry Pi OS and Armbian based on them; x86_64 and
# aarch64. The Python package goes into your own environment afterwards:
# the installer prints the command.
#
#   sudo ./install.sh [options]
#
#   --nic IFACE      network card for the bus (asked when not given)
#   --build-engine   build the engine here instead of downloading the release binary
#   --igh-only       IgH and the bus card only, no engine
#   --keep-igh       use the IgH already installed (not by this installer) as it is
#   --rt             also install the distribution's real-time kernel (Debian)
#   --yes            no questions (CI); needs --nic when several cards qualify
#   --dry-run        print what would be done, change nothing
#   --unsupported    run on a distribution or release not listed above
#
# Running it again repairs or upgrades: each step skips what is already done.
# Log: /var/log/odycat-install.log
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"
# shellcheck source=versions.env
. "$HERE/versions.env"

NIC='' BUILD_ENGINE=0 IGH_ONLY=0 KEEP_IGH=0 WANT_RT=0 ASSUME_YES=0 DRY_RUN=0 UNSUPPORTED=0
while [ $# -gt 0 ]; do
    case $1 in
        --nic) NIC=${2:?--nic needs an interface name}; shift ;;
        --build-engine) BUILD_ENGINE=1 ;;
        --igh-only) IGH_ONLY=1 ;;
        --keep-igh) KEEP_IGH=1 ;;
        --rt) WANT_RT=1 ;;
        --yes) ASSUME_YES=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --unsupported) UNSUPPORTED=1 ;;
        -h|--help) sed -n '5,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option $1 (see --help)" ;;
    esac
    shift
done
export NIC BUILD_ENGINE IGH_ONLY KEEP_IGH WANT_RT ASSUME_YES DRY_RUN

[ "$(id -u)" = 0 ] || die "run it with sudo: sudo $0 $*"

# The packaging files and the version: next to this script in a release
# tarball (release.env, packaging/), one level up in a repository checkout.
if [ -f "$HERE/release.env" ]; then
    # shellcheck source=/dev/null
    . "$HERE/release.env"                          # ODYCAT_VERSION
    PKG_DIR=$HERE/packaging
    SRC_DIR=
else
    SRC_DIR=$(cd "$HERE/.." && pwd)
    PKG_DIR=$SRC_DIR/packaging
    ODYCAT_VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC_DIR/src/odycat/_version.py")
fi
[ -n "${ODYCAT_VERSION:-}" ] || die "cannot tell which OdyCAT version this installer is for"
export HERE PKG_DIR SRC_DIR ODYCAT_VERSION

# ---------------------------------------------------------------- platform
. /etc/os-release
ARCH=$(uname -m)
DISTRO=unsupported
if [ -f /etc/armbian-release ]; then
    case "$ID:$VERSION_ID" in debian:13|ubuntu:24.04|ubuntu:26.04) DISTRO=armbian ;; esac
elif [ -f /etc/rpi-issue ]; then
    case "$ID:$VERSION_ID" in debian:13) DISTRO=raspios ;; esac
else
    case "$ID:$VERSION_ID" in
        debian:13) DISTRO=debian ;;
        ubuntu:24.04|ubuntu:26.04) DISTRO=ubuntu ;;
    esac
fi
case $ARCH in x86_64|aarch64) ;; *) DISTRO=unsupported ;; esac
if [ "$DISTRO" = unsupported ]; then
    [ "$UNSUPPORTED" = 1 ] || die "$PRETTY_NAME on $ARCH is not supported (Debian 13, Ubuntu 24.04/26.04 LTS, Raspberry Pi OS and Armbian based on them; x86_64, aarch64). --unsupported tries anyway."
    warn "$PRETTY_NAME on $ARCH is not supported: continuing because of --unsupported"
    DISTRO=${ID:-unknown}
fi
export ARCH DISTRO

if [ "$DRY_RUN" = 0 ]; then
    install -d -m 0755 "$STATE_DIR"
    exec > >(tee -a "$LOG_FILE") 2>&1
fi
say "OdyCAT $ODYCAT_VERSION installer, $(date -u '+%Y-%m-%d %H:%M UTC')"
info "$PRETTY_NAME, $ARCH, kernel $(uname -r); IgH $IGH_VERSION"
[ "$DRY_RUN" = 1 ] && info "dry run: nothing is changed"

for step in "$HERE"/steps/[0-9]*.sh; do
    # shellcheck source=/dev/null
    . "$step"
done
