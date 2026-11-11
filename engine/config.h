/*
 * OdyCAT Engine — the config model, filled by the JSON parser.
 * No IgH dependency (the IgH handle of each slave is only declared).
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_CONFIG_H
#define ODYCAT_CONFIG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "shm_abi.h"

#define MAX_SLAVES            512
#define MAX_SDOS_PER_SLAVE    32
#define MAX_PDOS_PER_DIR      32         /* PDOs assigned per SM direction */
#define MAX_PDO_ENTRIES       256        /* entries per direction, all PDOs */
#define MAX_COPY_CHUNKS       MAX_PDO_ENTRIES
#define MAX_LABEL_LEN         64

#define HISTORY_S_DEFAULT     10.0
#define HISTORY_S_MAX         60.0

struct ec_slave_config;                  /* IgH slave config, set by igh.c */

/* ------------------------------------------------------------------ */
/*  Per-PDO entry (for registration)                                   */
/* ------------------------------------------------------------------ */

typedef struct {
    uint16_t index;
    uint8_t  subindex;
    uint8_t  bits;
} pdo_entry_cfg_t;

/* ------------------------------------------------------------------ */
/*  Byte-aligned run of PDO data copied between domain and SHM.        */
/*  Sub-byte entries (e.g. 1-bit digital channels) are grouped until   */
/*  the bit position is byte-aligned again, so the RT loop only ever   */
/*  does whole-byte memcpy.                                            */
/* ------------------------------------------------------------------ */

typedef struct {
    unsigned int domain_off;
    unsigned int size;    /* bytes */
} copy_chunk_t;

/* ------------------------------------------------------------------ */
/*  PDOs of one direction. Each PDO names its sync manager (default:   */
/*  inputs SM3, outputs SM2; slaves without a mailbox use SM0/SM1, and */
/*  some spread a direction over two); the PDOs of one sync manager    */
/*  follow each other and fill whole bytes.                            */
/*  The entries of all PDOs sit in one array, in wire order; each PDO  */
/*  names its slice. Byte alignment applies per sync manager, so PDOs  */
/*  of 1 bit each (one per digital channel) are valid.                 */
/* ------------------------------------------------------------------ */

typedef struct {
    uint16_t index;
    int      first;       /* into pdo_dir_t.entries */
    int      count;
    uint8_t  sm;          /* sync manager the PDO is assigned to */
} pdo_cfg_t;

typedef struct {
    pdo_cfg_t       pdos[MAX_PDOS_PER_DIR];
    int             pdo_count;
    uint8_t         sm;            /* the direction's default sync manager */
    pdo_entry_cfg_t entries[MAX_PDO_ENTRIES];
    int             entry_count;
    int             size;          /* SHM bytes */
    /* Filled at runtime by domain registration */
    copy_chunk_t    chunks[MAX_COPY_CHUNKS];
    int             chunk_count;
} pdo_dir_t;

/* ------------------------------------------------------------------ */
/*  Per-SDO entry                                                      */
/* ------------------------------------------------------------------ */

typedef struct {
    uint16_t index;
    uint8_t  subindex;
    uint8_t  size;        /* bytes */
    uint8_t  data[8];     /* value in host byte order (IgH converts) */
    char     shown[48];   /* the value as printed at startup */
} sdo_entry_t;

/* ------------------------------------------------------------------ */
/*  Per-slave config                                                   */
/* ------------------------------------------------------------------ */

typedef struct {
    /* Addressing follows IgH: alias 0 -> position is the absolute ring
     * position; alias N -> position is the offset from the slave whose
     * station alias is N (what `ethercat slaves` shows as "N:position"). */
    uint16_t     alias;
    int          position;
    int          ring_pos;             /* absolute position, resolved by the bus scan */
    char         label[MAX_LABEL_LEN];
    uint32_t     vendor_id;
    uint32_t     product_code;

    /* PDO mapping: tx = inputs ("txpdo" in the config), rx = outputs
     * ("rxpdo") */
    pdo_dir_t    tx;
    pdo_dir_t    rx;

    /* SDOs */
    sdo_entry_t  sdos[MAX_SDOS_PER_SLAVE];
    int          sdo_count;

    /* SHM, placed by layout_place_blocks() */
    int          shm_offset;           /* byte offset from shm base */
    int          out_buf_offset;       /* output buffer 0 (outputs only) */
    int          out_buf_stride;       /* bytes per output buffer */

    /* Sync group ("sync_group" in the config; a group of its own if not
     * given). Only slaves with outputs belong to one. */
    char         sync_group[MAX_LABEL_LEN];
    int          out_group;            /* -1 without outputs */
    int          out_member;           /* slot in the group's words */

    /* DC synchronisation (optional) */
    bool     dc_enabled;
    uint16_t dc_assign_activate;   /* ESI AssignActivate word, e.g. 0x0300 */
    uint32_t dc_sync0_cycle_ns;    /* 0 = inherit bus cycle_us * 1000 */
    int32_t  dc_sync0_shift_ns;
    uint32_t dc_sync1_cycle_ns;
    int32_t  dc_sync1_shift_ns;

    /* IgH handles */
    struct ec_slave_config *sc;
    struct ec_reg_request  *al_req;    /* reads the AL status code (0x0134) */

    /* Start-up: the engine runs without a slave that is missing or did not
     * reach OP (unless --strict); al_code is its last AL status code. */
    bool     down;
    uint16_t al_code;
} slave_cfg_t;

typedef struct {
    int      count;
    uint16_t slaves[ODYCAT_GROUP_MAX_MEMBERS];   /* config indices, member order */
} out_group_t;

/* ------------------------------------------------------------------ */
/*  The whole config. Large (the slave table): keep it in static       */
/*  storage, never on the stack.                                       */
/* ------------------------------------------------------------------ */

typedef struct {
    char        bus[64];
    int         cycle_us;
    double      history_s;
    bool        require_rt;
    unsigned    master_index;          /* IgH master: one per NIC */
    char        shm_group[64];

    slave_cfg_t slaves[MAX_SLAVES];
    int         num_slaves;
    out_group_t groups[MAX_SLAVES];    /* sync groups of the slaves with outputs */
    int         group_count;

    size_t      shm_size;              /* data region, page-aligned */
    size_t      blocks_end;            /* end of the slave blocks */
    size_t      groups_offset;         /* pub words (u64 per group) */
    size_t      busy_offset;           /* busy words (u64 per group) */
    size_t      out_table_offset;      /* odycat_out_entry_t per slave */
    size_t      status_offset;         /* slave status table in the data region */

    char       *text;                  /* the config file as read, for Python; */
    size_t      text_len;              /* freed once published */
} odycat_config_t;

/* Read and validate a config file into *cfg, or die naming what is wrong. */
void config_load(odycat_config_t *cfg, const char *path);

/* Sync0 period of a DC slave: its own, or the bus cycle. */
static inline uint32_t sync0_cycle_ns(const slave_cfg_t *s, int cycle_us) {
    return s->dc_sync0_cycle_ns ? s->dc_sync0_cycle_ns : (uint32_t)cycle_us * 1000u;
}

#endif
