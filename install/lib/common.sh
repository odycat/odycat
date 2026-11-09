# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Shared by install.sh and its steps (sourced, not run).
#
# install.sh runs with `set -o pipefail`: never end a pipeline in `grep -q`.
# It exits at the first match, the command before it dies of SIGPIPE and the
# pipeline fails although it matched. Use `grep ... >/dev/null`.

# shellcheck disable=SC2034  # used by the steps
STATE_DIR=/var/lib/odycat-install         # what this installer installed
# shellcheck disable=SC2034
LOG_FILE=/var/log/odycat-install.log

say()  { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf '   WARNING: %s\n' "$*" >&2; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# A command that changes the system: printed with --dry-run, run otherwise.
run() {
    if [ "${DRY_RUN:-0}" = 1 ]; then
        printf '   [dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

# write_file PATH MODE: the content comes on stdin.
write_file() {
    local path=$1 mode=$2 content
    content=$(cat)
    if [ "${DRY_RUN:-0}" = 1 ]; then
        printf '   [dry-run] write %s (%s):\n' "$path" "$mode"
        printf '%s\n' "$content" | sed 's/^/             | /'
        return
    fi
    install -d "$(dirname "$path")"
    printf '%s\n' "$content" > "$path"
    chmod "$mode" "$path"
}

# ask "question": yes with --yes, else asks on the terminal.
ask() {
    [ "${ASSUME_YES:-0}" = 1 ] && return 0
    local answer
    read -r -p "   $1 [y/N] " answer </dev/tty || return 1
    case $answer in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

have() { command -v "$1" >/dev/null 2>&1; }

# The user who ran sudo (for the odycat group and builds in their checkout).
invoking_user() { printf '%s' "${SUDO_USER:-}"; }
