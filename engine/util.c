/*
 * OdyCAT Engine — die() with cleanup hooks, signals.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "util.h"

_Atomic int g_running = 1;
int g_verbose = 0;

#define MAX_DIE_HOOKS 8   /* the engine registers three */

static void (*g_die_hooks[MAX_DIE_HOOKS])(void);
static int g_die_hook_count = 0;

void on_die(void (*fn)(void)) {
    if (g_die_hook_count < MAX_DIE_HOOKS)
        g_die_hooks[g_die_hook_count++] = fn;
}

void die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "ERROR: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
    /* Don't leave a stale shm region for Python to attach to, nor the lock. */
    while (g_die_hook_count > 0)
        g_die_hooks[--g_die_hook_count]();
    exit(1);
}

static void sig_handler(int sig) {
    (void)sig;
    atomic_store_explicit(&g_running, 0, memory_order_relaxed);
}

/* SIGINT (Ctrl-C), SIGTERM (kill, systemctl stop) and SIGHUP (the
 * terminal the engine runs in was closed) all stop the engine through
 * the normal shutdown: bus to INIT, shm unlinked, lock released. A
 * signal during startup ends it through die(), which cleans up too.
 * SIGPIPE is ignored: a write to a closed pipe (`| tee log` after the
 * terminal closed) fails instead of killing the engine mid-cleanup. */
void install_signal_handlers(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = sig_handler;
    sigaction(SIGINT,  &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGHUP,  &sa, NULL);
    signal(SIGPIPE, SIG_IGN);
}
