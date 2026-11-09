/*
 * OdyCAT Engine — real-time setup (memory lock, SCHED_FIFO) and the bus
 * cycle loop.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include <errno.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "igh.h"
#include "rt.h"
#include "shm_abi.h"
#include "trace.h"
#include "util.h"

#define PREFAULT_STACK_BYTES  (64 * 1024)
#define RT_PRIORITY           80         /* SCHED_FIFO priority of the engine */

/* ------------------------------------------------------------------ */
/*  Real-time setup                                                    */
/* ------------------------------------------------------------------ */

/* Touch the stack the RT loop will use, so that with memory locked its
 * pages are resident before the first cycle. */
static void prefault_stack(void) {
    volatile uint8_t buf[PREFAULT_STACK_BYTES];
    for (size_t i = 0; i < sizeof(buf); i += 4096)
        buf[i] = 0;
}

static bool any_dc_slave(const odycat_config_t *cfg) {
    for (int i = 0; i < cfg->num_slaves; i++)
        if (cfg->slaves[i].dc_enabled) return true;
    return false;
}

static bool lock_memory(const odycat_config_t *cfg) {
    if (mlockall(MCL_CURRENT | MCL_FUTURE) < 0) {
        if (cfg->require_rt)
            die("could not lock memory (mlockall): %s. require_rt is set. Run as "
                "root, or give the engine CAP_IPC_LOCK or a large enough "
                "RLIMIT_MEMLOCK.", strerror(errno));
        fprintf(stderr, "WARNING: could not lock memory (mlockall): %s. "
                "Page faults can delay cycles.\n", strerror(errno));
        return false;
    }
    prefault_stack();
    VPRINTF("OdyCAT: memory locked.\n");
    return true;
}

/* Why SCHED_FIFO was refused. As root (or with CAP_SYS_NICE) the usual cause
 * is a kernel with RT group scheduling (CONFIG_RT_GROUP_SCHED, e.g. Armbian):
 * with cgroups v2 it gives no real-time time to any process outside the root
 * cgroup, a systemd service or a login session included. */
static const char *rt_refused_hint(int err) {
    if (err != EPERM || geteuid() != 0)
        return "Run as root or give the engine CAP_SYS_NICE.";
    char line[512] = "";
    FILE *f = fopen("/proc/self/cgroup", "r");
    if (f) {
        if (!fgets(line, sizeof(line), f))
            line[0] = 0;
        fclose(f);
    }
    if (strncmp(line, "0::/", 4) == 0 && line[4] != '\n' && line[4] != 0)
        return "The engine runs as root, so the kernel's real-time group scheduling "
               "(CONFIG_RT_GROUP_SCHED) is the likely cause: it gives no real-time time "
               "to processes outside the root cgroup. Boot with rt_group_sched=0 on the "
               "kernel command line (see docs/realtime.md).";
    return "The kernel refused it although the engine runs as root.";
}

static bool set_rt_priority(const odycat_config_t *cfg) {
    struct sched_param sp;
    sp.sched_priority = RT_PRIORITY;
    if (sched_setscheduler(0, SCHED_FIFO, &sp) < 0) {
        int err = errno;
        if (cfg->require_rt)
            die("could not set SCHED_FIFO priority %d: %s. require_rt is set. %s",
                RT_PRIORITY, strerror(err), rt_refused_hint(err));
        fprintf(stderr, "WARNING: could not set SCHED_FIFO priority %d: %s. %s "
                "Continuing without RT scheduling.\n", RT_PRIORITY, strerror(err),
                rt_refused_hint(err));
        if (any_dc_slave(cfg))
            fprintf(stderr, "WARNING: without RT scheduling, late cycles can leave "
                    "Sync0 periods of DC slaves without new data and make them "
                    "drop out of OP. If that happens, set a longer "
                    "dc_sync.sync0_cycle_ns (several bus cycles).\n");
        return false;
    }
    VPRINTF("OdyCAT: RT scheduling active (SCHED_FIFO prio %d).\n", RT_PRIORITY);
    return true;
}

/* Lock memory and request SCHED_FIFO. Unless require_rt is set, a failure
 * is a warning and the engine runs without it. Returns ODYCAT_RT_* flags. */
uint32_t rt_enter(const odycat_config_t *cfg) {
    uint32_t flags = 0;
    if (lock_memory(cfg))
        flags |= ODYCAT_RT_MEMLOCK;
    if (set_rt_priority(cfg))
        flags |= ODYCAT_RT_SCHED_FIFO;
    return flags;
}

/* ------------------------------------------------------------------ */
/*  Real-time loop                                                     */
/* ------------------------------------------------------------------ */


/* Absolute-time sleep to the next slot of the cycle grid, so jitter does
 * not accumulate.
 *
 * Frames at least half a period apart. A cycle runs only if it is less
 * than half a period late for its slot, and the previous frame went out
 * at least half a period ago (the previous cycle can itself have been
 * preempted before its send). Otherwise the missed slots are skipped
 * instead of run back to back, and the cycle runs in the current slot or
 * waits for the next one. So a frame is never sent before the previous
 * one came back (wrong WKC, stale inputs), nor several set-points between
 * two Sync0 edges. The grid, and with it the DC phase, is kept.
 * cycle_count only counts cycles that ran; the trace records how many
 * were skipped. */
static void rt_wait_for_slot(rt_loop_t *rt, uint64_t cycle) {
    int64_t period_ns = (int64_t)rt->cfg->cycle_us * 1000;
    ts_add_ns(&rt->next, period_ns);
    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &rt->next, NULL);

    uint64_t woke_ns = monotonic_ns();
    int64_t late_ns = (int64_t)woke_ns - (int64_t)ts_ns(rt->next);
    bool sent_recently = rt->prev_cycle_ns
        && woke_ns - rt->prev_cycle_ns < (uint64_t)(period_ns / 2);
    if (late_ns < period_ns / 2 && !sent_recently)
        return;

    int64_t missed = late_ns > 0 ? late_ns / period_ns : 0;   /* slots passed */
    if (late_ns - missed * period_ns >= period_ns / 2 || sent_recently)
        missed++;                                           /* wait for the next */
    ts_add_ns(&rt->next, missed * period_ns);
    rt->skipped_total += (uint64_t)missed;
    rt->shm->skipped_cycles = rt->skipped_total;
    trace_set_skipped(rt->skipped_total);
    trace_event(cycle, TRACE_EV_CYCLES_SKIPPED, 0xFFFF,
                missed > UINT32_MAX ? UINT32_MAX : (uint32_t)missed);
    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &rt->next, NULL);
}

static void rt_check_wkc(rt_loop_t *rt, uint64_t cycle, uint32_t wkc) {
    odycat_header_t *shm = rt->shm;
    bool wrong = wkc != shm->expected_wkc;
    shm->work_counter = wkc;
    if (wrong) {
        shm->wkc_wrong_cycles++;
        shm->wkc_wrong_streak++;
    } else {
        shm->wkc_wrong_streak = 0;
    }
    if (wrong)
        atomic_fetch_or(&shm->error_flags, ODYCAT_ERR_WKC_MISMATCH);
    else
        atomic_fetch_and(&shm->error_flags, ~ODYCAT_ERR_WKC_MISMATCH);
    if (wrong != rt->wkc_wrong) {
        rt->wkc_wrong = wrong;
        trace_event(cycle, wrong ? TRACE_EV_WKC_WRONG : TRACE_EV_WKC_OK, 0xFFFF, wkc);
    }
}

/* Claim the published buffers of a sync group: load its pub word, store it
 * into busy, and check that pub did not move in between. Once that holds,
 * no writer picks those buffers until busy changes again (writers skip both
 * the published and the busy buffer). A publish landing in the few
 * nanoseconds between the two loads costs a retry; if every try moves,
 * the group keeps last cycle's outputs this cycle. */
static bool claim_group(rt_loop_t *rt, int g, uint64_t *word) {
    _Atomic uint64_t *pub  = (_Atomic uint64_t *)(rt->shm_base + rt->cfg->groups_offset) + g;
    _Atomic uint64_t *busy = (_Atomic uint64_t *)(rt->shm_base + rt->cfg->busy_offset) + g;
    for (int tries = 0; tries < 4; tries++) {
        uint64_t w = atomic_load_explicit(pub, memory_order_acquire);
        atomic_store_explicit(busy, w, memory_order_seq_cst);
        if (atomic_load_explicit(pub, memory_order_seq_cst) == w) {
            *word = w;
            return true;
        }
    }
    return false;
}

/* One slave's outputs: its published buffer to the domain and to the
 * "as sent" half of its block, or zeros if the buffer is stale (the slave
 * left OP after it was written: it must not resume old commands, and a
 * CiA 402 drive would re-enable itself on controlword 0x000F). */
static void send_slave_outputs(rt_loop_t *rt, int i, unsigned idx) {
    const slave_cfg_t *s = &rt->cfg->slaves[i];
    uint8_t *sent = rt->shm_base + s->shm_offset + s->tx.size;
    const uint8_t *buf = idx < ODYCAT_OUT_BUFFERS
        ? rt->shm_base + s->out_buf_offset + (size_t)idx * (size_t)s->out_buf_stride : NULL;
    if (!buf || ((const odycat_out_buf_header_t *)buf)->epoch != rt->status[i].drop_count) {
        for (int c = 0; c < s->rx.chunk_count; c++)
            memset(rt->domain_pd + s->rx.chunks[c].domain_off, 0, s->rx.chunks[c].size);
        memset(sent, 0, (size_t)s->rx.size);
        return;
    }
    const uint8_t *src = buf + ODYCAT_OUT_BUF_HEADER;
    memcpy(sent, src, (size_t)s->rx.size);
    for (int c = 0; c < s->rx.chunk_count; c++) {
        memcpy(rt->domain_pd + s->rx.chunks[c].domain_off, src, s->rx.chunks[c].size);
        src += s->rx.chunks[c].size;
    }
}

/* Both directions, inside the inputs seqlock: domain inputs → shm, and each
 * sync group's published outputs → domain (and the block's "as sent" half),
 * so a reader of the blocks sees one cycle's inputs and outputs together. */
static void exchange_process_data(rt_loop_t *rt) {
    odycat_header_t *shm = rt->shm;
    const odycat_config_t *cfg = rt->cfg;
    uint32_t seq = atomic_load_explicit(&shm->seq_counter, memory_order_relaxed);
    atomic_store_explicit(&shm->seq_counter, seq + 1,
                          memory_order_release);  /* odd → write in progress */
    /* Keep the data copies below from moving before the odd store
     * (the release store alone only orders the writes before it). */
    atomic_thread_fence(memory_order_release);

    for (int i = 0; i < cfg->num_slaves; i++) {
        const slave_cfg_t *s = &cfg->slaves[i];
        uint8_t *dst = rt->shm_base + s->shm_offset;
        for (int c = 0; c < s->tx.chunk_count; c++) {
            memcpy(dst, rt->domain_pd + s->tx.chunks[c].domain_off, s->tx.chunks[c].size);
            dst += s->tx.chunks[c].size;
        }
    }
    for (int g = 0; g < cfg->group_count; g++) {
        uint64_t word;
        if (!claim_group(rt, g, &word)) {
            shm->out_group_skips++;
            continue;            /* the domain still holds last cycle's outputs */
        }
        const out_group_t *grp = &cfg->groups[g];
        for (int m = 0; m < grp->count; m++)
            send_slave_outputs(rt, grp->slaves[m], (unsigned)(word >> (2 * m)) & 3u);
    }
    atomic_store_explicit(&shm->seq_counter, seq + 2,
                          memory_order_release);  /* even → write done */
}

/* Cycle counters, end time of the cycle, and the longest cycle period
 * over the current and the previous window of 1000 cycles. */
static void rt_count_cycle(rt_loop_t *rt, uint64_t cycle) {
    odycat_header_t *shm = rt->shm;
    shm->cycle_count = cycle;
    shm->last_cycle_ns = monotonic_ns();
    if (rt->prev_cycle_ns && shm->last_cycle_ns - rt->prev_cycle_ns > rt->win_max_ns)
        rt->win_max_ns = shm->last_cycle_ns - rt->prev_cycle_ns;
    rt->prev_cycle_ns = shm->last_cycle_ns;
    if (++rt->win_count >= 1000) {
        rt->prev_win_max_ns = rt->win_max_ns;
        rt->win_max_ns = 0;
        rt->win_count = 0;
    }
}

/* Say why a slave left OP, when the cause is almost certainly a late
 * cycle. Printing here is fine: without SCHED_FIFO this loop is not
 * real-time anyway. */
static void explain_slave_lost(const rt_loop_t *rt, const slave_cfg_t *s) {
    if (!s->dc_enabled || (rt->rt_flags & ODYCAT_RT_SCHED_FIFO))
        return;
    uint64_t longest = rt->win_max_ns > rt->prev_win_max_ns
                     ? rt->win_max_ns : rt->prev_win_max_ns;
    fprintf(stderr, "WARNING: slave %u:%d (%s) left OP. It uses DC "
            "(Sync0 %u us) and the engine runs without real-time "
            "scheduling; the longest recent cycle took %.1f ms, which "
            "can leave a Sync0 period without data. Run the engine with "
            "real-time scheduling (sudo, or CAP_SYS_NICE), or remove "
            "\"dc_sync\" from this slave if it is not required.\n",
            s->alias, s->position, s->label, sync0_cycle_ns(s, rt->cfg->cycle_us) / 1000u,
            longest / 1e6);
}

static void refresh_slave_status(rt_loop_t *rt, int i, uint64_t cycle) {
    const slave_cfg_t *s = &rt->cfg->slaves[i];
    ec_slave_config_state_t ss;
    ecrt_slave_config_state(s->sc, &ss);
    odycat_slave_status_t *st = &rt->status[i];
    uint64_t *word = &rt->slaves_not_op[i / 64];
    uint64_t  bit  = 1ULL << (i % 64);
    bool was_op = !(*word & bit);

    /* A slave out of OP counts as back only after OP_HOLD_MS in OP: some
     * accept OP and refuse it a moment later, again on every retry of
     * IgH's, and would otherwise flap in and out (and count a drop each
     * time). Scripts see it out of OP until then. */
    bool op = ss.online && ss.operational;
    if (!op)
        rt->op_since[i] = 0;
    else if (!was_op) {
        if (!rt->op_since[i])
            rt->op_since[i] = cycle;
        op = (cycle - rt->op_since[i]) * (uint64_t)rt->cfg->cycle_us
             >= (uint64_t)OP_HOLD_MS * 1000u;
    }

    uint16_t al_code = st->al_code;
    igh_poll_al_code(s->al_req, op, &al_code);
    st->al_code     = al_code;
    st->al_state    = (uint8_t)ss.al_state;
    st->online      = (uint8_t)ss.online;
    st->operational = (uint8_t)op;
    st->refreshed   = 1;

    if (op) {
        if (!was_op) {
            rt->op_since[i] = 0;
            rt->not_op_count--;
            trace_event(cycle, TRACE_EV_SLAVE_BACK, (uint16_t)i, ss.al_state);
        }
        *word &= ~bit;
        return;
    }
    *word |= bit;
    if (!was_op)
        return;

    rt->not_op_count++;
    trace_event(cycle, TRACE_EV_SLAVE_LOST, (uint16_t)i,
                ss.al_state | (uint32_t)ss.online << 8);
    explain_slave_lost(rt, s);
    /* Left OP: every output buffer written before is now stale, so the
     * slave gets zeros until a script writes again (send_slave_outputs) */
    st->drop_count++;
}

/* A slave that refused a state change carries IgH's error flag, and IgH
 * does not try it again until a state is requested: ask for OP again while
 * any slave is out of OP, backing off from RETRY_FIRST_MS to RETRY_MAX_MS
 * (every attempt is a burst in the kernel log). Slaves in OP are not
 * touched by it; the back-off starts over once every slave is in OP. */
static void rt_retry_down_slaves(rt_loop_t *rt, uint64_t cycle) {
    if (!rt->not_op_count) {
        rt->retry_ms = 0;
        rt->retry_at = 0;
        return;
    }
    uint64_t cycles_per_ms = 1000u / (uint64_t)rt->cfg->cycle_us;
    if (!cycles_per_ms)
        cycles_per_ms = 1;                       /* cycles of 2 ms and more */
    uint64_t per_cycle_ms = (uint64_t)rt->cfg->cycle_us / 1000u;
    if (!rt->retry_at) {
        rt->retry_ms = RETRY_FIRST_MS;
    } else if (cycle < rt->retry_at) {
        return;
    } else {
        ecrt_master_reset(rt->master);
        rt->retry_ms = rt->retry_ms * 2 > RETRY_MAX_MS ? RETRY_MAX_MS : rt->retry_ms * 2;
    }
    rt->retry_at = cycle + (per_cycle_ms ? rt->retry_ms / per_cycle_ms
                                         : rt->retry_ms * cycles_per_ms);
}

static void refresh_master_status(rt_loop_t *rt, uint64_t cycle) {
    ec_master_state_t ms;
    ecrt_master_state(rt->master, &ms);
    rt->slaves_responding       = ms.slaves_responding;
    rt->shm->slaves_responding  = ms.slaves_responding;
    rt->shm->master_al_states   = ms.al_states;
    rt->shm->link_up            = ms.link_up;
    if (ms.link_up != rt->link_up) {
        rt->link_up = ms.link_up;
        trace_event(cycle, rt->link_up ? TRACE_EV_LINK_UP : TRACE_EV_LINK_DOWN, 0xFFFF, 0);
    }
}

/* One state query per cycle, round-robin over the slaves and then the
 * master. Both are lock-free in IgH. */
static void rt_refresh_status(rt_loop_t *rt, uint64_t cycle) {
    if (rt->status_idx < rt->cfg->num_slaves) {
        refresh_slave_status(rt, rt->status_idx++, cycle);
    } else {
        refresh_master_status(rt, cycle);
        rt->status_idx = 0;
    }
    if (rt->not_op_count || rt->slaves_responding < (uint32_t)rt->cfg->num_slaves)
        atomic_fetch_or(&rt->shm->error_flags, ODYCAT_ERR_SLAVE_LOST);
    else
        atomic_fetch_and(&rt->shm->error_flags, ~ODYCAT_ERR_SLAVE_LOST);
}

void rt_loop(rt_loop_t *rt) {
    printf("OdyCAT: entering RT loop (cycle = %d µs).\n", rt->cfg->cycle_us);
    /* Slaves the bus started without are not in OP from the first cycle:
     * no drop is counted for them, they only come back (TRACE_EV_SLAVE_BACK) */
    for (int i = 0; i < rt->cfg->num_slaves; i++) {
        if (!rt->cfg->slaves[i].down)
            continue;
        rt->slaves_not_op[i / 64] |= 1ULL << (i % 64);
        rt->not_op_count++;
        rt->status[i].al_code = rt->cfg->slaves[i].al_code;
    }
    clock_gettime(CLOCK_MONOTONIC, &rt->next);
    trace_event(0, TRACE_EV_ENGINE_START, 0xFFFF, rt->rt_flags);

    while (!stop_requested()) {
        uint64_t cycle = rt->shm->cycle_count + 1;     /* the cycle being run */

        rt_wait_for_slot(rt, cycle);

        /* Receive the frame, process the domain, check the WKC */
        ecrt_master_receive(rt->master);
        ecrt_domain_process(rt->domain);
        ec_domain_state_t dom_state;
        ecrt_domain_state(rt->domain, &dom_state);
        rt_check_wkc(rt, cycle, dom_state.working_counter);

        exchange_process_data(rt);

        igh_sync_dc_clocks(rt->master, ts_ns(rt->next));   /* after receive: see igh.h */
        ecrt_domain_queue(rt->domain);
        ecrt_master_send(rt->master);

        rt_count_cycle(rt, cycle);
        rt_refresh_status(rt, cycle);
        rt_retry_down_slaves(rt, cycle);

        /* Trace: record this cycle (after the send, off the frame's path) */
        trace_slot(rt->cfg, rt->shm_base, rt->domain_pd, cycle, rt->shm->last_cycle_ns,
                   dom_state.working_counter,
                   atomic_load_explicit(&rt->shm->error_flags, memory_order_relaxed),
                   rt->slaves_not_op);
    }
}
