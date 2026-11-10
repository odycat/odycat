/*
 * OdyCAT Engine — Real-Time EtherCAT Master (C11, IgH EtherCAT Master)
 *
 * Lock-free shared memory bridge between the IgH kernel master and
 * a Python control layer. See docs/architecture.md for the protocol.
 *
 *   main.c      startup order, shutdown
 *   config.c    JSON config parser                       (no IgH)
 *   layout.c    PDO byte chunks, slave block placement    (no IgH)
 *   shm.c       shm regions, group, instance lock         (no IgH)
 *   trace.c     trace ring                                (no IgH)
 *   shm_abi.h   binary layout shared with Python          (no IgH)
 *   igh.c       bus scan, slave configuration, way to OP
 *   rt.c        real-time setup and the cycle loop
 *   util.c      die() with cleanup hooks, signals
 *
 * Build: make -C engine (see engine/Makefile for the flags).
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include <inttypes.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "config.h"
#include "igh.h"
#include "rt.h"
#include "shm.h"
#include "trace.h"
#include "util.h"

/* Large (the slave table): static storage, never the stack */
static odycat_config_t g_cfg;

static void shutdown_engine(const rt_loop_t *rt) {
    if (rt->skipped_total)
        printf("OdyCAT: %" PRIu64 " cycle(s) skipped: the engine was late by half a "
               "period or more (without RT scheduling, or a stall).\n", rt->skipped_total);
    printf("OdyCAT: shutting down.\n");
    /* Scripts still attached see the stop at their next access, before the
     * region goes (Python: EngineStoppedError) */
    rt->shm->bus_state = 0;
    atomic_thread_fence(memory_order_seq_cst);
    trace_event(rt->shm->cycle_count, TRACE_EV_ENGINE_STOP, 0xFFFF, 0);
    ecrt_master_deactivate(rt->master);
    shm_remove(rt->shm_base, rt->cfg->shm_size);
    trace_remove();
    ecrt_release_master(rt->master);
    shm_release_instance_lock();
}

int main(int argc, char *argv[]) {
    /* Each line out when written, also to a file or a pipe (nohup, the
     * systemd journal): the C library would otherwise hold stdout in a
     * block buffer until it fills or the engine exits, and lose it in a
     * crash. stderr is unbuffered already. */
    setvbuf(stdout, NULL, _IOLBF, 0);

    /* First, before anything is created: see install_signal_handlers() */
    install_signal_handlers();

    if (argc == 2 && !strcmp(argv[1], "--version")) {
        printf("odycat %s (git %s; %s)\n", shm_engine_version(), shm_engine_git(),
               shm_engine_support());
        return 0;
    }
    if (argc == 2 && (!strcmp(argv[1], "--help") || !strcmp(argv[1], "-h"))) {
        printf("usage: %s [--strict] [-v] <config.json>\n"
               "       %s --version\n\n"
               "Runs the EtherCAT bus described by the config file (written out in\n"
               "full: `odycat-config expand` fills in device classes). Needs\n"
               "real-time scheduling (root, or CAP_SYS_NICE and CAP_IPC_LOCK) to\n"
               "keep its cycle. See docs/getting-started.md.\n\n"
               "A configured slave that is missing or does not reach OP is reported\n"
               "and the bus runs without it (scripts get DeviceLostError on it);\n"
               "the engine keeps asking for OP (every 1 s, backing off to 1 min) and\n"
               "it joins once it stays in OP. --strict stops the engine instead, for\n"
               "commissioning and tests. A different device at a configured address\n"
               "always stops it.\n\n"
               "It prints what needs attention: slaves missing, unknown or out of\n"
               "OP, and warnings. -v (--verbose) also prints the whole bus scan, the\n"
               "startup SDOs, DC and process-data layout of every slave, each state\n"
               "reached and the shared-memory paths.\n", argv[0], argv[0]);
        return 0;
    }
    bool strict = false;
    const char *config_path = NULL;
    for (int a = 1; a < argc; a++) {
        if (!strcmp(argv[a], "--strict"))
            strict = true;
        else if (!strcmp(argv[a], "-v") || !strcmp(argv[a], "--verbose"))
            g_verbose = 1;
        else if (argv[a][0] == '-' || config_path)
            die("usage: %s [--strict] [-v] <config.json> (or --help, --version)", argv[0]);
        else
            config_path = argv[a];
    }
    if (!config_path)
        die("usage: %s [--strict] [-v] <config.json> (or --help, --version)", argv[0]);

    printf("OdyCAT: engine %s (git %s; %s)\n", shm_engine_version(), shm_engine_git(),
           shm_engine_support());
    odycat_config_t *cfg = &g_cfg;
    config_load(cfg, config_path);
    shm_set_paths(cfg->bus);
    printf("OdyCAT: loaded config — bus '%s', %d slave(s), cycle %d µs\n",
           cfg->bus, cfg->num_slaves, cfg->cycle_us);
    VPRINTF("OdyCAT: data shm    '%s'\n", shm_data_path());
    VPRINTF("OdyCAT: config shm  '%s'\n", shm_config_path());

    shm_resolve_group(cfg->shm_group);
    shm_prepare_run_dir();
    /* Prevent multiple engine processes from racing for the same bus. */
    shm_acquire_instance_lock(cfg->bus);

    uint8_t *shm_base = shm_create_data(cfg);
    odycat_header_t *shm = (odycat_header_t *)shm_base;
    shm_publish_config(cfg);
    trace_create(cfg);
    if (cfg->history_s > 0)
        shm->features |= ODYCAT_FEAT_TRACE;

    ec_master_t *master = igh_request_master(cfg);
    igh_scan_bus(master, cfg, strict);   /* identity check, before any configuration */
    ec_domain_t *domain = ecrt_master_create_domain(master);
    if (!domain)
        die("could not create PDO domain.");
    for (int i = 0; i < cfg->num_slaves; i++)
        igh_configure_slave(master, domain, cfg, &cfg->slaves[i]);

    int ret = ecrt_master_activate(master);
    if (ret)
        die("ecrt_master_activate failed (ret=%d). Check PDO mapping.", ret);
    uint8_t *domain_pd = ecrt_domain_data(domain);
    if (!domain_pd)
        die("ecrt_domain_data returned NULL.");

    igh_bring_bus_to(master, domain, cfg, AL_PREOP, "PREOP", PREOP_TIMEOUT_S, 0, strict,
                     "Check wiring, power, and slave order.");
    igh_bring_bus_to(master, domain, cfg, AL_SAFEOP, "SAFEOP", SAFEOP_TIMEOUT_S, 0, strict,
                     "Verify PDO sizes match hardware ESI.");
    igh_bring_bus_to(master, domain, cfg, AL_OP, "OP", OP_TIMEOUT_S, OP_HOLD_MS, strict,
                     "A drive that only runs with DC sync needs \"dc_sync\" "
                     "(odycat-config scan writes it when the slave says so).");
    igh_report_slave_states(master, cfg);
    igh_report_down_slaves(master, domain, cfg);
    shm_publish_bus_info(shm, shm_status_table(shm_base, cfg), cfg);

    uint32_t rt_flags = rt_enter(cfg);
    shm->rt_flags = rt_flags;

    rt_loop_t rt = {
        .cfg = cfg,
        .master = master, .domain = domain, .domain_pd = domain_pd,
        .shm_base = shm_base, .shm = shm, .status = shm_status_table(shm_base, cfg),
        .rt_flags = rt_flags,
        .link_up = 1, .slaves_responding = (uint32_t)cfg->num_slaves,
    };
    rt_loop(&rt);
    shutdown_engine(&rt);
    return 0;
}
