/*
 * OdyCAT Engine — the shared-memory regions (plain files on tmpfs in the run
 * directory, /run/odycat or $ODYCAT_RUN_DIR), the shm group and the
 * per-bus instance lock. No IgH dependency.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_SHM_H
#define ODYCAT_SHM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#include "config.h"
#include "shm_abi.h"

/* <run dir>/<bus>_shared, _config, _trace and .lock; dies on an
 * bus name that is not a plain file name. */
void shm_set_paths(const char *bus);
const char *shm_run_dir(void);
const char *shm_data_path(void);
const char *shm_config_path(void);
const char *shm_trace_path(void);

/* The group that may open the regions; dies if it does not exist. */
void shm_resolve_group(const char *group);

/* Create the run directory if needed and check it (after shm_resolve_group). */
void shm_prepare_run_dir(void);

/* One engine per bus: dies if another one holds the lock. */
void shm_acquire_instance_lock(const char *bus);
void shm_release_instance_lock(void);

/* Create a region file exclusively (a stale one left by an engine is
 * replaced, anything else refused), give it to the shm group with `mode`,
 * or die. Sets *created (if not NULL) once it exists. */
int shm_create_region(const char *path, mode_t mode, bool *created);

/* The data region: header (magic, layout version, sizes), slave data blocks
 * and the slave status table, zeroed. Returns its base. */
uint8_t *shm_create_data(const odycat_config_t *cfg);

/* The config region: odycat_config_header_t (engine version, config
 * SHA-256, ...) then the config file as loaded. Frees cfg->text. */
void shm_publish_config(odycat_config_t *cfg);

/* Version and git describe the engine was built with. */
const char *shm_engine_version(void);
const char *shm_engine_git(void);
const char *shm_engine_support(void);

/* Header fields and status table entries that are fixed once in OP */
void shm_publish_bus_info(odycat_header_t *shm, odycat_slave_status_t *status,
                          const odycat_config_t *cfg);

/* Unmap the data region and remove the data and config regions. */
void shm_remove(uint8_t *base, size_t size);

static inline odycat_slave_status_t *shm_status_table(uint8_t *shm_base,
                                                      const odycat_config_t *cfg) {
    return (odycat_slave_status_t *)(shm_base + cfg->status_offset);
}

#endif
