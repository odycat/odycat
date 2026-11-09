#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# Fuzzes the engine's config parser (engine/tests/fuzz_config.c) with
# libFuzzer under AddressSanitizer and UBSan, seeded from
# engine/tests/fuzz_seeds and examples/.
#
#   ci/fuzz_config.sh [SECONDS]       default 60; needs clang (with its runtimes:
#                                     libclang-rt-dev on Debian) and libcjson-dev
#   ci/fuzz_config.sh --replay FILE...    replay inputs (e.g. a crash file) with gcc
#
# A finding is left as crash-*, leak-* or timeout-* in the current directory.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
T=$ROOT/engine/tests
SAN="-fsanitize=address,undefined,float-cast-overflow -fno-sanitize-recover=all"
SRCS="$T/fuzz_config.c $ROOT/engine/config.c $ROOT/engine/layout.c"
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

if [ "${1:-}" = "--replay" ]; then
    shift
    gcc -g -O1 -DFUZZ_STANDALONE $SAN -I"$ROOT/engine" $SRCS -lcjson -o "$OUT/fuzz_replay"
    "$OUT/fuzz_replay" "$@"
    echo "== replayed $# input(s): no finding"
    exit 0
fi

SECONDS_=${1:-60}
clang -g -O1 -fsanitize=fuzzer $SAN -I"$ROOT/engine" $SRCS -lcjson -o "$OUT/fuzz_config"
mkdir "$OUT/corpus"
cp "$T"/fuzz_seeds/*.json "$ROOT"/examples/*.json "$OUT/corpus/"
echo "== fuzzing the config parser for ${SECONDS_} s"
"$OUT/fuzz_config" -max_total_time="$SECONDS_" -timeout=5 -rss_limit_mb=2048 \
    -close_fd_mask=1 -print_final_stats=1 "$OUT/corpus"
