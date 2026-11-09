# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Step 50: check what was installed, then say what comes next.
# ok and bad always succeed, so `test && ok || bad` is an if-then-else here.
# shellcheck disable=SC2015
say "Check"

failed=0
ok()  { info "OK    $*"; }
bad() { warn "$*"; failed=1; }

if [ "$DRY_RUN" = 1 ]; then
    info "[dry-run] nothing to check"
else
    if [ "$KEEP_IGH" = 0 ]; then
        dkms status -m ethercat -v "$IGH_VERSION" -k "$(uname -r)" 2>/dev/null | grep ': installed' >/dev/null &&
            ok "IgH $IGH_VERSION modules (DKMS) for kernel $(uname -r)" ||
            bad "the IgH modules are not installed for the running kernel $(uname -r)"
    fi
    lsmod | grep '^ec_master ' >/dev/null && ok "ec_master loaded" || bad "ec_master is not loaded (systemctl status ethercat)"
    if master=$(ethercat master 2>&1); then
        ok "ethercat master: $(printf '%s\n' "$master" | sed -n 's/^ *\(Phase\|Slaves\): *\(.*\)/\1 \2/p' | paste -sd, -)"
        printf '%s\n' "$master" | grep 'Link: UP' >/dev/null || info "      no link on the bus card yet (cable, or no slave powered)"
    else
        bad "ethercat master: $master"
    fi
    if [ "$IGH_ONLY" = 0 ]; then
        [ "$(/usr/local/bin/odycat --version 2>/dev/null | awk '{print $2}')" = "$ODYCAT_VERSION" ] &&
            ok "engine: $(/usr/local/bin/odycat --version)" || bad "the engine is not OdyCAT $ODYCAT_VERSION"
        [ -e /dev/EtherCAT0 ] && [ "$(stat -c %G /dev/EtherCAT0)" = odycat ] &&
            ok "/dev/EtherCAT0 belongs to the odycat group" || bad "/dev/EtherCAT0 missing or not in the odycat group"
    fi
fi

user=$(invoking_user)
# shellcheck disable=SC2088  # printed for the user, not expanded here
venv="~/odycat-venv"
say "Next steps"
[ "$IGH_ONLY" = 0 ] && [ -n "$user" ] && info "1. Log out and in again, so that $user is in the odycat group."
info "2. Install the Python package of the same release, in a virtual environment:"
info "       python3 -m venv $venv"
info "       $venv/bin/pip install odycat==$ODYCAT_VERSION"
info "3. Write the bus configuration from the connected slaves:"
info "       $venv/bin/odycat-config scan -o mybus.json"
info "4. Start the engine (or run it as a service: docs/getting-started.md):"
info "       sudo odycat mybus.json"
[ "$WANT_RT" = 1 ] && [ "$DISTRO" = debian ] && info "Reboot into the real-time kernel first (--rt); its IgH modules are already built."
if [ "$failed" = 1 ]; then
    die "some checks failed (above); the log is in $LOG_FILE"
fi
