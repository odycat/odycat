/*
 * OdyCAT Engine — config parser: reads the JSON config into an
 * odycat_config_t, or dies naming what is wrong. No IgH dependency.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define _GNU_SOURCE
#include <errno.h>
#include <float.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include <cjson/cJSON.h>

#include "config.h"
#include "layout.h"
#include "util.h"

/* Bus cycles the engine accepts (µs): the usual EtherCAT periods. */
static const int CYCLE_US_ALLOWED[] = {125, 250, 500, 1000, 2000, 4000, 8000, 10000};

/* The largest config file the engine reads. Real configs are tens of kB. */
#define CONFIG_MAX_BYTES (16u * 1024u * 1024u)

/* The value types of PDO entries and SDOs (the config's "type"); the Python
 * package has the same table (odycat._config.TYPES). */
typedef enum { VAL_UNSIGNED, VAL_SIGNED, VAL_BOOL, VAL_FLOAT } val_kind_t;

static const struct { const char *name; int bytes; val_kind_t kind; } VALUE_TYPES[] = {
    {"bool", 1, VAL_BOOL},
    {"u8",  1, VAL_UNSIGNED}, {"i8",  1, VAL_SIGNED},
    {"u16", 2, VAL_UNSIGNED}, {"i16", 2, VAL_SIGNED},
    {"u32", 4, VAL_UNSIGNED}, {"i32", 4, VAL_SIGNED}, {"f32", 4, VAL_FLOAT},
    {"u64", 8, VAL_UNSIGNED}, {"i64", 8, VAL_SIGNED}, {"f64", 8, VAL_FLOAT},
};

/* Bytes of a value type (0 = unknown), and its kind. */
static int value_type(const char *name, val_kind_t *kind) {
    if (!name) return 0;
    for (size_t t = 0; t < sizeof(VALUE_TYPES) / sizeof(VALUE_TYPES[0]); t++)
        if (!strcmp(name, VALUE_TYPES[t].name)) {
            if (kind) *kind = VALUE_TYPES[t].kind;
            return VALUE_TYPES[t].bytes;
        }
    return 0;
}

#define VALUE_TYPE_NAMES "bool, u8, i8, u16, i16, u32, i32, f32, u64, i64, f64"

static bool parse_int_strict(const cJSON *j, int64_t *out, bool *is_hex) {
    *is_hex = false;
    if (cJSON_IsNumber(j)) {
        double d = j->valuedouble;
        if (d != d || d < -9.2e18 || d > 9.2e18) return false;
        int64_t v = (int64_t)d;
        if ((double)v != d) return false;
        *out = v;
        return true;
    }
    if (!cJSON_IsString(j) || !j->valuestring[0]) return false;
    const char *s = j->valuestring;
    char *end;
    errno = 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        if (!s[2] || s[2] == '-' || s[2] == '+') return false;
        unsigned long long u = strtoull(s + 2, &end, 16);
        if (*end || errno || u > 0xFFFFFFFFull) return false;
        *out = (int64_t)u;
        *is_hex = true;
        return true;
    }
    long long v = strtoll(s, &end, 10);
    if (*end || errno) return false;
    *out = v;
    return true;
}

/* An integer field: a JSON number, or a string in decimal or "0x" hex
 * (the forms the Python package reads too), within lo..hi. False if it is
 * missing, not a whole number, or out of range: callers die naming the field,
 * so no value is ever truncated or wrapped into another one. */
static bool json_int(const cJSON *j, int64_t lo, int64_t hi, int64_t *out) {
    int64_t v;
    bool hex;
    if (!j || !parse_int_strict(j, &v, &hex) || v < lo || v > hi)
        return false;
    *out = v;
    return true;
}

/* Copy a string field into a fixed buffer, or die: a cut label or name would
 * no longer be the one the Python package reads from the same config. */
static void copy_name(char *dst, size_t size, const char *src, const char *what) {
    if (strlen(src) >= size)
        die("%s \"%s\" is too long (at most %zu characters)", what, src, size - 1);
    memcpy(dst, src, strlen(src) + 1);
}

/* Die on an object that has the same key twice. JSON allows it, but cJSON
 * keeps the first value and Python's json the last: the engine and the
 * scripts would read two different configs from the same text. */
static void check_duplicate_keys(const cJSON *j, const char *where) {
    for (const cJSON *c = j ? j->child : NULL; c; c = c->next) {
        if (cJSON_IsObject(j) && c->string)
            for (const cJSON *d = c->next; d; d = d->next)
                if (d->string && !strcmp(c->string, d->string))
                    die("config has the key \"%s\" twice in %s", c->string, where);
        if (cJSON_IsObject(c) || cJSON_IsArray(c))
            check_duplicate_keys(c, c->string ? c->string : where);
    }
}

/* Parse one "sdos" entry into *sdo, or die naming what is wrong. */
static void parse_sdo(const cJSON *j, const slave_cfg_t *s, int k, sdo_entry_t *sdo) {
    cJSON *si = cJSON_GetObjectItem(j, "index");
    cJSON *ss = cJSON_GetObjectItem(j, "subindex");
    cJSON *st = cJSON_GetObjectItem(j, "type");
    cJSON *sv = cJSON_GetObjectItem(j, "value");
    cJSON *sl = cJSON_GetObjectItem(j, "label");
    char what[96];
    snprintf(what, sizeof(what), "slave %d (%s): SDO %d%s%s%s", s->position, s->label, k,
             cJSON_IsString(sl) ? " '" : "", cJSON_IsString(sl) ? sl->valuestring : "",
             cJSON_IsString(sl) ? "'" : "");
    if (!si || !ss || !sv)
        die("%s: needs 'index', 'subindex' and 'value'", what);

    int64_t v;
    bool hex;
    if (!parse_int_strict(si, &v, &hex) || v < 1 || v > 0xFFFF)
        die("%s: 'index' must be 0x0001..0xFFFF, e.g. \"0x6083\"", what);
    sdo->index = (uint16_t)v;
    if (!parse_int_strict(ss, &v, &hex) || v < 0 || v > 0xFF)
        die("%s: 'subindex' must be 0..255", what);
    sdo->subindex = (uint8_t)v;

    val_kind_t kind = VAL_UNSIGNED;
    int size = cJSON_IsString(st) ? value_type(st->valuestring, &kind) : 0;
    if (!size)
        die("%s: needs a 'type' (%s)", what, VALUE_TYPE_NAMES);
    if (size > 4)
        die("%s: 8-byte SDOs are not supported; use a type of 1, 2 or 4 bytes", what);
    sdo->size = (uint8_t)size;
    memset(sdo->data, 0, sizeof(sdo->data));

    int bits = 8 * size;
    if (kind == VAL_FLOAT) {
        if (cJSON_IsString(sv) && parse_int_strict(sv, &v, &hex) && hex) {
            uint32_t raw = (uint32_t)v;        /* raw IEEE 754 bit pattern */
            memcpy(sdo->data, &raw, 4);
            float f;
            memcpy(&f, &raw, 4);
            snprintf(sdo->shown, sizeof(sdo->shown), "%g (0x%08" PRIX32 ")", (double)f, raw);
            return;
        }
        if (!cJSON_IsNumber(sv) || sv->valuedouble != sv->valuedouble ||
            sv->valuedouble > FLT_MAX || sv->valuedouble < -FLT_MAX)
            die("%s: 'value' must be a number in float range, or a hex bit "
                "pattern such as \"0x3FC00000\"", what);
        float f = (float)sv->valuedouble;
        memcpy(sdo->data, &f, 4);
        snprintf(sdo->shown, sizeof(sdo->shown), "%g", (double)f);
        return;
    }

    if (kind == VAL_BOOL && cJSON_IsBool(sv)) {
        v = cJSON_IsTrue(sv);
        hex = false;
    } else if (!parse_int_strict(sv, &v, &hex)) {
        die("%s: 'value' must be an integer, or a string in decimal or hex "
            "(\"0x...\")", what);
    }
    int64_t lo, hi;
    if (hex) {
        lo = 0;  hi = (int64_t)((1ull << bits) - 1);
    } else if (kind == VAL_SIGNED) {
        lo = -((int64_t)1 << (bits - 1));  hi = ((int64_t)1 << (bits - 1)) - 1;
    } else if (kind == VAL_BOOL) {
        lo = 0;  hi = 1;
    } else {
        lo = 0;  hi = (int64_t)((1ull << bits) - 1);
    }
    if (v < lo || v > hi)
        die("%s: 'value' %" PRId64 " does not fit %s (%" PRId64 "..%" PRId64 ")%s",
            what, v, st->valuestring, lo, hi,
            hex ? "; hex values are raw bit patterns" : "");
    uint64_t raw = (uint64_t)v & ((1ull << bits) - 1);
    uint8_t  u8  = (uint8_t)raw;
    uint16_t u16 = (uint16_t)raw;
    uint32_t u32 = (uint32_t)raw;
    memcpy(sdo->data, size == 1 ? (void *)&u8 : size == 2 ? (void *)&u16 : (void *)&u32,
           (size_t)size);
    int64_t shown = (kind == VAL_SIGNED && (raw >> (bits - 1)))
                  ? (int64_t)(raw | ~((1ull << bits) - 1)) : (int64_t)raw;
    snprintf(sdo->shown, sizeof(sdo->shown), "%" PRId64 " (0x%0*" PRIX64 ")",
             shown, size * 2, raw);
}

/* Every slave with outputs joins its "sync_group", or a group of its own.
 * Groups are numbered in order of their first slave; members in config
 * order. */
static void assign_sync_groups(odycat_config_t *cfg) {
    cfg->group_count = 0;
    for (int i = 0; i < cfg->num_slaves; i++) {
        slave_cfg_t *s = &cfg->slaves[i];
        s->out_group = -1;
        if (s->rx.size == 0)
            continue;
        int g = -1;
        if (s->sync_group[0])
            for (int k = 0; k < i && g < 0; k++)
                if (cfg->slaves[k].out_group >= 0 &&
                    strcmp(cfg->slaves[k].sync_group, s->sync_group) == 0)
                    g = cfg->slaves[k].out_group;
        if (g < 0)
            g = cfg->group_count++;
        out_group_t *grp = &cfg->groups[g];
        if (grp->count == ODYCAT_GROUP_MAX_MEMBERS)
            die("sync_group \"%s\" has more than %d slaves", s->sync_group,
                ODYCAT_GROUP_MAX_MEMBERS);
        s->out_group  = g;
        s->out_member = grp->count;
        grp->slaves[grp->count++] = (uint16_t)i;
    }
    for (int g = 0; g < cfg->group_count; g++) {
        const out_group_t *grp = &cfg->groups[g];
        const slave_cfg_t *first = &cfg->slaves[grp->slaves[0]];
        if (!first->sync_group[0])
            continue;
        VPRINTF("OdyCAT: sync group '%s':", first->sync_group);
        for (int m = 0; m < grp->count; m++)
            VPRINTF(" %s", cfg->slaves[grp->slaves[m]].label);
        VPRINTF("\n");
    }
}

/* The config file's text. Its size comes from the opened file, so what is
 * read, hashed and published is the whole file and only that file. */
static char *read_file_to_string(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) die("could not open config file '%s': %s", path, strerror(errno));
    struct stat st;
    if (fstat(fileno(f), &st) != 0)
        die("could not read config file '%s': %s", path, strerror(errno));
    if (!S_ISREG(st.st_mode))
        die("config file '%s' is not a regular file", path);
    if (st.st_size <= 0 || (uint64_t)st.st_size > CONFIG_MAX_BYTES)
        die("config file '%s' has %lld bytes; a config has 1..%u",
            path, (long long)st.st_size, CONFIG_MAX_BYTES);
    size_t len = (size_t)st.st_size;
    char *buf = malloc(len + 1);
    if (!buf) die("malloc failed reading config");
    if (fread(buf, 1, len, f) != len || fgetc(f) != EOF)
        die("config file '%s' changed size while it was read", path);
    buf[len] = '\0';
    if (memchr(buf, '\0', len))
        die("config file '%s' contains a NUL byte: it is not a JSON text file", path);
    fclose(f);
    return buf;
}

/* PDO index ranges of each direction (CoE): RxPDOs = outputs, TxPDOs = inputs */
typedef struct {
    const char *key, *kind;
    uint16_t    lo, hi;
} pdo_dir_spec_t;

static const pdo_dir_spec_t RXPDO_SPEC = {"rxpdo", "an RxPDO", 0x1600, 0x17FF};
static const pdo_dir_spec_t TXPDO_SPEC = {"txpdo", "a TxPDO",  0x1A00, 0x1BFF};

/* Parse one PDO object ({"index", "entries"}) and append it to
 * a direction. A PDO without entries is not assigned. */
static void parse_pdo(cJSON *blk, const char *name, const pdo_dir_spec_t *spec,
                      const slave_cfg_t *s, pdo_dir_t *dir) {
    if (!cJSON_IsObject(blk))
        die("slave %d (%s): %s must be an object", s->position, s->label, name);

    cJSON *arr = cJSON_GetObjectItem(blk, "entries");
    if (arr && !cJSON_IsArray(arr))
        die("slave %d (%s): %s.entries must be an array", s->position, s->label, name);
    int count = arr ? cJSON_GetArraySize(arr) : 0;
    if (count == 0) return;

    cJSON *j = cJSON_GetObjectItem(blk, "index");
    if (!j || !cJSON_IsString(j))
        die("slave %d (%s): %s has entries but no PDO 'index' (e.g. \"0x1A00\")",
            s->position, s->label, name);
    if (dir->pdo_count >= MAX_PDOS_PER_DIR)
        die("slave %d (%s): too many PDOs in one direction (max %d)",
            s->position, s->label, MAX_PDOS_PER_DIR);
    if (dir->entry_count + count > MAX_PDO_ENTRIES)
        die("slave %d (%s): too many PDO entries in one direction (max %d)",
            s->position, s->label, MAX_PDO_ENTRIES);

    pdo_cfg_t *pdo = &dir->pdos[dir->pdo_count++];
    int64_t v;
    if (!json_int(j, spec->lo, spec->hi, &v))
        die("slave %d (%s): %s index \"%s\" is not %s (0x%04X-0x%04X). "
            "\"rxpdo\" holds the slave's RxPDOs (outputs, 0x16xx), "
            "\"txpdo\" its TxPDOs (inputs, 0x1Axx)",
            s->position, s->label, name, j->valuestring, spec->kind, spec->lo, spec->hi);
    pdo->index = (uint16_t)v;
    pdo->first = dir->entry_count;
    pdo->count = count;
    pdo_entry_cfg_t *ents = &dir->entries[pdo->first];
    dir->entry_count += count;

    for (int e = 0; e < count; e++) {
        cJSON *ent  = cJSON_GetArrayItem(arr, e);
        cJSON *idx  = cJSON_GetObjectItem(ent, "index");
        cJSON *sub  = cJSON_GetObjectItem(ent, "subindex");
        cJSON *typ  = cJSON_GetObjectItem(ent, "type");
        cJSON *bits = cJSON_GetObjectItem(ent, "bits");
        if (!idx || !sub)
            die("slave %d (%s): %s entry %d missing index/subindex",
                s->position, s->label, name, e);
        if (!json_int(idx, 0, 0xFFFF, &v))
            die("slave %d (%s): %s entry %d: 'index' must be 0x0000..0xFFFF "
                "(0x0000 for a gap), e.g. \"0x6041\"", s->position, s->label, name, e);
        ents[e].index = (uint16_t)v;
        if (!json_int(sub, 0, 0xFF, &v))
            die("slave %d (%s): %s entry %d: 'subindex' must be 0..255",
                s->position, s->label, name, e);
        ents[e].subindex = (uint8_t)v;
        if (typ && bits)
            die("slave %d (%s): %s entry %d has both 'type' and 'bits': give one",
                s->position, s->label, name, e);
        if (typ) {
            int bytes = cJSON_IsString(typ) ? value_type(typ->valuestring, NULL) : 0;
            if (!bytes)
                die("slave %d (%s): %s entry %d: 'type' must be one of %s",
                    s->position, s->label, name, e, VALUE_TYPE_NAMES);
            ents[e].bits = (uint8_t)(8 * bytes);
        } else if (bits) {
            if (!json_int(bits, 1, 255, &v))
                die("slave %d (%s): %s entry %d: 'bits' must be 1..255",
                    s->position, s->label, name, e);
            ents[e].bits = (uint8_t)v;
            /* A value of a whole type's size is given by its type */
            if (ents[e].index && (ents[e].bits == 8 || ents[e].bits == 16 ||
                                  ents[e].bits == 32 || ents[e].bits == 64))
                die("slave %d (%s): %s entry %d has \"bits\": %d: give a value of "
                    "that size its type (\"type\": \"u%d\", \"i%d\"...)",
                    s->position, s->label, name, e, ents[e].bits, ents[e].bits, ents[e].bits);
        } else {
            die("slave %d (%s): %s entry %d needs a 'type' (%s), or 'bits' for a bit "
                "field or a gap", s->position, s->label, name, e, VALUE_TYPE_NAMES);
        }
        if (!ents[e].bits)
            die("slave %d (%s): %s entry %d has zero bit length",
                s->position, s->label, name, e);
    }
}

/* Parse one direction: `key` is one PDO object or an array of them. */
static void parse_pdo_dir(cJSON *sl, const pdo_dir_spec_t *spec,
                          const slave_cfg_t *s, pdo_dir_t *dir) {
    const char *key = spec->key;
    char name[32];
    cJSON *blk = cJSON_GetObjectItem(sl, key);
    if (!blk) die("slave %d (%s): config missing field '%s'", s->position, s->label, key);
    if (cJSON_IsArray(blk)) {
        int n = cJSON_GetArraySize(blk);
        for (int p = 0; p < n; p++) {
            snprintf(name, sizeof(name), "%s[%d]", key, p);
            parse_pdo(cJSON_GetArrayItem(blk, p), name, spec, s, dir);
        }
    } else {
        parse_pdo(blk, key, spec, s, dir);
    }

    int rem = walk_pdo_chunks(dir->entries, dir->entry_count, count_chunk_bytes,
                              &dir->size);
    if (rem)
        die("slave %d (%s): %s entries do not end on a byte boundary "
            "(%d trailing bits). Add a gap entry {\"index\":\"0x0000\",\"subindex\":0,\"bits\":%d}.",
            s->position, s->label, key, rem, 8 - rem);
}

void config_load(odycat_config_t *cfg, const char *path) {
    memset(cfg, 0, sizeof(*cfg));
    cfg->cycle_us = 1000;
    cfg->history_s = HISTORY_S_DEFAULT;
    snprintf(cfg->shm_group, sizeof(cfg->shm_group), "odycat");

    char *text = read_file_to_string(path);
    cfg->text = text;
    cfg->text_len = strlen(text);
    /* The whole text must be one JSON value: no trailing data. */
    cJSON *root = cJSON_ParseWithOpts(text, NULL, true);
    if (!root) die("config JSON parse error near: %.40s", cJSON_GetErrorPtr());
    if (!cJSON_IsObject(root)) die("config must be a JSON object");
    check_duplicate_keys(root, "the top level");

    /* Top-level fields */
    cJSON *j;
    int64_t v;

    j = cJSON_GetObjectItem(root, "bus");
    if (!j || !cJSON_IsString(j)) die("config missing field 'bus'");
    copy_name(cfg->bus, sizeof(cfg->bus), j->valuestring, "bus name");

    j = cJSON_GetObjectItem(root, "cycle_us");
    if (!j || !cJSON_IsNumber(j)) die("config missing field 'cycle_us'");
    cfg->cycle_us = 0;
    for (size_t c = 0; c < sizeof(CYCLE_US_ALLOWED) / sizeof(CYCLE_US_ALLOWED[0]); c++)
        if (j->valuedouble == CYCLE_US_ALLOWED[c]) cfg->cycle_us = CYCLE_US_ALLOWED[c];
    if (!cfg->cycle_us)
        die("cycle_us=%g is not supported. Use one of 125, 250, 500, 1000, "
            "2000, 4000, 8000, 10000", j->valuedouble);

    j = cJSON_GetObjectItem(root, "require_rt");
    if (j && !cJSON_IsBool(j)) die("config field 'require_rt' must be true or false");
    cfg->require_rt = cJSON_IsTrue(j);

    j = cJSON_GetObjectItem(root, "master");
    if (j && (!cJSON_IsNumber(j) || !json_int(j, 0, 255, &v)))
        die("config field 'master' must be an IgH master index, 0..255");
    if (j) cfg->master_index = (unsigned)v;

    j = cJSON_GetObjectItem(root, "history_s");
    if (j && (!cJSON_IsNumber(j) || j->valuedouble < 0 || j->valuedouble > HISTORY_S_MAX))
        die("config field 'history_s' must be a number of seconds, 0..%g (0 = no trace)",
            HISTORY_S_MAX);
    if (j) cfg->history_s = j->valuedouble;

    j = cJSON_GetObjectItem(root, "shm_group");
    if (j && !cJSON_IsString(j)) die("config field 'shm_group' must be a string");
    if (j) copy_name(cfg->shm_group, sizeof(cfg->shm_group), j->valuestring, "shm_group");

    /* Slaves */
    cJSON *slaves = cJSON_GetObjectItem(root, "slaves");
    if (!slaves || !cJSON_IsArray(slaves)) die("config missing field 'slaves'");

    cfg->num_slaves = cJSON_GetArraySize(slaves);
    if (cfg->num_slaves > MAX_SLAVES)
        die("too many slaves (%d > %d)", cfg->num_slaves, MAX_SLAVES);

    for (int i = 0; i < cfg->num_slaves; i++) {
        cJSON *sl = cJSON_GetArrayItem(slaves, i);
        slave_cfg_t *s = &cfg->slaves[i];
        memset(s, 0, sizeof(*s));

        /* "device" entries are filled in by `odycat-config expand` */
        j = cJSON_GetObjectItem(sl, "device");
        if (j && !cJSON_GetObjectItem(sl, "vendor_id"))
            die("slave %d uses \"device\": %s, a device class from the Python "
                "package. Write the config out in full first, as your user: "
                "`odycat-config expand %s -o machine.json`, then start the "
                "engine with machine.json", i,
                cJSON_IsString(j) ? j->valuestring : "?", path);

        if (!cJSON_IsObject(sl)) die("slave %d must be an object", i);

        j = cJSON_GetObjectItem(sl, "alias");
        if (j && !json_int(j, 0, 0xFFFF, &v))
            die("slave %d: 'alias' must be 0..65535 (a number, or a string such as \"0x3C\")", i);
        if (j) s->alias = (uint16_t)v;

        /* With an alias, position is an offset and defaults to 0. */
        j = cJSON_GetObjectItem(sl, "position");
        if (j && !json_int(j, 0, 0xFFFF, &v))
            die("slave %d: 'position' must be 0..65535", i);
        if (j)
            s->position = (int)v;
        else if (!s->alias)
            die("config missing field 'position' in slave %d", i);

        j = cJSON_GetObjectItem(sl, "label");
        if (!j || !cJSON_IsString(j)) die("config missing field 'label' in slave %d", i);
        copy_name(s->label, sizeof(s->label), j->valuestring, "label");

        j = cJSON_GetObjectItem(sl, "vendor_id");
        if (!j || !cJSON_IsString(j) || !json_int(j, 0, 0xFFFFFFFF, &v))
            die("slave %d (%s): 'vendor_id' must be a hex string, e.g. \"0x00000002\"",
                s->position, s->label);
        s->vendor_id = (uint32_t)v;

        j = cJSON_GetObjectItem(sl, "product_code");
        if (!j || !cJSON_IsString(j) || !json_int(j, 0, 0xFFFFFFFF, &v))
            die("slave %d (%s): 'product_code' must be a hex string, e.g. \"0x07113052\"",
                s->position, s->label);
        s->product_code = (uint32_t)v;

        /* PDOs. txpdo/rxpdo are required (entries may be empty) and hold
         * one PDO or a list. */
        parse_pdo_dir(sl, &TXPDO_SPEC, s, &s->tx);
        parse_pdo_dir(sl, &RXPDO_SPEC, s, &s->rx);

        j = cJSON_GetObjectItem(sl, "sync_group");
        if (j && !cJSON_IsNull(j)) {
            if (!cJSON_IsString(j) || !j->valuestring[0])
                die("slave %d (%s): 'sync_group' must be a non-empty name",
                    s->position, s->label);
            if (s->rx.size == 0)
                die("slave %d (%s): 'sync_group' \"%s\" on a slave without outputs. "
                    "A sync group sends the outputs of its slaves in the same "
                    "cycle; remove it from this slave.", s->position, s->label,
                    j->valuestring);
            copy_name(s->sync_group, sizeof(s->sync_group), j->valuestring, "sync_group");
        }
        /* SDOs */
        cJSON *sdos = cJSON_GetObjectItem(sl, "sdos");
        if (sdos && !cJSON_IsArray(sdos))
            die("slave %d (%s): 'sdos' must be an array", s->position, s->label);
        if (sdos) {
            s->sdo_count = cJSON_GetArraySize(sdos);
            if (s->sdo_count > MAX_SDOS_PER_SLAVE)
                die("slave %d: too many SDOs (%d)", i, s->sdo_count);
            for (int k = 0; k < s->sdo_count; k++)
                parse_sdo(cJSON_GetArrayItem(sdos, k), s, k, &s->sdos[k]);
        }

        /* DC sync: off (free-run) unless the config asks for it. A number
         * is the Sync0 period in ns (0 = the bus cycle), an object gives
         * the settings in full; null is the same as no key. */
        cJSON *dc = cJSON_GetObjectItem(sl, "dc_sync");
        if (dc && !cJSON_IsNull(dc)) {
            if (!cJSON_IsNumber(dc) && !cJSON_IsObject(dc))
                die("slave %d (%s): dc_sync must be the Sync0 period in ns (0 = the "
                    "bus cycle), an object, or null", s->position, s->label);
            s->dc_enabled = true;
            /* Each setting: absent = its default, present = valid or die. */
            const struct { const char *key; int64_t lo, hi, dflt; } DC[] = {
                {"assign_activate", 0, 0xFFFF, 0x0300},
                {"sync0_cycle_ns",  0, UINT32_MAX, 0},
                {"sync0_shift_ns",  INT32_MIN, INT32_MAX, 0},
                {"sync1_cycle_ns",  0, UINT32_MAX, 0},
                {"sync1_shift_ns",  INT32_MIN, INT32_MAX, 0},
            };
            int64_t dcv[5];
            for (int k = 0; k < 5; k++) {
                cJSON *jk = cJSON_IsObject(dc) ? cJSON_GetObjectItem(dc, DC[k].key)
                          : (k == 1 ? dc : NULL);    /* a number is the Sync0 period */
                dcv[k] = DC[k].dflt;
                if (jk && !json_int(jk, DC[k].lo, DC[k].hi, &dcv[k]))
                    die("slave %d (%s): dc_sync %s must be an integer, %" PRId64 "..%" PRId64,
                        s->position, s->label, DC[k].key, DC[k].lo, DC[k].hi);
            }
            s->dc_assign_activate = (uint16_t)dcv[0];
            s->dc_sync0_cycle_ns  = (uint32_t)dcv[1];
            s->dc_sync0_shift_ns  = (int32_t)dcv[2];
            s->dc_sync1_cycle_ns  = (uint32_t)dcv[3];
            s->dc_sync1_shift_ns  = (int32_t)dcv[4];
            /* Sync0 shorter than the bus cycle, or not a whole multiple of
             * it, leaves Sync0 periods without new data: the drive would
             * report sync errors all the time. */
            uint32_t bus_ns = (uint32_t)cfg->cycle_us * 1000u;
            if (s->dc_sync0_cycle_ns && (s->dc_sync0_cycle_ns < bus_ns ||
                                         s->dc_sync0_cycle_ns % bus_ns))
                die("slave %d (%s): dc_sync Sync0 period %u ns must be a whole "
                    "multiple of the bus cycle (cycle_us=%d -> %u, %u, %u ns...)",
                    s->position, s->label, s->dc_sync0_cycle_ns, cfg->cycle_us,
                    bus_ns, 2 * bus_ns, 4 * bus_ns);
        }
    }

    for (int a = 0; a < cfg->num_slaves; a++)
        for (int b = a + 1; b < cfg->num_slaves; b++)
            if (cfg->slaves[a].alias == cfg->slaves[b].alias &&
                cfg->slaves[a].position == cfg->slaves[b].position)
                die("slaves '%s' and '%s' have the same address %u:%d",
                    cfg->slaves[a].label, cfg->slaves[b].label,
                    cfg->slaves[a].alias, cfg->slaves[a].position);

    assign_sync_groups(cfg);

    /* The shm layout is internal: blocks follow each other in config
     * order, and Python derives the same offsets. */
    layout_place_blocks(cfg);

    cJSON_Delete(root);
}
