/*
 * OdyCAT Engine — the link to the IgH master.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_IGH_H
#define ODYCAT_IGH_H

#include <stdbool.h>
#include <stdint.h>

#include <ecrt.h>

#include "config.h"

#define PREOP_TIMEOUT_S       5
/* IgH configures slaves one at a time and its FSM only advances on cycles
 * we send, so CoE PDO remapping of several slaves can take a few seconds. */
#define SAFEOP_TIMEOUT_S      15
#define OP_TIMEOUT_S          10
/* How long the bus must stay in OP before the engine starts: some slaves
 * accept OP and refuse it a moment later (no Sync0, a watchdog). */
#define OP_HOLD_MS            500
/* While a slave is out of OP the engine asks IgH to bring every slave to OP
 * again (ecrt_master_reset), first after RETRY_FIRST_MS, then doubling up to
 * RETRY_MAX_MS. IgH retries a slave that drops out on its own by itself, but
 * one that refused a state change only when a state is requested again. */
#define RETRY_FIRST_MS        1000
#define RETRY_MAX_MS          60000

ec_master_t *igh_request_master(const odycat_config_t *cfg);

/* List the slaves IgH found and check them against the config; resolves
 * every slave's ring_pos. A different device at a configured address is
 * fatal; a configured slave that is missing is fatal only with `strict`
 * (otherwise it is marked down); slaves not in the config are left alone. */
void igh_scan_bus(ec_master_t *master, odycat_config_t *cfg, bool strict);

/* Create the IgH slave config, queue what the slave needs on its way to
 * OP, and register its PDO entries in the domain (fills the copy chunks). */
void igh_configure_slave(ec_master_t *master, ec_domain_t *domain,
                         const odycat_config_t *cfg, slave_cfg_t *s);

/* Run bus cycles until every slave not marked down is in al_state and has
 * stayed there for hold_ms (a slave can accept a state and refuse it a
 * moment later). A slave that misses the timeout or drops out during the
 * hold is marked down and the bus goes on without it; with `strict` the
 * engine stops instead (`hint` is added to that error). Returns the number
 * of slaves down. */
int igh_bring_bus_to(ec_master_t *master, ec_domain_t *domain, odycat_config_t *cfg,
                     uint8_t al_state, const char *name, int timeout_s, int hold_ms,
                     bool strict, const char *hint);

/* The slaves marked down, with their state and AL status code. */
void igh_report_down_slaves(ec_master_t *master, ec_domain_t *domain,
                            odycat_config_t *cfg);

void igh_report_slave_states(ec_master_t *master, const odycat_config_t *cfg);

/* The AL status code register (0x0134) of a slave not in OP, read without
 * blocking through its register request (IgH's FSM does the read): call
 * it every so often. *code keeps the last non-zero code until the slave is
 * in OP again (in_op), then 0. RT-safe. */
void igh_poll_al_code(ec_reg_request_t *req, bool in_op, uint16_t *code);
const char *igh_al_code_text(uint16_t code);

/* DC clock discipline: application time, then reference and slave clocks.
 * Call it after ecrt_master_receive(): the sync datagrams of the previous
 * cycle are still waiting for their reply until then, and queueing them
 * again first makes IgH drop that reply (2 "datagrams UNMATCHED" a cycle). */
static inline void igh_sync_dc_clocks(ec_master_t *master, uint64_t app_time_ns) {
    ecrt_master_application_time(master, app_time_ns);
    ecrt_master_sync_reference_clock(master);
    ecrt_master_sync_slave_clocks(master);
}

#endif
