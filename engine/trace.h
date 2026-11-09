/*
 * OdyCAT Engine — the trace ring: one slot per cycle and an event ring,
 * in <run dir>/<bus>_trace (layout in shm_abi.h). No IgH dependency.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_TRACE_H
#define ODYCAT_TRACE_H

#include <stdint.h>

#include "config.h"

/* Create the trace region (nothing when history_s is 0). */
void trace_create(const odycat_config_t *cfg);

/* Append one engine event. Cheap and bounded: also called in the RT loop. */
void trace_event(uint64_t cycle, uint16_t type, uint16_t slave, uint32_t arg);

/* Record one cycle: inputs as received (already in shm), outputs as sent
 * (from the domain), header values and the not-in-OP bitmap. */
void trace_slot(const odycat_config_t *cfg, const uint8_t *shm_base,
                const uint8_t *domain_pd, uint64_t cycle, uint64_t mono_ns,
                uint32_t wkc, uint32_t error_flags, const uint64_t *not_op);

/* Cycles skipped since the start, for readers of the trace header. */
void trace_set_skipped(uint64_t total);

/* Unlink the region, if this engine created it. */
void trace_remove(void);

#endif
