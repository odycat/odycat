/*
 * OdyCAT Engine — the link to the IgH master: bus scan, slave
 * configuration (PDOs, SDOs, DC, free-run), PDO registration and the way
 * to OP.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "igh.h"
#include "layout.h"
#include "shm_abi.h"
#include "util.h"

#define EC_MAX_SLAVES_SCAN    1024       /* configured + unconfigured */

/* ------------------------------------------------------------------ */
/*  Domain registration                                                */
/* ------------------------------------------------------------------ */

typedef struct {
    slave_cfg_t  *s;
    ec_domain_t  *domain;
    const char   *name;
    copy_chunk_t *chunks;
    int          *count;
} reg_ctx_t;

static void register_chunk(void *vctx, const pdo_entry_cfg_t *ents,
                           int first, int last, int bits) {
    reg_ctx_t *ctx = vctx;
    if (!chunk_has_data(ents, first, last))
        return;   /* pure gap: present on the wire, not mirrored into SHM */

    /* Register the first real entry; derive the chunk start from its bit offset. */
    int k = first, lead_bits = 0;
    while (!ents[k].index) lead_bits += ents[k++].bits;

    unsigned int bit_pos = 0;
    int off = ecrt_slave_config_reg_pdo_entry(ctx->s->sc, ents[k].index,
                                              ents[k].subindex, ctx->domain,
                                              &bit_pos);
    if (off < 0)
        die("slave %d (%s): %s entry 0x%04X:%02X registration failed (ret=%d)",
            ctx->s->position, ctx->s->label, ctx->name,
            ents[k].index, ents[k].subindex, off);
    if ((int)bit_pos != lead_bits % 8)
        die("slave %d (%s): %s entry 0x%04X:%02X at bit %u, expected %d. "
            "PDO layout does not match the configured entries.",
            ctx->s->position, ctx->s->label, ctx->name,
            ents[k].index, ents[k].subindex, bit_pos, lead_bits % 8);
    if (*ctx->count >= MAX_COPY_CHUNKS)
        die("slave %d (%s): too many PDO copy chunks",
            ctx->s->position, ctx->s->label);

    copy_chunk_t *c = &ctx->chunks[(*ctx->count)++];
    c->domain_off = (unsigned int)off - (unsigned int)(lead_bits / 8);
    c->size       = (unsigned int)(bits / 8);
}

/* Register all entries of one direction. Chunks may span PDO boundaries:
 * IgH places the PDOs of a sync manager back to back in the domain. */
static void register_pdo_dir(slave_cfg_t *s, ec_domain_t *domain,
                             const char *name, pdo_dir_t *dir) {
    dir->chunk_count = 0;
    reg_ctx_t ctx = { s, domain, name, dir->chunks, &dir->chunk_count };
    walk_pdo_chunks(dir->entries, dir->entry_count, register_chunk, &ctx);
}

/* Fill IgH's PDO descriptions for one direction; returns the PDO count. */
static unsigned int build_pdo_info(const pdo_dir_t *dir,
                                   ec_pdo_entry_info_t *ents,
                                   ec_pdo_info_t *pdos) {
    for (int e = 0; e < dir->entry_count; e++) {
        ents[e].index      = dir->entries[e].index;
        ents[e].subindex   = dir->entries[e].subindex;
        ents[e].bit_length = dir->entries[e].bits;
    }
    for (int p = 0; p < dir->pdo_count; p++) {
        pdos[p].index     = dir->pdos[p].index;
        pdos[p].n_entries = (unsigned int)dir->pdos[p].count;
        pdos[p].entries   = &ents[dir->pdos[p].first];
    }
    return (unsigned int)dir->pdo_count;
}

/* ------------------------------------------------------------------ */
/*  Per-slave AL state checks                                          */
/* ------------------------------------------------------------------ */

/* IgH's al_state is the slave's AL status register: the state in the low
 * nibble and 0x10 when the slave refused a transition and is waiting for
 * an error acknowledge. PREOP + error (0x12) is not "at least SAFEOP". */
#define AL_STATE_MASK 0x0F
#define AL_ERROR      0x10

static bool slave_reached(uint8_t st, uint8_t al_state) {
    return !(st & AL_ERROR) && (st & AL_STATE_MASK) >= al_state;
}

/* AL status codes (ETG.1000.6), as `dmesg` and the Python package name them */
static const struct { uint16_t code; const char *text; } AL_CODES[] = {
    {0x0001, "Unspecified error"}, {0x0002, "No memory"},
    {0x0011, "Invalid requested state change"}, {0x0012, "Unknown requested state"},
    {0x0013, "Bootstrap not supported"}, {0x0014, "No valid firmware"},
    {0x0015, "Invalid mailbox configuration"}, {0x0016, "Invalid mailbox configuration"},
    {0x0017, "Invalid sync manager configuration"}, {0x0018, "No valid inputs available"},
    {0x0019, "No valid outputs"}, {0x001A, "Synchronization error"},
    {0x001B, "Sync manager watchdog"}, {0x001C, "Invalid sync manager types"},
    {0x001D, "Invalid output configuration"}, {0x001E, "Invalid input configuration"},
    {0x001F, "Invalid watchdog configuration"}, {0x0020, "Slave needs cold start"},
    {0x0021, "Slave needs INIT"}, {0x0022, "Slave needs PREOP"},
    {0x0023, "Slave needs SAFEOP"}, {0x0024, "Invalid input mapping"},
    {0x0025, "Invalid output mapping"}, {0x0026, "Inconsistent settings"},
    {0x0027, "Freerun not supported"}, {0x0028, "Synchronization not supported"},
    {0x0029, "Freerun needs 3 buffer mode"}, {0x002A, "Background watchdog"},
    {0x002B, "No valid inputs and outputs"}, {0x002C, "Fatal sync error"},
    {0x002D, "No sync error"}, {0x0030, "Invalid DC SYNC configuration"},
    {0x0031, "Invalid DC latch configuration"}, {0x0032, "PLL error"},
    {0x0033, "DC sync IO error"}, {0x0034, "DC sync timeout error"},
    {0x0035, "DC invalid sync cycle time"}, {0x0036, "DC Sync0 cycle time"},
    {0x0037, "DC Sync1 cycle time"}, {0x0041, "Mailbox AoE"}, {0x0042, "Mailbox EoE"},
    {0x0043, "Mailbox CoE"}, {0x0044, "Mailbox FoE"}, {0x0045, "Mailbox SoE"},
    {0x004F, "Mailbox VoE"}, {0x0050, "EEPROM no access"}, {0x0051, "EEPROM error"},
    {0x0060, "Slave restarted locally"},
};

const char *igh_al_code_text(uint16_t code) {
    for (size_t i = 0; i < sizeof(AL_CODES) / sizeof(AL_CODES[0]); i++)
        if (AL_CODES[i].code == code)
            return AL_CODES[i].text;
    return code >= 0x8000 ? "vendor specific" : "unknown code";
}

void igh_poll_al_code(ec_reg_request_t *req, bool in_op, uint16_t *code) {
    if (!req)
        return;
    switch (ecrt_reg_request_state(req)) {
        case EC_REQUEST_BUSY:
            return;
        case EC_REQUEST_SUCCESS: {
            /* Keep the last reason the slave gave: many clear the code
             * once IgH has acknowledged the error, before refusing again */
            uint16_t v = EC_READ_U16(ecrt_reg_request_data(req));
            if (!in_op && v)
                *code = v;
            break;
        }
        default:            /* not used yet, or the read failed (slave offline) */
            break;
    }
    if (in_op) {
        *code = 0;
        return;
    }
    ecrt_reg_request_read(req, 0x0134, 2);
}

static const char *al_state_name(uint8_t st);
static void startup_cycle(ec_master_t *master, ec_domain_t *domain);

/* "SAFEOP + error, AL status 0x0027 (Freerun not supported)" */
static void describe_state(char *buf, size_t n, const ec_slave_config_state_t *ss,
                           uint16_t al_code) {
    if (!ss->online) {
        snprintf(buf, n, "offline");
        return;
    }
    int len = snprintf(buf, n, "%s%s", al_state_name((uint8_t)ss->al_state),
                       (ss->al_state & AL_ERROR) ? " + error" : "");
    if (al_code && len > 0 && (size_t)len < n)
        snprintf(buf + len, n - (size_t)len, ", AL status 0x%04X (%s)", al_code,
                 igh_al_code_text(al_code));
}

/* ------------------------------------------------------------------ */
/*  Bus scan / state report                                            */
/* ------------------------------------------------------------------ */

static const char *al_state_name(uint8_t st) {
    switch (st & 0x0F) {
        case 1: return "INIT";
        case 2: return "PREOP";
        case 3: return "BOOT";
        case 4: return "SAFEOP";
        case 8: return "OP";
        default: return "?";
    }
}

static const char *slave_name(const ec_slave_info_t *si) {
    return si->name[0] ? si->name : "(no name in SII)";
}

static const slave_cfg_t *config_at_ring_pos(const odycat_config_t *cfg, int pos) {
    for (int i = 0; i < cfg->num_slaves; i++)
        if (cfg->slaves[i].ring_pos == pos) return &cfg->slaves[i];
    return NULL;
}

/* Resolve every configured slave to its absolute ring position.
 * Returns the number of slaves that could not be resolved (ring_pos = -1). */
static int resolve_ring_positions(odycat_config_t *cfg, const ec_slave_info_t *bus,
                                  unsigned int count) {
    int unresolved = 0;
    for (int i = 0; i < cfg->num_slaves; i++) {
        slave_cfg_t *s = &cfg->slaves[i];
        int base = -1;
        if (!s->alias) {
            base = 0;
        } else {
            for (unsigned int p = 0; p < count; p++) {
                if (bus[p].alias != s->alias) continue;
                if (base >= 0)
                    die("alias %u is set on more than one slave (positions %d and %u). "
                        "Aliases must be unique on the bus.", s->alias, base, p);
                base = (int)p;
            }
        }
        s->ring_pos = (base >= 0 && base + s->position < (int)count)
                    ? base + s->position : -1;
        if (s->ring_pos < 0) unresolved++;
    }
    return unresolved;
}

/* List the slaves IgH found and check them against the config.
 * Identity mismatches and missing slaves are fatal while extra slaves are not. */
void igh_scan_bus(ec_master_t *master, odycat_config_t *cfg, bool strict) {
    ec_master_info_t mi;
    if (ecrt_master(master, &mi))
        die("could not read master info.");
    printf("OdyCAT: bus scan — %u slave(s) found, %d configured\n",
           mi.slave_count, cfg->num_slaves);

    static ec_slave_info_t bus[EC_MAX_SLAVES_SCAN];
    if (mi.slave_count > EC_MAX_SLAVES_SCAN)
        die("bus has %u slaves, more than the %d supported.",
            mi.slave_count, EC_MAX_SLAVES_SCAN);
    for (unsigned int pos = 0; pos < mi.slave_count; pos++)
        if (ecrt_master_get_slave(master, (uint16_t)pos, &bus[pos]))
            die("could not read info for slave %u.", pos);

    int missing = resolve_ring_positions(cfg, bus, mi.slave_count);
    int problems = strict ? missing : 0;
    for (unsigned int pos = 0; pos < mi.slave_count; pos++) {
        const ec_slave_info_t si = bus[pos];
        const slave_cfg_t *c = config_at_ring_pos(cfg, (int)pos);
        char verdict[96];
        if (!c) {
            snprintf(verdict, sizeof(verdict), "not in config (stays in PREOP)");
        } else if (c->vendor_id != si.vendor_id || c->product_code != si.product_code) {
            snprintf(verdict, sizeof(verdict), "MISMATCH, config expects 0x%08X:0x%08X",
                     c->vendor_id, c->product_code);
            problems++;
        } else {
            if (c->alias)
                snprintf(verdict, sizeof(verdict), "OK -> %s (%u:%d)",
                         c->label, c->alias, c->position);
            else
                snprintf(verdict, sizeof(verdict), "OK -> %s", c->label);
        }
        char alias[8] = "";
        if (si.alias) snprintf(alias, sizeof(alias), "#%u", si.alias);
        /* without -v, only the rows that need attention */
        bool ok_row = c && c->vendor_id == si.vendor_id && c->product_code == si.product_code;
        if (ok_row && !g_verbose)
            continue;
        printf("  [%2u] %-6s 0x%08X:0x%08X  %-6s%s  %-34.34s %s\n",
               pos, alias, si.vendor_id, si.product_code, al_state_name(si.al_state),
               si.error_flag ? " E" : "  ", slave_name(&si), verdict);
    }
    for (int i = 0; i < cfg->num_slaves; i++) {
        slave_cfg_t *s = &cfg->slaves[i];
        if (s->ring_pos >= 0) continue;
        s->down = true;
        if (s->alias)
            printf("  MISSING  %u:%d (alias %u, offset %d) config expects 0x%08X:0x%08X -> %s\n",
                   s->alias, s->position, s->alias, s->position,
                   s->vendor_id, s->product_code, s->label);
        else
            printf("  MISSING  position %d, config expects 0x%08X:0x%08X -> %s\n",
                   s->position, s->vendor_id, s->product_code, s->label);
    }
    if (problems)
        die("%d configured slave(s) missing or with wrong identity. "
            "Check wiring, power, and slave order.", problems);
    if (missing)
        fprintf(stderr, "WARNING: %d configured slave(s) not on the bus: the engine "
                "runs without them, and scripts get DeviceLostError (offline) on "
                "them until they appear. --strict refuses to start instead.\n", missing);
}

void igh_report_down_slaves(ec_master_t *master, ec_domain_t *domain,
                            odycat_config_t *cfg) {
    int down = 0;
    for (int i = 0; i < cfg->num_slaves; i++)
        down += cfg->slaves[i].down;
    if (!down)
        return;
    /* A few more cycles, to see each down slave out of OP with its AL status
     * code read: one that refuses OP keeps getting there for a moment on
     * every retry of IgH's, so "now" can be OP and says nothing */
    static ec_slave_config_state_t seen[MAX_SLAVES];
    static bool have[MAX_SLAVES];
    uint64_t start = monotonic_ns();
    for (;;) {
        startup_cycle(master, domain);
        bool waiting = false;
        for (int i = 0; i < cfg->num_slaves; i++) {
            slave_cfg_t *s = &cfg->slaves[i];
            if (!s->down || s->ring_pos < 0)
                continue;
            ec_slave_config_state_t ss;
            ecrt_slave_config_state(s->sc, &ss);
            igh_poll_al_code(s->al_req, false, &s->al_code);
            if (!(ss.online && slave_reached((uint8_t)ss.al_state, AL_OP))) {
                seen[i] = ss;
                have[i] = true;
            }
            waiting |= !have[i] || (seen[i].online && !s->al_code);
        }
        if (!waiting || monotonic_ns() - start > 1000000000ULL)
            break;
        usleep((useconds_t)cfg->cycle_us);
    }
    fprintf(stderr, "WARNING: %d slave(s) not in OP; scripts get DeviceLostError on "
            "them. The engine keeps asking for OP and they join once they stay "
            "in OP:\n", down);
    for (int i = 0; i < cfg->num_slaves; i++) {
        const slave_cfg_t *s = &cfg->slaves[i];
        if (!s->down)
            continue;
        char st[128] = "not on the bus";
        if (s->ring_pos >= 0 && have[i])
            describe_state(st, sizeof(st), &seen[i], s->al_code);
        else if (s->ring_pos >= 0)
            snprintf(st, sizeof(st), "in OP for moments only, drops out again");
        fprintf(stderr, "  %u:%d (%s): %s\n", s->alias, s->position, s->label, st);
    }
}

void igh_report_slave_states(ec_master_t *master, const odycat_config_t *cfg) {
    for (int i = 0; i < cfg->num_slaves; i++) {
        const slave_cfg_t *s = &cfg->slaves[i];
        ec_slave_info_t si;
        if (s->ring_pos < 0) {
            printf("  [--] MISSING   %-20.20s %-34.34s tx %3d B  rx %3d B%s\n",
                   s->label, "(not on the bus)", s->tx.size, s->rx.size,
                   s->dc_enabled ? "  DC" : "");
            continue;
        }
        if (ecrt_master_get_slave(master, (uint16_t)s->ring_pos, &si))
            continue;
        if (!g_verbose && si.al_state == AL_OP && !si.error_flag)
            continue;                               /* without -v: only what is not in OP */
        printf("  [%2d] %-6s%s  %-20.20s %-34.34s tx %3d B  rx %3d B%s\n",
               s->ring_pos, al_state_name(si.al_state), si.error_flag ? " E" : "  ",
               s->label, slave_name(&si), s->tx.size, s->rx.size,
               s->dc_enabled ? "  DC" : "");
    }
}

/* ------------------------------------------------------------------ */
/*  Free-run for slaves without DC                                     */
/* ------------------------------------------------------------------ */

/* Queue a startup SDO setting the sync type of one SM parameter object
 * (0x1C32 outputs, 0x1C33 inputs) to free-run (0), if the slave has the
 * object and lists free-run in its supported types (subindex 4, bit 0).
 * Slaves in free-run do not depend on the frame timing, so a late or
 * jittery cycle cannot trip their sync error counters. */
static bool queue_free_run(ec_master_t *master, const slave_cfg_t *s,
                           uint16_t index) {
    uint8_t buf[2];
    size_t len = 0;
    uint32_t abort_code = 0;
    if (s->ring_pos < 0)
        return false;   /* not on the bus: nothing to ask */
    if (ecrt_master_sdo_upload(master, (uint16_t)s->ring_pos, index, 0x04,
                               buf, sizeof(buf), &len, &abort_code) || len != 2)
        return false;   /* no CoE, or no such object */
    if (!(EC_READ_U16(buf) & 0x0001))
        return false;   /* free-run not supported */
    if (ecrt_slave_config_sdo16(s->sc, index, 0x01, 0))
        die("failed to queue free-run SDO for slave %u:%d (%s) 0x%04X:01",
            s->alias, s->position, s->label, index);
    return true;
}

static void configure_free_run(ec_master_t *master, const slave_cfg_t *s) {
    bool has_out = s->rx.size > 0;
    bool has_in  = s->tx.size > 0;
    if (!has_out && !has_in) return;
    bool out = has_out && queue_free_run(master, s, 0x1C32);
    bool in  = has_in  && queue_free_run(master, s, 0x1C33);
    if (out || in)
        VPRINTF("  slave %u:%d [%s]: free-run (%s%s%s)\n", s->alias, s->position,
               s->label, out ? "outputs" : "", out && in ? ", " : "",
               in ? "inputs" : "");
    else
        VPRINTF("  slave %u:%d [%s]: free-run not configurable, keeps its sync mode\n",
               s->alias, s->position, s->label);
}

/* ------------------------------------------------------------------ */
/*  Master and slave configuration                                     */
/* ------------------------------------------------------------------ */

ec_master_t *igh_request_master(const odycat_config_t *cfg) {
    ec_master_t *master = ecrt_request_master(cfg->master_index);
    if (!master)
        die("could not acquire IgH master %u (bus '%s'). It may be busy (another "
            "engine or program owns it), not configured (MASTER%u_DEVICE in "
            "/etc/ethercat.conf; `ethercat master -m %u`), or ec_master not loaded.",
            cfg->master_index, cfg->bus, cfg->master_index, cfg->master_index);
    VPRINTF("OdyCAT: IgH master %u\n", cfg->master_index);
    return master;
}

/* PDO assignment: clear the slave's, add every configured PDO of each
 * direction, in config order. */
static void configure_pdos(const slave_cfg_t *s) {
    if (s->tx.pdo_count == 0 && s->rx.pdo_count == 0)
        return;
    ec_pdo_entry_info_t tx_ent[MAX_PDO_ENTRIES], rx_ent[MAX_PDO_ENTRIES];
    ec_pdo_info_t tx_pdos[MAX_PDOS_PER_DIR], rx_pdos[MAX_PDOS_PER_DIR];
    unsigned int n_tx_pdos = build_pdo_info(&s->tx, tx_ent, tx_pdos);
    unsigned int n_rx_pdos = build_pdo_info(&s->rx, rx_ent, rx_pdos);

    ec_sync_info_t syncs[] = {
        {2, EC_DIR_OUTPUT, n_rx_pdos, n_rx_pdos ? rx_pdos : NULL,
         n_rx_pdos ? EC_WD_ENABLE : EC_WD_DISABLE},
        {3, EC_DIR_INPUT,  n_tx_pdos, n_tx_pdos ? tx_pdos : NULL,
         EC_WD_DISABLE},
        {0xFF, 0, 0, NULL, EC_WD_DISABLE}
    };
    if (ecrt_slave_config_pdos(s->sc, EC_END, syncs))
        die("slave %d (%s): ecrt_slave_config_pdos failed", s->position, s->label);
}

/* Startup SDO writes go through the slave config API. This avoids
 * blocking ioctl-based SDO downloads in userspace startup. */
static void queue_startup_sdos(const slave_cfg_t *s) {
    for (int k = 0; k < s->sdo_count; k++) {
        const sdo_entry_t *sdo = &s->sdos[k];
        int rc = 0;
        switch (sdo->size) {
            case 1: {
                uint8_t v;
                memcpy(&v, sdo->data, 1);
                rc = ecrt_slave_config_sdo8(s->sc, sdo->index, sdo->subindex, v);
                break;
            }
            case 2: {
                uint16_t v;
                memcpy(&v, sdo->data, 2);
                rc = ecrt_slave_config_sdo16(s->sc, sdo->index, sdo->subindex, v);
                break;
            }
            case 4: {
                uint32_t v;
                memcpy(&v, sdo->data, 4);
                rc = ecrt_slave_config_sdo32(s->sc, sdo->index, sdo->subindex, v);
                break;
            }
            default:
                die("SDO startup write unsupported size %u for 0x%04X:%02X. "
                    "Use 1/2/4-byte values.",
                    sdo->size, sdo->index, sdo->subindex);
        }
        if (rc)
            die("failed to queue startup SDO for slave %d (%s) 0x%04X:%02X",
                s->position, s->label, sdo->index, sdo->subindex);
        VPRINTF("  SDO startup: slave %d [%s] 0x%04X:%02X = %s queued (%u bytes)\n",
               s->position, s->label, sdo->index, sdo->subindex, sdo->shown,
               sdo->size);
    }
}

static void configure_dc(const odycat_config_t *cfg, const slave_cfg_t *s) {
    uint32_t cycle_ns = sync0_cycle_ns(s, cfg->cycle_us);
    ecrt_slave_config_dc(s->sc, s->dc_assign_activate,
                         cycle_ns,             s->dc_sync0_shift_ns,
                         s->dc_sync1_cycle_ns, s->dc_sync1_shift_ns);
    VPRINTF("  slave %d [%s]: DC sync — assign_activate=0x%04X "
           "sync0_cycle=%uns sync0_shift=%dns\n",
           s->position, s->label, s->dc_assign_activate, cycle_ns, s->dc_sync0_shift_ns);
}

/* Create the IgH slave config, queue what the slave needs on its way to
 * OP, and register its PDO entries in the domain. */
void igh_configure_slave(ec_master_t *master, ec_domain_t *domain,
                         const odycat_config_t *cfg, slave_cfg_t *s) {
    s->sc = ecrt_master_slave_config(master, s->alias, s->position,
                                     s->vendor_id, s->product_code);
    if (!s->sc)
        die("slave %u:%d (%s): not found or vendor/product mismatch. "
            "Expected vendor=0x%08X product=0x%08X",
            s->alias, s->position, s->label, s->vendor_id, s->product_code);

    s->al_req = ecrt_slave_config_create_reg_request(s->sc, 2);
    if (!s->al_req)
        die("slave %u:%d (%s): could not create the AL status register request.",
            s->alias, s->position, s->label);

    configure_pdos(s);
    /* Slaves without DC go free-run, so late cycles cannot trip their
     * sync error. Queued first so explicit 0x1C32/0x1C33 SDOs in the
     * config still win. */
    if (!s->dc_enabled)
        configure_free_run(master, s);
    queue_startup_sdos(s);
    if (s->dc_enabled)
        configure_dc(cfg, s);

    /* Register PDO entries into the domain as byte-aligned copy chunks */
    register_pdo_dir(s, domain, "inputs", &s->tx);
    register_pdo_dir(s, domain, "outputs", &s->rx);

    VPRINTF("  slave %u:%d [%s]: tx %d B in %d PDO(s), %d chunk(s); "
           "rx %d B in %d PDO(s), %d chunk(s); shm_off=%d\n",
           s->alias, s->position, s->label,
           s->tx.size, s->tx.pdo_count, s->tx.chunk_count,
           s->rx.size, s->rx.pdo_count, s->rx.chunk_count,
           s->shm_offset);
}

/* ------------------------------------------------------------------ */
/*  Bringing the bus to OP                                             */
/* ------------------------------------------------------------------ */

/* One start-up cycle: IgH's FSM only advances on the cycles we send. */
static void startup_cycle(ec_master_t *master, ec_domain_t *domain) {
    ecrt_master_receive(master);
    ecrt_domain_process(domain);
    igh_sync_dc_clocks(master, monotonic_ns());   /* after receive: see igh.h */
    ecrt_domain_queue(domain);
    ecrt_master_send(master);
}

static void give_up(slave_cfg_t *s, const char *why) {
    s->down = true;
    fprintf(stderr, "WARNING: slave %u:%d (%s) %s: the engine runs without it.\n",
            s->alias, s->position, s->label, why);
}

int igh_bring_bus_to(ec_master_t *master, ec_domain_t *domain, odycat_config_t *cfg,
                     uint8_t al_state, const char *name, int timeout_s, int hold_ms,
                     bool strict, const char *hint) {
    uint64_t start = monotonic_ns(), reached = 0;
    for (;;) {
        startup_cycle(master, domain);
        uint64_t now = monotonic_ns();
        int below = 0;
        bool left = false;
        for (int i = 0; i < cfg->num_slaves; i++) {
            slave_cfg_t *s = &cfg->slaves[i];
            if (s->down)
                continue;
            ec_slave_config_state_t ss;
            ecrt_slave_config_state(s->sc, &ss);
            bool ok = ss.online && slave_reached((uint8_t)ss.al_state, al_state);
            igh_poll_al_code(s->al_req, ok, &s->al_code);
            if (ok)
                continue;
            if (!reached) {
                below++;
                continue;
            }
            /* It was there with all the others and dropped out during the hold */
            if (strict) {
                char st[128];
                describe_state(st, sizeof(st), &ss, s->al_code);
                die("slave %u:%d (%s) reached %s and left it after %.0f ms (now %s). "
                    "A slave that drops back with an error refused something in %s.%s%s",
                    s->alias, s->position, s->label, name,
                    (double)(now - reached) / 1e6, st, name, hint ? " " : "",
                    hint ? hint : "");
            }
            char why[48];
            snprintf(why, sizeof(why), "left %s after %.0f ms",
                     name, (double)(now - reached) / 1e6);
            give_up(s, why);
            left = true;
        }
        if (left)
            reached = now;          /* the others hold again from here */
        if (!below) {
            if (!reached)
                reached = now;
            if (now - reached >= (uint64_t)hold_ms * 1000000ULL)
                break;
        } else if (now - start > (uint64_t)timeout_s * 1000000000ULL) {
            for (int i = 0; i < cfg->num_slaves; i++) {
                slave_cfg_t *s = &cfg->slaves[i];
                if (s->down)
                    continue;
                ec_slave_config_state_t ss;
                ecrt_slave_config_state(s->sc, &ss);
                if (ss.online && slave_reached((uint8_t)ss.al_state, al_state))
                    continue;
                if (strict) {
                    char st[128];
                    describe_state(st, sizeof(st), &ss, s->al_code);
                    fprintf(stderr, "ERROR: slave %u:%d (%s) is %s, expected %s.\n",
                            s->alias, s->position, s->label, st, name);
                } else {
                    char why[48];
                    snprintf(why, sizeof(why), "did not reach %s in %d s",
                             name, timeout_s);
                    give_up(s, why);
                }
            }
            if (strict)
                die("could not reach %s.%s%s", name, hint ? " " : "", hint ? hint : "");
            break;
        }
        if (stop_requested())
            die("interrupted during startup.");
        usleep((useconds_t)cfg->cycle_us);
    }
    int down = 0;
    for (int i = 0; i < cfg->num_slaves; i++)
        down += cfg->slaves[i].down;
    if (down)
        printf("OdyCAT: bus in %s, without %d slave(s).\n", name, down);
    else if (g_verbose || !strcmp(name, "OP"))      /* PREOP, SAFEOP: with -v */
        printf("OdyCAT: bus in %s.\n", name);
    return down;
}
