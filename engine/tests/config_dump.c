/*
 * OdyCAT Engine — config_dump: runs the engine's config parser on a file and
 * prints what it understood, as JSON. A config the engine refuses exits with
 * status 1 and the engine's own message, as the engine would.
 *
 * Needs only cJSON (no IgH, no bus, no root): the unit tests
 * (tests/test_engine_config.py) and the fuzzer build it with
 * `make -C engine config-dump`.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#include <stdio.h>
#include <stdlib.h>

#include <cjson/cJSON.h>

#include "../config.h"

static odycat_config_t g_cfg;       /* large: not on the stack */

static cJSON *dump_dir(const pdo_dir_t *d) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "size", d->size);
    cJSON *pdos = cJSON_AddArrayToObject(o, "pdos");
    for (int p = 0; p < d->pdo_count; p++) {
        const pdo_cfg_t *pdo = &d->pdos[p];
        cJSON *jp = cJSON_CreateObject();
        cJSON_AddNumberToObject(jp, "index", pdo->index);
        cJSON *ents = cJSON_AddArrayToObject(jp, "entries");
        for (int e = 0; e < pdo->count; e++) {
            const pdo_entry_cfg_t *ent = &d->entries[pdo->first + e];
            cJSON *je = cJSON_CreateObject();
            cJSON_AddNumberToObject(je, "index", ent->index);
            cJSON_AddNumberToObject(je, "subindex", ent->subindex);
            cJSON_AddNumberToObject(je, "bits", ent->bits);
            cJSON_AddItemToArray(ents, je);
        }
        cJSON_AddItemToArray(pdos, jp);
    }
    return o;
}

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <config.json>\n", argv[0]);
        return 2;
    }
    config_load(&g_cfg, argv[1]);

    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "bus", g_cfg.bus);
    cJSON_AddNumberToObject(o, "cycle_us", g_cfg.cycle_us);
    cJSON_AddNumberToObject(o, "master", g_cfg.master_index);
    cJSON_AddNumberToObject(o, "history_s", g_cfg.history_s);
    cJSON_AddStringToObject(o, "shm_group", g_cfg.shm_group);
    cJSON *slaves = cJSON_AddArrayToObject(o, "slaves");
    for (int i = 0; i < g_cfg.num_slaves; i++) {
        const slave_cfg_t *s = &g_cfg.slaves[i];
        cJSON *js = cJSON_CreateObject();
        cJSON_AddStringToObject(js, "label", s->label);
        cJSON_AddNumberToObject(js, "alias", s->alias);
        cJSON_AddNumberToObject(js, "position", s->position);
        cJSON_AddNumberToObject(js, "vendor_id", s->vendor_id);
        cJSON_AddNumberToObject(js, "product_code", s->product_code);
        cJSON_AddStringToObject(js, "sync_group", s->sync_group);
        cJSON_AddItemToObject(js, "txpdo", dump_dir(&s->tx));
        cJSON_AddItemToObject(js, "rxpdo", dump_dir(&s->rx));
        cJSON_AddNumberToObject(js, "sdo_count", s->sdo_count);
        if (s->dc_enabled) {
            cJSON *dc = cJSON_AddObjectToObject(js, "dc");
            cJSON_AddNumberToObject(dc, "assign_activate", s->dc_assign_activate);
            cJSON_AddNumberToObject(dc, "sync0_cycle_ns", s->dc_sync0_cycle_ns);
            cJSON_AddNumberToObject(dc, "sync0_shift_ns", s->dc_sync0_shift_ns);
            cJSON_AddNumberToObject(dc, "sync1_cycle_ns", s->dc_sync1_cycle_ns);
            cJSON_AddNumberToObject(dc, "sync1_shift_ns", s->dc_sync1_shift_ns);
        }
        cJSON_AddItemToArray(slaves, js);
    }
    char *text = cJSON_Print(o);
    puts(text);
    free(text);
    cJSON_Delete(o);
    free(g_cfg.text);
    return 0;
}
