/*
 * OdyCAT Engine — PDO byte chunks and the placement of the slave blocks
 * in the data region. No IgH dependency. odycat/_config.py applies the
 * same rules.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_LAYOUT_H
#define ODYCAT_LAYOUT_H

#include "config.h"

/* Walk a PDO block's entries and call back once per byte-aligned chunk.
 * A chunk starts at a byte boundary and ends at the next one; entries with
 * index 0 are gaps. Returns the trailing bits (non-zero: not byte-aligned). */
typedef void (*chunk_fn)(void *ctx, const pdo_entry_cfg_t *ents,
                         int first, int last, int bits);

int walk_pdo_chunks(const pdo_entry_cfg_t *ents, int n, chunk_fn fn, void *ctx);

/* True if a chunk holds data (not only gaps). Chunks made only of gaps are
 * not mirrored into SHM. */
bool chunk_has_data(const pdo_entry_cfg_t *ents, int first, int last);

/* chunk_fn adding the bytes of data chunks to *(int *)ctx */
void count_chunk_bytes(void *ctx, const pdo_entry_cfg_t *ents,
                       int first, int last, int bits);

/* Place the slave blocks in config order (first at SHM_HEADER_SIZE, each
 * 8-byte aligned), the output buffers, the group words, the output table
 * and the status table (see shm_abi.h); sets every offset and shm_size.
 * Needs the sync groups assigned. */
void layout_place_blocks(odycat_config_t *cfg);

/* The WKC IgH returns for the domain once registered (uses chunk_count). */
uint32_t layout_expected_wkc(const odycat_config_t *cfg);

#endif
