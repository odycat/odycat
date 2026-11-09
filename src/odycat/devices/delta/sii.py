# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/delta/sii.py
"""The SII the AX-516AM and AX-516AN modules need before IgH can exchange
their process data (`odycat-config prepare`).

IgH sets up the process-data sync managers from the SII. The modules come
with an SII of the 64-word header only, so they reach PREOP but exchange no
PDOs. The new SII keeps that header as read from the module (identity,
mailbox, station alias) and adds the Strings, General, FMMU, SyncM and PDO
sections for the module's fixed layout of two 8-bit PDOs, with the values of
Delta's ESI files for these modules.
"""

from __future__ import annotations

import struct

from odycat._prepare import SII_HEADER_BYTES, Plan

MODELS = {   # product code: (order number, kind, name)
    0x10512081: ("AX-516AM20-0A", "AM",
                 "AX-516AM20-0A (16 DI, 24VDC, 2.5mA, 1ms, Sink Input (PNP))"),
    0x10512181: ("AX-516AN02-0A", "AN",
                 "AX-516AN02-0A (16 DO, 24VDC, 0.5A, 1ms, Source Output (PNP))"),
}

CAT_STRINGS, CAT_GENERAL, CAT_FMMU, CAT_SYNCM = 10, 30, 40, 41
CAT_TXPDO, CAT_RXPDO, CAT_END = 50, 51, 0xFFFF

# ETG.2010 General.CoEDetails bits
COE_SDO, COE_SDO_INFO, COE_PDO_ASSIGN, COE_COMPLETE_ACCESS = 0x01, 0x02, 0x04, 0x20
SM_MBX_OUT, SM_MBX_IN, SM_PD_OUT, SM_PD_IN = 1, 2, 3, 4
DT_BOOL = 0x01


def category(cat_type: int, data: bytes) -> bytes:
    if len(data) % 2:
        data += b"\x00"
    return struct.pack("<HH", cat_type, len(data) // 2) + data


def strings_cat(strings: list) -> bytes:
    data = bytes([len(strings)])
    for s in strings:
        raw = s.encode("ascii")
        data += bytes([len(raw)]) + raw
    return category(CAT_STRINGS, data)


def general_cat(order_idx: int, name_idx: int) -> bytes:
    # CoE: SDO info, PDO assign, complete access; fixed PDOs (no PDO config)
    coe = COE_SDO | COE_SDO_INFO | COE_PDO_ASSIGN | COE_COMPLETE_ACCESS
    data = struct.pack(
        "<BBBBBBBBBBBBhBBHH12x",
        0, 0, order_idx, name_idx,  # group, image, order, name string index
        0, coe, 0, 0, 0, 0, 0, 0,   # reserved, CoE, FoE, EoE, SoE, DS402, sysman, flags
        0, 0, 0,                    # E-bus current, group idx, reserved
        0, 0,                       # physical port, physical memory address
    )
    return category(CAT_GENERAL, data)


def syncm_cat(kind: str) -> bytes:
    # Only the process-data SM the module uses is enabled, 2 bytes by default.
    outputs = kind == "AN"
    sms = [
        (0x1000, 128, 0x26, 1, SM_MBX_OUT),
        (0x1080, 128, 0x22, 1, SM_MBX_IN),
        (0x1100, 2 if outputs else 0, 0x64, int(outputs), SM_PD_OUT),
        (0x1400, 0 if outputs else 2, 0x20, int(not outputs), SM_PD_IN),
    ]
    data = b"".join(struct.pack("<HHBBBB", start, length, ctrl, 0, enable, typ)
                    for start, length, ctrl, enable, typ in sms)
    return category(CAT_SYNCM, data)


def pdo_cat(cat_type: int, pdos: list) -> bytes:
    """pdos: [(pdo index, object index, sync manager)], 8 one-bit entries each."""
    data = b""
    for pdo_index, obj, sm in pdos:
        data += struct.pack("<HBBBBH", pdo_index, 8, sm, 0, 0, 0)
        for sub in range(1, 9):
            data += struct.pack("<HBBBBH", obj, sub, 0, DT_BOOL, 1, 0)
    return category(cat_type, data)


def build_image(header: bytes, product: int) -> bytes:
    """The complete SII: the module's own header plus the sections."""
    model, kind, name = MODELS[product]
    cats = strings_cat([model, name]) + general_cat(1, 2)
    cats += category(CAT_FMMU, bytes([0x01, 0x02, 0x03, 0x00]))  # outputs, inputs, mailbox
    cats += syncm_cat(kind)
    if kind == "AM":
        cats += pdo_cat(CAT_TXPDO, [(0x1A00, 0x6000, 3), (0x1A01, 0x6001, 3)])
    else:
        cats += pdo_cat(CAT_RXPDO, [(0x1600, 0x7000, 2), (0x1601, 0x7001, 2)])
    return header[:SII_HEADER_BYTES] + cats + struct.pack("<H", CAT_END)


def categories(sii: bytes) -> list:
    """[(type, size in words)] of the sections after the header, up to the end
    marker or the first word that cannot start a section."""
    out, off = [], SII_HEADER_BYTES
    while off + 4 <= len(sii):
        cat_type, size = struct.unpack_from("<HH", sii, off)
        if cat_type in (CAT_END, 0x0000):
            break
        out.append((cat_type, size))
        off += 4 + size * 2
    return out


def plan(sii: bytes) -> Plan:
    """What `odycat-config prepare` changes on this module: nothing once its
    SII has a SyncM section."""
    product = struct.unpack_from("<I", sii, 0x14)[0]
    model = MODELS[product][0]
    found = [t for t, _ in categories(sii)]
    if CAT_SYNCM in found:
        return Plan(None, [f"{model}: the SII has its sync manager and PDO sections"])
    image = build_image(sii, product)
    alias = struct.unpack_from("<H", sii, 8)[0]
    return Plan(image, [
        f"{model}: the SII is the header only ({len(sii)} B read)",
        "adds the Strings, General, FMMU, SyncM and PDO sections for the fixed "
        "2 x 8-bit PDO layout; the header (identity, mailbox, station alias "
        f"{alias}) is kept as read",
    ])
