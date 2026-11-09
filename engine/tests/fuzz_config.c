/*
 * OdyCAT Engine — fuzz target for the config parser (engine/config.c), which
 * reads its input as root.
 *
 * libFuzzer (clang):
 *   clang -g -O1 -fsanitize=fuzzer,address,undefined -I.. \
 *         fuzz_config.c ../config.c ../layout.c -lcjson -o fuzz_config
 *   ./fuzz_config -max_total_time=60 -close_fd_mask=1 corpus/
 * Any compiler, to replay inputs (e.g. a crash file) under the sanitizers:
 *   gcc -g -DFUZZ_STANDALONE -fsanitize=address,undefined -I.. \
 *       fuzz_config.c ../config.c ../layout.c -lcjson -o fuzz_replay
 *   ./fuzz_replay file...
 * ci/fuzz_config.sh does both.
 *
 * A config the parser refuses ends in die(), which in the engine exits. Here
 * die() jumps back instead, so every input is one run; what the fuzzer looks
 * for is a crash, a sanitizer report or a hang before that point. Memory the
 * parser holds when it dies is not freed (the engine exits there), so leak
 * detection is off.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "config.h"
#include "util.h"

static jmp_buf g_back;
static odycat_config_t g_cfg;
static char g_path[64];

_Noreturn void die(const char *fmt, ...) {
    (void)fmt;
    longjmp(g_back, 1);
}

void on_die(void (*fn)(void)) { (void)fn; }

int g_verbose = 0;                     /* util.c is not linked: quiet, as by default */

const char *__asan_default_options(void) { return "detect_leaks=0"; }

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (!g_path[0]) {
        snprintf(g_path, sizeof(g_path), "/tmp/odycat-fuzz-XXXXXX");
        int fd = mkstemp(g_path);
        if (fd < 0) abort();
        close(fd);
    }
    FILE *f = fopen(g_path, "wb");
    if (!f) abort();
    if (size && fwrite(data, 1, size, f) != size) abort();
    fclose(f);

    if (setjmp(g_back) == 0)
        config_load(&g_cfg, g_path);
    free(g_cfg.text);          /* when it was read, whether parsed or not */
    g_cfg.text = NULL;
    return 0;
}

#ifdef FUZZ_STANDALONE
int main(int argc, char *argv[]) {
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "rb");
        if (!f) { perror(argv[i]); return 2; }
        static uint8_t buf[1 << 20];
        size_t n = fread(buf, 1, sizeof(buf), f);
        fclose(f);
        LLVMFuzzerTestOneInput(buf, n);
    }
    if (g_path[0]) unlink(g_path);
    return 0;
}
#endif
