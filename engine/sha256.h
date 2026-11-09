/*
 * OdyCAT Engine — SHA-256 (FIPS 180-4), for the config hash published in
 * the config region. No IgH dependency, no external library.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#ifndef ODYCAT_SHA256_H
#define ODYCAT_SHA256_H

#include <stddef.h>
#include <stdint.h>

/* SHA-256 of len bytes at data, into out[32]. */
void sha256(const void *data, size_t len, uint8_t out[32]);

#endif
