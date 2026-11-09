# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Step 40: the OdyCAT engine (/usr/local/bin/odycat): the release binary,
# checked against the release's SHA256SUMS, or built here with
# --build-engine. Then the system files from packaging/ (what
# `make -C engine install-service` installs): the odycat user and group, the
# udev rule for /dev/EtherCAT*, the odycat@ service, the polkit rule,
# /etc/odycat. The Python package is not installed here (step 50 says how).
say "OdyCAT engine $ODYCAT_VERSION"

RELEASES=https://github.com/odycat/odycat/releases/download/v$ODYCAT_VERSION

# fetch NAME DIR: a release file, checked against the release's SHA256SUMS.
fetch() {
    local name=$1 dir=$2
    if ! curl -fsSL --retry 3 -o "$dir/SHA256SUMS" "$RELEASES/SHA256SUMS" ||
       ! curl -fsSL --retry 3 -o "$dir/$name" "$RELEASES/$name"; then
        die "could not download $name of OdyCAT $ODYCAT_VERSION from $RELEASES. Build the engine here instead: --build-engine"
    fi
    (cd "$dir" && grep -E "  $name\$" SHA256SUMS | sha256sum -c --quiet -) ||
        die "$name does not match the release's SHA256SUMS: not installed"
    info "$name: checksum OK"
}

engine_build() {
    local user tmp
    user=$(invoking_user)
    if [ -n "$SRC_DIR" ]; then                    # a repository checkout
        info "building in $SRC_DIR/engine"
        if [ -n "$user" ]; then
            run sudo -u "$user" make -s -C "$SRC_DIR/engine" IGH=/usr/local
        else
            run make -s -C "$SRC_DIR/engine" IGH=/usr/local
        fi
        run install -m 0755 "$SRC_DIR/engine/odycat" /usr/local/bin/odycat
    else                                          # a release tarball: the release's source
        tmp=$(mktemp -d)
        [ "$DRY_RUN" = 1 ] || fetch "odycat-$ODYCAT_VERSION-source.tar.gz" "$tmp"
        run tar -xzf "$tmp/odycat-$ODYCAT_VERSION-source.tar.gz" -C "$tmp"
        run make -s -C "$tmp/odycat-$ODYCAT_VERSION/engine" IGH=/usr/local
        run install -m 0755 "$tmp/odycat-$ODYCAT_VERSION/engine/odycat" /usr/local/bin/odycat
        run rm -rf "$tmp"
    fi
}

engine_download() {
    local name tmp
    name=odycat-engine-$ODYCAT_VERSION-linux-$ARCH.tar.gz
    tmp=$(mktemp -d)
    if [ "$DRY_RUN" = 1 ]; then
        info "[dry-run] download $RELEASES/$name and check it against SHA256SUMS"
    else
        fetch "$name" "$tmp"
    fi
    run tar -xzf "$tmp/$name" -C "$tmp"
    run install -m 0755 "$tmp/odycat-engine-$ODYCAT_VERSION-linux-$ARCH/odycat" /usr/local/bin/odycat
    run rm -rf "$tmp"
}

engine_step() {
    if [ "$IGH_ONLY" = 1 ]; then
        info "--igh-only: no engine"
        return 0
    fi
    if [ "$(/usr/local/bin/odycat --version 2>/dev/null | awk '{print $2}')" = "$ODYCAT_VERSION" ] &&
       [ "$BUILD_ENGINE" = 0 ]; then
        info "engine $ODYCAT_VERSION already installed"
    elif [ "$BUILD_ENGINE" = 1 ]; then
        engine_build
    else
        engine_download
    fi
    if [ "$DRY_RUN" = 0 ]; then
        if ldd /usr/local/bin/odycat | grep 'not found' >/dev/null; then
            die "the engine misses libraries: $(ldd /usr/local/bin/odycat | grep 'not found' | tr -s ' \t' ' ')"
        fi
        info "$(/usr/local/bin/odycat --version)"
    fi

    # System files (the same as `make -C engine install-service`).
    run install -D -m 0644 "$PKG_DIR/sysusers.d/odycat.conf" /etc/sysusers.d/odycat.conf
    run systemd-sysusers /etc/sysusers.d/odycat.conf
    run install -D -m 0644 "$PKG_DIR/udev/99-ethercat.rules" /etc/udev/rules.d/99-ethercat.rules
    run udevadm control --reload
    run udevadm trigger --sysname-match='EtherCAT*'
    sed 's|@BINDIR@|/usr/local/bin|' "$PKG_DIR/systemd/odycat@.service.in" |
        write_file /etc/systemd/system/odycat@.service 0644
    run install -D -m 0644 "$PKG_DIR/polkit/50-odycat.rules" /etc/polkit-1/rules.d/50-odycat.rules
    run install -d -m 0750 -g odycat /etc/odycat
    run systemctl daemon-reload

    local user
    user=$(invoking_user)
    if [ -n "$user" ] && ! id -nG "$user" | tr ' ' '\n' | grep -x odycat >/dev/null; then
        info "adding $user to the odycat group"
        run usermod -aG odycat "$user"
    fi
}
engine_step
