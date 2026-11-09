/*
 * OdyCAT Engine — PDO byte chunks and the placement of the slave blocks.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include "layout.h"
#include "shm_abi.h"
#include "util.h"

int walk_pdo_chunks(const pdo_entry_cfg_t *ents, int n, chunk_fn fn, void *ctx) {
    int start = 0, bits = 0;
    for (int e = 0; e < n; e++) {
        bits += ents[e].bits;
        if (bits % 8 == 0) {
            if (fn) fn(ctx, ents, start, e, bits);
            start = e + 1;
            bits = 0;
        }
    }
    return bits;   /* non-zero → block not byte-aligned */
}

bool chunk_has_data(const pdo_entry_cfg_t *ents, int first, int last) {
    for (int e = first; e <= last; e++)
        if (ents[e].index) return true;
    return false;
}

void count_chunk_bytes(void *ctx, const pdo_entry_cfg_t *ents,
                       int first, int last, int bits) {
    if (chunk_has_data(ents, first, last))
        *(int *)ctx += bits / 8;
}

static size_t align_up_64(size_t v) {
    return (v + 63) & ~(size_t)63;
}

void layout_place_blocks(odycat_config_t *cfg) {
    int next_auto_offset = SHM_HEADER_SIZE;
    size_t shm_size = 0;
    for (int i = 0; i < cfg->num_slaves; i++) {
        slave_cfg_t *s = &cfg->slaves[i];
        s->shm_offset = align_up_8(next_auto_offset);
        size_t end = (size_t)s->shm_offset
                   + (size_t)s->tx.size + (size_t)s->rx.size;
        next_auto_offset = align_up_8((int)end);
        if (end > shm_size) shm_size = end;
    }
    if (shm_size < SHM_HEADER_SIZE) shm_size = SHM_HEADER_SIZE;
    cfg->blocks_end = (size_t)align_up_8((int)shm_size);

    /* Output buffers: ODYCAT_OUT_BUFFERS per slave with outputs, each on
     * cache lines of its own */
    size_t off = align_up_64(cfg->blocks_end);
    for (int i = 0; i < cfg->num_slaves; i++) {
        slave_cfg_t *s = &cfg->slaves[i];
        if (s->out_group < 0)
            continue;
        s->out_buf_stride = (int)align_up_64(ODYCAT_OUT_BUF_HEADER + (size_t)s->rx.size);
        s->out_buf_offset = (int)off;
        off += (size_t)ODYCAT_OUT_BUFFERS * (size_t)s->out_buf_stride;
    }

    /* Group words: pub (scripts write) and busy (the engine writes) on
     * separate cache lines; then the output table and the status table */
    size_t words = (size_t)cfg->group_count * sizeof(uint64_t);
    cfg->groups_offset    = align_up_64(off);
    cfg->busy_offset      = align_up_64(cfg->groups_offset + words);
    cfg->out_table_offset = align_up_64(cfg->busy_offset + words);
    cfg->status_offset    = cfg->out_table_offset
                          + (size_t)cfg->num_slaves * sizeof(odycat_out_entry_t);
    shm_size = cfg->status_offset
             + (size_t)cfg->num_slaves * sizeof(odycat_slave_status_t);

    /* Round up to page-aligned size */
    cfg->shm_size = (shm_size + 4095) & ~(size_t)4095;
}

/* Mirrors IgH's datagram_pair.c: one FMMU per direction per slave.
 * With LRW (domain has both directions) an output FMMU counts 2,
 * otherwise every FMMU counts 1. Slaves without PDOs count 0. */
uint32_t layout_expected_wkc(const odycat_config_t *cfg) {
    uint32_t n_in = 0, n_out = 0;
    for (int i = 0; i < cfg->num_slaves; i++) {
        if (cfg->slaves[i].tx.chunk_count > 0) n_in++;
        if (cfg->slaves[i].rx.chunk_count > 0) n_out++;
    }
    return (n_in && n_out) ? n_out * 2 + n_in : n_out + n_in;
}
