/*
 * OdyCAT Engine — the binary layout of the shm regions, shared with the
 * Python package (odycat/_shm.py mirrors it; the fake engine in
 * odycat/testing.py builds regions with it). Definitions only.
 * See docs/architecture.md.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_SHM_ABI_H
#define ODYCAT_SHM_ABI_H

#include <stdatomic.h>
#include <stdint.h>

/* Layout version: a new field in reserved space raises the minor (old
 * readers ignore it); anything incompatible raises the major, and readers
 * refuse a region whose major differs from theirs. */
#define ODYCAT_LAYOUT_MAJOR   3
#define ODYCAT_LAYOUT_MINOR   0

#define ODYCAT_DATA_MAGIC     0x4144434Fu   /* "OCDA": OdyCAT DAta, little-endian */
#define ODYCAT_CONFIG_MAGIC   0x4643434Fu   /* "OCCF": OdyCAT ConFig */

/* The markers that say which program wrote a region: they must never drift
 * from the four letters they spell. */
_Static_assert((ODYCAT_DATA_MAGIC & 0xFFu) == 'O' && ((ODYCAT_DATA_MAGIC >> 8) & 0xFFu) == 'C' &&
               ((ODYCAT_DATA_MAGIC >> 16) & 0xFFu) == 'D' && ((ODYCAT_DATA_MAGIC >> 24) & 0xFFu) == 'A',
               "ODYCAT_DATA_MAGIC must spell \"OCDA\" little-endian");
_Static_assert((ODYCAT_CONFIG_MAGIC & 0xFFu) == 'O' && ((ODYCAT_CONFIG_MAGIC >> 8) & 0xFFu) == 'C' &&
               ((ODYCAT_CONFIG_MAGIC >> 16) & 0xFFu) == 'C' && ((ODYCAT_CONFIG_MAGIC >> 24) & 0xFFu) == 'F',
               "ODYCAT_CONFIG_MAGIC must spell \"OCCF\" little-endian");

#define SHM_HEADER_SIZE       256           /* data region header */
#define CONFIG_HEADER_SIZE    256           /* config region header */

/* EtherCAT AL states, as IgH reports them and the header publishes them */
enum { AL_PREOP = 2, AL_SAFEOP = 4, AL_OP = 8 };

#define ODYCAT_ERR_WKC_MISMATCH  (1u << 0)
#define ODYCAT_ERR_SLAVE_LOST    (1u << 1)

#define ODYCAT_RT_SCHED_FIFO     (1u << 0)   /* rt_flags: SCHED_FIFO active */
#define ODYCAT_RT_MEMLOCK        (1u << 1)   /* rt_flags: memory locked */

#define ODYCAT_FEAT_TRACE        (1u << 0)   /* features: the trace region exists */

/* ------------------------------------------------------------------ */
/*  Data region <run dir>/<bus>_shared: the header (256 bytes),  */
/*  the slave blocks, the output buffers, the sync-group words, the    */
/*  output table and the status table. The first cache line holds what */
/*  readers look at every cycle.                                       */
/*                                                                     */
/*  Slave block: its inputs as received, then its outputs as sent in   */
/*  the last cycle, both written by the engine inside the inputs       */
/*  seqlock (one consistent snapshot of the bus).                      */
/*                                                                     */
/*  Outputs are written by scripts into ODYCAT_OUT_BUFFERS buffers per */
/*  slave, never into what the engine is sending (#54):                */
/*  - every slave with outputs belongs to one sync group (by default a */
/*    group of its own); the group's pub word holds 2 bits per member, */
/*    the index of the member's published buffer;                     */
/*  - a writer, holding the group's lock (an OFD lock on byte <group>  */
/*    of this file), fills a buffer that is neither published nor in   */
/*    the engine's busy word, then publishes all its members at once   */
/*    with one 8-byte store of the pub word;                           */
/*  - every cycle the engine loads pub, stores it into busy, checks    */
/*    pub again (retrying if it moved), and only then copies;          */
/*  - a buffer whose epoch is not the slave's drop_count is stale (the */
/*    slave left OP since it was written): the engine sends zeros.     */
/* ------------------------------------------------------------------ */

#define ODYCAT_OUT_BUFFERS        3        /* per slave with outputs */
#define ODYCAT_GROUP_MAX_MEMBERS  32       /* 2 bits each in a 64-bit word */
#define ODYCAT_NO_GROUP           0xFFFFu  /* output table: slave without outputs */
#define ODYCAT_OUT_BUF_HEADER     8        /* odycat_out_buf_header_t, then the outputs */

typedef struct __attribute__((aligned(64))) {
    /* cache line 0: read every cycle */
    uint32_t          magic;             /* 0   ODYCAT_DATA_MAGIC, written first */
    uint16_t          layout_major;      /* 4   */
    uint16_t          layout_minor;      /* 6   */
    uint32_t          header_size;       /* 8   SHM_HEADER_SIZE: the first block's offset */
    uint32_t          features;          /* 12  ODYCAT_FEAT_* */
    _Atomic uint32_t  seq_counter;       /* 16  inputs seqlock: odd while copying */
    uint32_t          reserved20;        /* 20  */
    uint64_t          cycle_count;       /* 24  cycles run since the start, never wraps */
    uint64_t          last_cycle_ns;     /* 32  CLOCK_MONOTONIC at the end of the last cycle */
    uint32_t          bus_state;         /* 40  AL_OP once the bus is up */
    _Atomic uint32_t  error_flags;       /* 44  ODYCAT_ERR_* for the last cycle */
    uint32_t          work_counter;      /* 48  */
    uint32_t          expected_wkc;      /* 52  */
    uint32_t          wkc_wrong_streak;  /* 56  consecutive cycles with a wrong WKC, 0 when right */
    uint32_t          reserved0;         /* 60  */
    /* cache line 1: counters and status */
    uint64_t          wkc_wrong_cycles;  /* 64  cycles with a wrong WKC since the start */
    uint64_t          skipped_cycles;    /* 72  cycles skipped since the start (see the RT loop) */
    uint32_t          status_offset;     /* 80  slave status table, 0 = not published yet */
    uint32_t          status_count;      /* 84  entries in the table */
    uint32_t          slaves_responding; /* 88  from ecrt_master_state */
    uint32_t          master_al_states;  /* 92  OR of all slaves' AL states */
    uint32_t          link_up;           /* 96  */
    uint32_t          rt_flags;          /* 100 ODYCAT_RT_*, set before the RT loop */
    uint32_t          cycle_us;          /* 104 bus cycle */
    uint32_t          out_table_offset;  /* 108 output table (odycat_out_entry_t per slave) */
    uint32_t          group_count;       /* 112 sync groups */
    uint32_t          groups_offset;     /* 116 pub words: u64 per group, written by scripts */
    uint32_t          busy_offset;       /* 120 busy words: u64 per group, written by the engine */
    uint32_t          reserved1;         /* 124 */
    /* cache lines 2-3 */
    uint64_t          out_group_skips;   /* 128 times a group kept last cycle's outputs */
    uint8_t           reserved2[120];    /* 136 */
} odycat_header_t;

_Static_assert(sizeof(odycat_header_t) == SHM_HEADER_SIZE,
               "data header must be exactly 256 bytes");

/* ------------------------------------------------------------------ */
/*  Output buffers and the output table, written once when the region  */
/*  is created (config order, one entry per slave).                    */
/* ------------------------------------------------------------------ */

typedef struct {
    uint32_t epoch;        /* 0  drop_count of the slave when the buffer was filled */
    uint32_t reserved;     /* 4  */
} odycat_out_buf_header_t;  /* then the outputs, packed as in the slave block */

_Static_assert(sizeof(odycat_out_buf_header_t) == ODYCAT_OUT_BUF_HEADER,
               "output buffer header must be 8 bytes");

typedef struct {
    uint32_t buf_offset;   /* 0  buffer 0 in the data region (buffer k at + k * buf_stride) */
    uint32_t buf_stride;   /* 4  bytes per buffer, a multiple of 64 */
    uint16_t group;        /* 8  sync group, ODYCAT_NO_GROUP without outputs */
    uint8_t  member;       /* 10 slot in the group's words (bits 2*member, 2*member+1) */
    uint8_t  reserved0;    /* 11 */
    uint32_t reserved1;    /* 12 */
} odycat_out_entry_t;

_Static_assert(sizeof(odycat_out_entry_t) == 16, "output table entry must be 16 bytes");

/* ------------------------------------------------------------------ */
/*  Per-slave status, one entry per config slave (config order),       */
/*  placed after the last slave data block. Refreshed round-robin by   */
/*  the RT loop; every field is a single byte, written once, or an     */
/*  aligned u16 updated in one store, so readers need no seqlock.      */
/* ------------------------------------------------------------------ */

typedef struct {
    uint8_t  al_state;     /* 0  0 until first refresh */
    uint8_t  online;       /* 1  */
    uint8_t  operational;  /* 2  */
    uint8_t  refreshed;    /* 3  1 once the RT loop has written this entry */
    uint16_t ring_pos;     /* 4  absolute ring position, written at startup */
    uint16_t drop_count;   /* 6  times the slave left OP (wraps), for Python */
    uint32_t block_offset; /* 8  the slave's block in the data region, written at startup */
    uint16_t in_bytes;     /* 12 its input bytes (first in the block) */
    uint16_t out_bytes;    /* 14 its output bytes (after the inputs) */
    uint16_t al_code;      /* 16 AL status code (0x0134) while not in OP, 0 in OP */
    uint16_t reserved;     /* 18 */
} odycat_slave_status_t;

_Static_assert(sizeof(odycat_slave_status_t) == 20,
               "slave status entry must be 20 bytes");

/* ------------------------------------------------------------------ */
/*  Config region <run dir>/<bus>_config: this header, then the  */
/*  config file exactly as it was loaded (json_len bytes). Written     */
/*  once at startup.                                                   */
/* ------------------------------------------------------------------ */

typedef struct __attribute__((aligned(8))) {
    uint32_t magic;                /* 0   ODYCAT_CONFIG_MAGIC */
    uint16_t layout_major;         /* 4   */
    uint16_t layout_minor;         /* 6   */
    uint32_t header_size;          /* 8   CONFIG_HEADER_SIZE: where the JSON starts */
    uint32_t json_len;             /* 12  */
    char     engine_version[32];   /* 16  e.g. "0.1.0", NUL-terminated */
    char     engine_git[48];       /* 48  git describe of the build, NUL-terminated */
    uint8_t  config_sha256[32];    /* 96  SHA-256 of the JSON bytes */
    uint32_t engine_pid;           /* 128 */
    uint32_t support_end;          /* 132 end of the support period as YYYYMM, 0 = not set */
    uint64_t start_realtime_ns;    /* 136 CLOCK_REALTIME when the engine started */
    uint8_t  instance_id[16];      /* 144 random, new at every start */
    uint8_t  reserved[96];         /* 160 */
} odycat_config_header_t;

_Static_assert(sizeof(odycat_config_header_t) == CONFIG_HEADER_SIZE,
               "config header must be exactly 256 bytes");

/* ------------------------------------------------------------------ */
/*  Trace region <run dir>/<bus>_trace (mode 0640: Python only   */
/*  reads). A ring of one slot per cycle, history_s long, and a ring   */
/*  of engine events. See docs/architecture.md.                         */
/* ------------------------------------------------------------------ */

#define TRACE_MAGIC        0x5254434Fu   /* "OCTR": OdyCAT TRace, little-endian */
#define TRACE_VERSION      2
#define TRACE_HEADER_SIZE  128
#define TRACE_SLOT_HEADER_SIZE 64
#define TRACE_EVENTS       1024

/* The only marker in the shm layout that says which program wrote the region,
   so the constant must never drift from the four letters it spells. */
_Static_assert((TRACE_MAGIC & 0xFFu) == 'O' && ((TRACE_MAGIC >> 8) & 0xFFu) == 'C' &&
               ((TRACE_MAGIC >> 16) & 0xFFu) == 'T' && ((TRACE_MAGIC >> 24) & 0xFFu) == 'R',
               "TRACE_MAGIC must spell \"OCTR\" little-endian");

typedef struct __attribute__((aligned(8))) {
    uint32_t          magic;          /* 0  TRACE_MAGIC */
    uint32_t          version;        /* 4  TRACE_VERSION */
    uint32_t          slot_size;      /* 8  bytes per slot, multiple of 8 */
    uint32_t          slot_count;     /* 12 slots in the ring */
    uint32_t          slots_offset;   /* 16 */
    uint32_t          blocks_end;     /* 20 data-region offset where the slave blocks end */
    uint32_t          not_op_offset;  /* 24 not-in-OP bitmap within a slot */
    uint32_t          event_count;    /* 28 events in the ring */
    uint32_t          events_offset;  /* 32 */
    uint32_t          cycle_us;       /* 36 */
    _Atomic uint64_t  head;           /* 40 cycle of the newest slot, 0 = none */
    _Atomic uint64_t  event_head;     /* 48 seq of the newest event, 0 = none */
    _Atomic uint64_t  skipped;        /* 56 cycles skipped since start (see the RT loop) */
    uint32_t          header_size;    /* 64 TRACE_HEADER_SIZE */
    uint32_t          blocks_start;   /* 68 data-region offset of the first slave block */
    uint8_t           reserved[56];   /* 72 */
} odycat_trace_header_t;

_Static_assert(sizeof(odycat_trace_header_t) == TRACE_HEADER_SIZE,
               "trace header must be 128 bytes");

/* First 64 bytes of a slot. The data region's bytes blocks_start ..
 * blocks_end follow at TRACE_SLOT_HEADER_SIZE (a data-region offset X is at
 * X - blocks_start + TRACE_SLOT_HEADER_SIZE): inputs as received, outputs as
 * sent (from the domain, not from shm). Then the not-in-OP bitmap. */
typedef struct __attribute__((aligned(8))) {
    _Atomic uint64_t  cycle;          /* 0  written last; 0 while the slot is rewritten */
    uint64_t          mono_ns;        /* 8  = last_cycle_ns of that cycle */
    uint64_t          real_ns;        /* 16 CLOCK_REALTIME */
    uint32_t          work_counter;   /* 24 */
    uint32_t          error_flags;    /* 28 */
    uint8_t           reserved[32];   /* 32 */
} odycat_slot_header_t;

_Static_assert(sizeof(odycat_slot_header_t) == TRACE_SLOT_HEADER_SIZE,
               "slot header must be 64 bytes");

enum {
    TRACE_EV_ENGINE_START = 1,   /* arg = rt_flags */
    TRACE_EV_ENGINE_STOP  = 2,
    TRACE_EV_SLAVE_LOST   = 3,   /* slave left OP; arg = al_state | online << 8 */
    TRACE_EV_SLAVE_BACK   = 4,   /* slave back in OP */
    TRACE_EV_WKC_WRONG    = 5,   /* arg = working counter */
    TRACE_EV_WKC_OK       = 6,
    TRACE_EV_LINK_DOWN    = 7,
    TRACE_EV_LINK_UP      = 8,
    TRACE_EV_CYCLES_SKIPPED = 9, /* arg = cycles skipped before this one */
};

typedef struct __attribute__((aligned(8))) {
    _Atomic uint64_t  seq;            /* 0  written last; 1, 2, ... */
    uint64_t          cycle;          /* 8  cycle it happened in (0 = before the loop) */
    uint64_t          real_ns;        /* 16 CLOCK_REALTIME */
    uint16_t          type;           /* 24 TRACE_EV_* */
    uint16_t          slave;          /* 26 config index, 0xFFFF = none */
    uint32_t          arg;            /* 28 */
} odycat_trace_event_t;

_Static_assert(sizeof(odycat_trace_event_t) == 32, "trace event must be 32 bytes");

#endif
