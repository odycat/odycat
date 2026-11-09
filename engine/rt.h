/*
 * OdyCAT Engine — real-time setup and the bus cycle loop.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_RT_H
#define ODYCAT_RT_H

#include <stdbool.h>
#include <stdint.h>
#include <time.h>

#include <ecrt.h>

#include "config.h"
#include "shm_abi.h"

typedef struct {
    const odycat_config_t *cfg;
    ec_master_t           *master;
    ec_domain_t           *domain;
    uint8_t               *domain_pd;
    uint8_t               *shm_base;
    odycat_header_t       *shm;
    odycat_slave_status_t *status;
    uint32_t               rt_flags;

    /* Carried from one cycle to the next */
    struct timespec next;              /* absolute wake-up of the current cycle */
    uint64_t skipped_total;            /* cycles skipped, late by a full period */
    uint64_t prev_cycle_ns;            /* end of the previous cycle, 0 = none */
    uint64_t win_max_ns, prev_win_max_ns;  /* longest cycle period, over two windows */
    int      win_count;
    bool     wkc_wrong;                /* for WKC events: edges only */
    uint32_t link_up;                  /* for link events: edges only */
    uint32_t slaves_responding;
    int      status_idx;               /* 0..n-1 = slave, n = master */
    uint64_t slaves_not_op[(MAX_SLAVES + 63) / 64];  /* bit i = slave i offline or not OP */
    int      not_op_count;             /* bits set in slaves_not_op */
    uint64_t op_since[MAX_SLAVES];     /* cycle a not-OP slave was seen in OP again, 0 = not */
    uint64_t retry_at;                 /* cycle of the next ecrt_master_reset, 0 = none due */
    uint32_t retry_ms;                 /* current back-off */
} rt_loop_t;

/* Lock memory and request SCHED_FIFO. Unless require_rt is set, a failure
 * is a warning and the engine runs without it. Returns ODYCAT_RT_* flags. */
uint32_t rt_enter(const odycat_config_t *cfg);

/* Run bus cycles until a stop is requested (see util.h). */
void rt_loop(rt_loop_t *rt);

#endif
