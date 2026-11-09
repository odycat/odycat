# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Step 10: build tools, DKMS, kernel headers, libcjson, Python venv support.
say "Packages"

export DEBIAN_FRONTEND=noninteractive
KVER=$(uname -r)
pkgs=(build-essential autoconf automake libtool pkg-config git ca-certificates curl
      dkms libcjson-dev python3-dev python3-venv)

# Headers of the running kernel, and the headers metapackage of every kernel
# metapackage installed (linux-image-amd64 -> linux-headers-amd64,
# linux-image-generic -> linux-headers-generic, linux-image-current-sunxi64
# -> linux-headers-current-sunxi64...), so that DKMS also has the headers of
# the kernels that upgrades install later.
apt_has() { apt-cache show "$1" >/dev/null 2>&1; }
run apt-get update -qq
apt_has "linux-headers-$KVER" && pkgs+=("linux-headers-$KVER")
while read -r image; do
    apt_has "linux-headers-${image#linux-image-}" && pkgs+=("linux-headers-${image#linux-image-}")
done < <(dpkg-query -W -f='${Status} ${Package}\n' 'linux-image-*' 2>/dev/null |
         awk '$3 == "installed" && $4 !~ /^linux-image-[0-9]/ && $4 !~ /-dbg$/ {print $4}')

if [ "$WANT_RT" = 1 ]; then
    if [ "$DISTRO" = debian ]; then
        darch=$(dpkg --print-architecture)
        pkgs+=("linux-image-rt-$darch" "linux-headers-rt-$darch")
        info "--rt: Debian's PREEMPT_RT kernel ($darch); boot into it after the installer"
    elif [ "$DISTRO" = ubuntu ]; then
        warn "--rt: Ubuntu's real-time kernel comes with Ubuntu Pro (pro enable realtime-kernel); not installed"
    else
        warn "--rt: no packaged real-time kernel for $DISTRO; not installed (docs/realtime.md)"
    fi
fi

info "${pkgs[*]}"
run apt-get install -y -qq --no-install-recommends "${pkgs[@]}"

if [ "$DRY_RUN" = 0 ] && [ ! -d "/lib/modules/$KVER/build" ]; then
    die "no kernel headers for the running kernel $KVER. Upgrade the system (apt full-upgrade), reboot into the new kernel and run the installer again."
fi
