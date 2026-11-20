# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Step 20: the IgH EtherCAT Master. Kernel modules (ec_master, ec_generic)
# through DKMS, so a kernel upgrade rebuilds them; userland (libethercat, the
# ethercat tool, ethercatctl, /etc/ethercat.conf) in /usr/local; the
# ethercat.service unit. Only the generic Ethernet driver is built.
say "IgH EtherCAT Master $IGH_VERSION"

IGH_SRC=/usr/src/ethercat-$IGH_VERSION
IGH_MARK=$STATE_DIR/igh.env

# IgH's own kernel modules: the master, the generic driver and its native
# drivers (not every ec_* module: ec_sys is ACPI, ec_bhf a mainline driver).
IGH_MODULES='ec_master|ec_generic|ec_8139too|ec_e100|ec_e1000|ec_e1000e|ec_r8169|ec_igb|ec_igc|ec_ccat|ec_genet|ec_bcmgenet|ec_stmmac|ec_dwmac[-_a-z0-9]*'

# A module that a package of the distribution owns (its kernel): left alone,
# since the DKMS modules (updates/) take precedence over it.
distro_module() {
    have dpkg || return 1
    local f
    for f in "$1" "/usr$1" "${1#/usr}"; do       # dpkg records /lib or /usr/lib (usrmerge)
        dpkg -S "$f" >/dev/null 2>&1 && return 0
    done
    return 1
}

# The IgH modules installed by other means: not the distribution's, not ours.
foreign_igh_modules() {
    local m
    find /lib/modules -regextype posix-extended -regex ".*/($IGH_MODULES)\.ko(\.[a-z]+)?" 2>/dev/null |
        while read -r m; do
            case $m in */updates/dkms/*) continue ;; esac
            distro_module "$m" || printf '%s\n' "$m"
        done
}

# The files of an IgH installed by other means, wherever they are.
foreign_igh_files() {
    local f
    for f in /usr/local/bin/ethercat /usr/bin/ethercat /usr/local/sbin/ethercatctl \
             /usr/sbin/ethercatctl /usr/local/lib/libethercat.* /usr/lib/libethercat.* \
             /usr/local/lib/pkgconfig/libethercat.pc /usr/local/include/ecrt.h \
             /usr/local/include/ectty.h /usr/local/etc/ethercat.conf /etc/ethercat.conf \
             /usr/local/etc/sysconfig/ethercat /usr/local/etc/init.d/ethercat \
             /lib/systemd/system/ethercat.service /etc/systemd/system/ethercat.service; do
        [ -e "$f" ] && printf '%s\n' "$f"
    done
    foreign_igh_modules
}

igh_step() {
    if [ "$KEEP_IGH" = 1 ]; then
        have ethercat || die "--keep-igh: no IgH found (the ethercat tool is missing)"
        modinfo ec_master >/dev/null 2>&1 ||
            die "--keep-igh: the IgH installed has no ec_master module for the running kernel $(uname -r). Build it for this kernel first, or uninstall that IgH and run the installer without --keep-igh."
        info "--keep-igh: using the IgH already installed ($(ethercat version 2>/dev/null | head -1))"
        info "OdyCAT $ODYCAT_VERSION is tested with IgH $IGH_VERSION"
        return 0
    fi
    if [ ! -f "$IGH_MARK" ] && { have ethercat || [ -n "$(foreign_igh_modules)" ]; }; then
        die "an IgH master is already installed, not by this installer. Keep it with --keep-igh, or uninstall it first: \`make uninstall\` in its source tree, or remove these files by hand:
$(foreign_igh_files | sed 's/^/   /')"
    fi
    local own
    own=$(modinfo -n ec_master 2>/dev/null || true)
    if [ -n "$own" ] && distro_module "$own"; then
        info "the kernel's own IgH $(modinfo -F version ec_master 2>/dev/null | awk '{print $1}') ($own) stays installed; IgH $IGH_VERSION through DKMS takes precedence"
    fi
    # Marked as ours before anything is installed, so that a run stopped
    # half-way (a module build that failed) can be run again.
    [ -f "$IGH_MARK" ] || printf '# Being installed by the OdyCAT installer\n' |
        write_file "$IGH_MARK" 0644

    # Source: the pinned tag, checked against the pinned commit.
    if [ "$DRY_RUN" = 1 ] || [ ! -d "$IGH_SRC/.git" ] ||
       [ "$(git -C "$IGH_SRC" rev-parse HEAD 2>/dev/null)" != "$IGH_COMMIT" ]; then
        dkms status -m ethercat -v "$IGH_VERSION" 2>/dev/null | grep . >/dev/null &&
            run dkms remove -m ethercat -v "$IGH_VERSION" --all
        run rm -rf "$IGH_SRC"
        run git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$IGH_VERSION" "$IGH_REPO" "$IGH_SRC"
        if [ "$DRY_RUN" = 0 ]; then
            local got
            got=$(git -C "$IGH_SRC" rev-parse HEAD)
            [ "$got" = "$IGH_COMMIT" ] || { rm -rf "$IGH_SRC"; die "IgH $IGH_VERSION from $IGH_REPO is commit $got, not the pinned $IGH_COMMIT: not installed"; }
            info "source $IGH_SRC, commit $got (pinned)"
            (cd "$IGH_SRC" && ./bootstrap >/dev/null 2>&1)
        fi
    else
        info "source $IGH_SRC already at the pinned commit"
    fi

    # Userland, built in a copy so that the DKMS source stays clean.
    local conf_backup='' build
    [ -f /etc/ethercat.conf ] && conf_backup=$(mktemp) && cp -p /etc/ethercat.conf "$conf_backup"
    build=$(mktemp -d)
    info "userland -> /usr/local (libethercat, ethercat, ethercatctl)"
    run cp -a "$IGH_SRC/." "$build/"
    run sh -c "cd '$build' && ./configure -q --prefix=/usr/local --sysconfdir=/etc --disable-kernel --with-systemdsystemunitdir=no >/dev/null && make -j$(nproc) >/dev/null && make install >/dev/null"
    run rm -rf "$build"
    [ -n "$conf_backup" ] && run cp -p "$conf_backup" /etc/ethercat.conf   # make install overwrites it
    run ldconfig

    # Kernel modules through DKMS.
    write_file "$IGH_SRC/odycat-dkms-configure" 0755 <<CONF
#!/bin/sh
# DKMS PRE_BUILD (written by the OdyCAT installer): configure the IgH kernel
# modules for kernel \$1, generic Ethernet driver only.
set -e
[ -x ./configure ] || ./bootstrap
./configure -q --with-linux-dir="/lib/modules/\$1/build" --sysconfdir=/etc \\
    --disable-userlib --disable-tool --enable-generic --disable-8139too \\
    --with-systemdsystemunitdir=no
# IgH picks the kernel_bind() prototype by kernel version (struct
# sockaddr_unsized from 6.19), but some kernels carry it earlier (Raspberry
# Pi OS 6.18.60). Pick it from this kernel's headers instead, in the copy
# DKMS builds from (not the source in /usr/src).
for h in "/lib/modules/\$1/build/include/linux/net.h" "/lib/modules/\$1/source/include/linux/net.h"; do
    [ -f "\$h" ] || continue
    if grep -q 'kernel_bind(struct socket \*sock, struct sockaddr_unsized' "\$h"; then u=1; else u=0; fi
    sed -i "s|^#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 19, 0)\$|#if \$u /* kernel_bind() with struct sockaddr_unsized, from the headers */|" devices/generic.c
    break
done
CONF
    write_file "$IGH_SRC/dkms.conf" 0644 <<CONF
# Written by the OdyCAT installer (install/steps/20-igh.sh).
PACKAGE_NAME="ethercat"
PACKAGE_VERSION="$IGH_VERSION"
PRE_BUILD="odycat-dkms-configure \${kernelver}"
MAKE[0]="make modules"
CLEAN="true"
BUILT_MODULE_NAME[0]="ec_master"
BUILT_MODULE_LOCATION[0]="master/"
DEST_MODULE_LOCATION[0]="/updates/dkms"
BUILT_MODULE_NAME[1]="ec_generic"
BUILT_MODULE_LOCATION[1]="devices/"
DEST_MODULE_LOCATION[1]="/updates/dkms"
AUTOINSTALL="yes"
CONF
    local v
    for v in $(dkms status -m ethercat 2>/dev/null | sed -n 's|^ethercat/\([^,:]*\).*|\1|p' | sort -u); do
        [ "$v" = "$IGH_VERSION" ] || run dkms remove -m ethercat -v "$v" --all
    done
    dkms status -m ethercat -v "$IGH_VERSION" 2>/dev/null | grep . >/dev/null ||
        run dkms add -m ethercat -v "$IGH_VERSION"
    local k
    for k in /lib/modules/*; do                 # every installed kernel that has its headers
        k=${k##*/}
        [ -d "/lib/modules/$k/kernel" ] && [ -d "/lib/modules/$k/build" ] || continue
        if dkms status -m ethercat -v "$IGH_VERSION" -k "$k" 2>/dev/null | grep ': installed' >/dev/null; then
            info "modules for kernel $k: already installed"
        else
            info "modules for kernel $k (DKMS)"
            run dkms install -m ethercat -v "$IGH_VERSION" -k "$k" --force >/dev/null ||
                die "the IgH modules did not build for kernel $k: see /var/lib/dkms/ethercat/$IGH_VERSION/build/make.log"
        fi
    done
    if have mokutil && mokutil --sb-state 2>/dev/null | grep -i 'enabled' >/dev/null; then
        warn "Secure Boot is on: the kernel loads the DKMS modules only once their signing key is enrolled (mokutil --import, see your distribution's DKMS documentation)"
    fi

    # The service that loads the modules (ethercatctl reads /etc/ethercat.conf).
    sed "s|@sbindir@|/usr/local/sbin|g" "$IGH_SRC/script/ethercat.service.in" 2>/dev/null |
        write_file /etc/systemd/system/ethercat.service 0644
    run systemctl daemon-reload

    write_file "$IGH_MARK" 0644 <<MARK
# Installed by the OdyCAT installer on $(date -u +%Y-%m-%d)
IGH_VERSION=$IGH_VERSION
IGH_COMMIT=$IGH_COMMIT
MARK
}
igh_step
