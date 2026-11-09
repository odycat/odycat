/*
 * OdyCAT Engine — die() with cleanup hooks, the stop flag, time helpers.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_UTIL_H
#define ODYCAT_UTIL_H

#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <time.h>

/* Print "ERROR: <message>", run the cleanup hooks (last registered first)
 * and exit(1). */
_Noreturn void die(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Register a cleanup for die(): each module that creates something outside
 * the process (shm regions, the instance lock) undoes it here. */
void on_die(void (*fn)(void));

/* Cleared by SIGINT, SIGTERM and SIGHUP; see install_signal_handlers(). */
extern _Atomic int g_running;

/* -v: the startup details (shm paths, the whole bus scan, startup SDOs, DC,
 * every slave's layout, each state reached). Without it the engine prints
 * what needs attention, and warnings always. */
extern int g_verbose;
#define VPRINTF(...) do { if (g_verbose) printf(__VA_ARGS__); } while (0)

void install_signal_handlers(void);

static inline bool stop_requested(void) {
    return !atomic_load_explicit(&g_running, memory_order_relaxed);
}

static inline int align_up_8(int v) {
    return (v + 7) & ~7;
}

static inline uint64_t ts_ns(struct timespec ts) {
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static inline uint64_t monotonic_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts_ns(ts);
}

static inline uint64_t realtime_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return ts_ns(ts);
}

/* Move an absolute deadline ns (>= 0) later. */
static inline void ts_add_ns(struct timespec *t, int64_t ns) {
    t->tv_sec  += (time_t)(ns / 1000000000LL);
    t->tv_nsec += (long)(ns % 1000000000LL);
    if (t->tv_nsec >= 1000000000L) {
        t->tv_nsec -= 1000000000L;
        t->tv_sec++;
    }
}

#endif
