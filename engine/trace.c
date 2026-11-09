/*
 * OdyCAT Engine — the trace ring.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "shm.h"
#include "shm_abi.h"
#include "trace.h"
#include "util.h"

#define TRACE_MAX_BYTES    (512u * 1024u * 1024u)

/* NULL when history_s is 0 */
static odycat_trace_header_t *g_trace          = NULL;
static uint8_t               *g_trace_slots    = NULL;
static odycat_trace_event_t  *g_trace_events   = NULL;
static uint64_t               g_trace_event_seq = 0;
static bool                   g_trace_created  = false;

void trace_remove(void) {
    if (g_trace_created)
        unlink(shm_trace_path());
}

/* Create the trace region: header, history_s of slots, the event ring.
 * Allocated and touched here, so the RT loop never page-faults on it. */
void trace_create(const odycat_config_t *cfg) {
    if (cfg->history_s <= 0) {
        VPRINTF("OdyCAT: trace off (history_s = 0)\n");
        return;
    }
    /* A slot: its 64-byte header, then the data region's slave blocks
     * (SHM_HEADER_SIZE .. blocks_end), then the not-in-OP bitmap. */
    uint32_t bitmap_bytes = (uint32_t)((cfg->num_slaves + 63) / 64) * 8;
    uint32_t not_op_off = (uint32_t)(TRACE_SLOT_HEADER_SIZE + cfg->blocks_end - SHM_HEADER_SIZE);
    uint32_t slot_size = (uint32_t)align_up_8((int)(not_op_off + bitmap_bytes));
    uint64_t depth = (uint64_t)(cfg->history_s * 1e6 / cfg->cycle_us + 0.5);
    if (depth < 2) depth = 2;
    uint64_t slots_off  = sizeof(odycat_trace_header_t);
    uint64_t events_off = slots_off + depth * slot_size;
    uint64_t size = events_off + (uint64_t)TRACE_EVENTS * sizeof(odycat_trace_event_t);
    if (size > TRACE_MAX_BYTES)
        die("trace of %g s needs %" PRIu64 " MB (limit %u MB). Lower history_s.",
            cfg->history_s, size >> 20, TRACE_MAX_BYTES >> 20);

    on_die(trace_remove);
    const char *name = shm_trace_path();
    int fd = shm_create_region(name, 0640, &g_trace_created);
    if (ftruncate(fd, (off_t)size) < 0)
        die("ftruncate trace shm to %" PRIu64 " bytes failed: %s", size, strerror(errno));
    uint8_t *base = mmap(NULL, (size_t)size, PROT_READ | PROT_WRITE,
                         MAP_SHARED | MAP_POPULATE, fd, 0);
    if (base == MAP_FAILED)
        die("mmap trace shm failed: %s", strerror(errno));
    close(fd);
    memset(base, 0, (size_t)size);

    g_trace = (odycat_trace_header_t *)base;
    g_trace_slots = base + slots_off;
    g_trace_events = (odycat_trace_event_t *)(base + events_off);
    g_trace->version       = TRACE_VERSION;
    g_trace->slot_size     = slot_size;
    g_trace->slot_count    = (uint32_t)depth;
    g_trace->slots_offset  = (uint32_t)slots_off;
    g_trace->blocks_end    = (uint32_t)cfg->blocks_end;
    g_trace->not_op_offset = not_op_off;
    g_trace->header_size   = TRACE_HEADER_SIZE;
    g_trace->blocks_start  = SHM_HEADER_SIZE;
    g_trace->event_count   = TRACE_EVENTS;
    g_trace->events_offset = (uint32_t)events_off;
    g_trace->cycle_us      = (uint32_t)cfg->cycle_us;
    atomic_thread_fence(memory_order_release);
    g_trace->magic         = TRACE_MAGIC;     /* last: readers check it first */
    VPRINTF("OdyCAT: trace shm   '%s' (%g s, %" PRIu64 " slots of %u B, %" PRIu64 " KB)\n",
           name, cfg->history_s, depth, slot_size, size >> 10);
}

void trace_event(uint64_t cycle, uint16_t type, uint16_t slave, uint32_t arg) {
    if (!g_trace) return;
    uint64_t seq = ++g_trace_event_seq;
    odycat_trace_event_t *e = &g_trace_events[(seq - 1) % TRACE_EVENTS];
    atomic_store_explicit(&e->seq, 0, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
    e->cycle   = cycle;
    e->real_ns = realtime_ns();
    e->type    = type;
    e->slave   = slave;
    e->arg     = arg;
    atomic_store_explicit(&e->seq, seq, memory_order_release);
    atomic_store_explicit(&g_trace->event_head, seq, memory_order_release);
}

void trace_slot(const odycat_config_t *cfg, const uint8_t *shm_base,
                const uint8_t *domain_pd, uint64_t cycle, uint64_t mono_ns,
                uint32_t wkc, uint32_t error_flags, const uint64_t *not_op) {
    if (!g_trace) return;
    uint8_t *slot = g_trace_slots
                  + (size_t)((cycle - 1) % g_trace->slot_count) * g_trace->slot_size;
    odycat_slot_header_t *h = (odycat_slot_header_t *)slot;
    atomic_store_explicit(&h->cycle, 0, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);

    /* The slave blocks hold the inputs as received and the outputs as sent
     * this cycle (both written by the RT loop before the send) */
    (void)domain_pd;
    memcpy(slot + TRACE_SLOT_HEADER_SIZE, shm_base + SHM_HEADER_SIZE,
           cfg->blocks_end - SHM_HEADER_SIZE);
    memcpy(slot + g_trace->not_op_offset, not_op, (size_t)((cfg->num_slaves + 63) / 64) * 8);
    h->mono_ns      = mono_ns;
    h->real_ns      = realtime_ns();
    h->work_counter = wkc;
    h->error_flags  = error_flags;

    atomic_store_explicit(&h->cycle, cycle, memory_order_release);
    atomic_store_explicit(&g_trace->head, cycle, memory_order_release);
}

void trace_set_skipped(uint64_t total) {
    if (g_trace)
        atomic_store_explicit(&g_trace->skipped, total, memory_order_release);
}
