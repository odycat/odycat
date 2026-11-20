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
[ -d "/lib/modules/$KVER/kernel" ] ||
    die "the running kernel $KVER is no longer installed: a kernel upgrade waits for a reboot. Reboot and run the installer again (nothing was installed)."
keep_headers=''
if apt_has "linux-headers-$KVER"; then
    pkgs+=("linux-headers-$KVER")
else
    # No headers package for this kernel version (Armbian): its headers come
    # from the flavour's metapackage, and a newer one replaces them. Install
    # it only at the version of the running kernel's package.
    image=$(dpkg -S "/boot/vmlinuz-$KVER" 2>/dev/null | sed -n 's/^\(linux-image-[^:,]*\).*/\1/p' | head -1 || true)
    if [ -n "$image" ]; then
        headers=linux-headers-${image#linux-image-}
        kernel_v=$(dpkg-query -W -f='${Version}' "$image")
        have_v=$(dpkg-query -W -f='${Status} ${Version}' "$headers" 2>/dev/null | awk '$3 == "installed" {print $4}' || true)
        repo_v=$(apt-cache policy "$headers" 2>/dev/null | sed -n 's/^ *Candidate: //p' || true)
        if [ "$have_v" = "$kernel_v" ]; then
            keep_headers=$headers       # already there: not upgraded past the running kernel
        elif [ -n "$repo_v" ] && [ "$repo_v" != "$kernel_v" ]; then
            die "$headers is now $repo_v and the running kernel ($image) is $kernel_v: installing it would replace the headers of the running kernel. Upgrade first (apt full-upgrade), reboot into the new kernel and run the installer again (nothing was installed)."
        fi
    fi
fi
while read -r image; do
    h=linux-headers-${image#linux-image-}
    [ "$h" != "$keep_headers" ] && apt_has "$h" && pkgs+=("$h")
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
