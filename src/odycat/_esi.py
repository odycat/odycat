# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/_esi.py
"""
Internal: the EtherCATInfo XML that `ethercat xml -p N` writes for one
slave, from what the slave itself reports (identity, sync managers, PDOs,
DC modes), parsed into EsiDevice objects for `odycat-config scan`.

`ethercat xml` makes up each entry's DataType from its bit length (a signed
32-bit position comes out as UINT32), so these documents are parsed with
types_trusted=False and only the bit length decides the type.

Vendor ESI files use the same format but are not supported yet.
"""

import re
import xml.etree.ElementTree as ET
from typing import NamedTuple, Optional, Union
from xml.sax.saxutils import escape

from odycat._config import TYPE_BITS


class EsiEntry(NamedTuple):
    index: int            # 0 = gap
    subindex: int
    bits: int
    name: str
    data_type: str        # as written in the document, "" if none


class EsiPdo(NamedTuple):
    direction: str        # "rx" (the slave's outputs) or "tx" (its inputs)
    index: int
    name: str
    sm: Optional[int]     # sync manager in the default assignment, None = not assigned
    fixed: bool
    mandatory: bool
    entries: tuple        # of EsiEntry


class EsiSm(NamedTuple):
    start: int
    control: int
    default_size: int
    enabled: bool


class EsiDcMode(NamedTuple):
    name: str
    description: str
    assign_activate: int


class EsiDevice(NamedTuple):
    vendor_id: int
    product_code: int
    revision: Optional[int]
    type_name: str        # <Type> text, usually the order number
    name: str
    sms: tuple            # of EsiSm, in sync manager order
    pdos: tuple           # of EsiPdo, in document order
    dc_modes: tuple       # of EsiDcMode
    types_trusted: bool   # False for `ethercat xml` output

    def assigned(self, direction: str, sm: Optional[int] = None) -> list:
        """PDOs of a direction in the default assignment (optionally of one SM)."""
        return [p for p in self.pdos if p.direction == direction and p.sm is not None
                and (sm is None or p.sm == sm)]


def esi_int(text: Optional[str]) -> Optional[int]:
    """ESI numbers: "#x1A00" (hex), "0x1A00" or decimal; None if empty."""
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    if s[:2] in ("#x", "#X"):
        return int(s[2:], 16)
    return int(s, 0)


def _text(el, tag: str) -> str:
    """Text of a child; with several <Name LcId=...>, English (1033) or the first."""
    found = el.findall(tag)
    if not found:
        return ""
    for f in found:
        if f.get("LcId") == "1033":
            return (f.text or "").strip()
    return (found[0].text or "").strip()


def _bool(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true")


def _device(el, vendor_id: int, types_trusted: bool) -> EsiDevice:
    typ = el.find("Type")
    sms = tuple(EsiSm(esi_int(sm.get("StartAddress")) or 0,
                      esi_int(sm.get("ControlByte")) or 0,
                      esi_int(sm.get("DefaultSize")) or 0,
                      _bool(sm.get("Enable", "1")))
                for sm in el.findall("Sm"))
    pdos = []
    for p in el:
        if p.tag not in ("RxPdo", "TxPdo"):
            continue
        entries = tuple(EsiEntry(esi_int(_text(e, "Index")) or 0,
                                 esi_int(_text(e, "SubIndex")) or 0,
                                 esi_int(_text(e, "BitLen")) or 0,
                                 _text(e, "Name"), _text(e, "DataType"))
                        for e in p.findall("Entry"))
        pdos.append(EsiPdo("rx" if p.tag == "RxPdo" else "tx",
                           esi_int(_text(p, "Index")) or 0, _text(p, "Name"),
                           esi_int(p.get("Sm")), _bool(p.get("Fixed")),
                           _bool(p.get("Mandatory")), entries))
    dc_modes = tuple(EsiDcMode(_text(m, "Name"), _text(m, "Desc"),
                               esi_int(_text(m, "AssignActivate")) or 0)
                     for m in el.findall("Dc/OpMode"))
    return EsiDevice(vendor_id,
                     esi_int(typ.get("ProductCode")) if typ is not None else 0,
                     esi_int(typ.get("RevisionNo")) if typ is not None else None,
                     (typ.text or "").strip() if typ is not None else "",
                     _text(el, "Name"), sms, tuple(pdos), dc_modes, types_trusted)


# Characters XML 1.0 does not allow in a document, even escaped.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
# The text `ethercat xml` copies from the slave's EEPROM as it is: the order
# number in <Type>, PDO and entry names, and the device name inside CDATA.
_IGH_TEXT = re.compile(r"(<(Type|Name)\b[^>]*>)(.*?)(</\2>)", re.DOTALL)


def _igh_text(m: "re.Match") -> str:
    text = m.group(3)
    if text.startswith("<![CDATA[") and text.endswith("]]>"):
        text = text[9:-3]
    return m.group(1) + escape(text) + m.group(4)


def igh_document(text: str) -> str:
    """The output of `ethercat xml`, made well-formed. IgH writes the strings
    of the slave's EEPROM without escaping them, and a slave may store
    anything there: control bytes, "&", "<". Characters XML cannot hold become
    U+FFFD; the rest is escaped."""
    return _IGH_TEXT.sub(_igh_text, _NOT_XML.sub("\ufffd", text))


def parse(data: Union[str, bytes], types_trusted: bool = True) -> list:
    """Every device of an EtherCATInfo document."""
    root = ET.fromstring(data)
    if root.tag != "EtherCATInfo":
        raise ValueError(f"not an EtherCATInfo document (root <{root.tag}>)")
    vendor_id = esi_int(root.findtext("Vendor/Id")) or 0
    return [_device(d, vendor_id, types_trusted)
            for d in root.findall("Descriptions/Devices/Device")]


# ---------------------------------------------------------------------------
#  Data types
# ---------------------------------------------------------------------------

# ESI (IEC 61131) names, CoE dictionary names (`ethercat sdos`) and the
# sized names `ethercat xml` writes, to the config's types.
_TYPES = {
    "SINT": "i8", "INT8": "i8",
    "USINT": "u8", "UINT8": "u8", "BYTE": "u8",
    "INT": "i16", "INT16": "i16",
    "UINT": "u16", "UINT16": "u16", "WORD": "u16",
    "DINT": "i32", "INT32": "i32",
    "UDINT": "u32", "UINT32": "u32", "DWORD": "u32",
    "LINT": "i64", "INT64": "i64",
    "ULINT": "u64", "UINT64": "u64", "LWORD": "u64",
    "REAL": "f32", "REAL32": "f32", "FLOAT": "f32",
    "LREAL": "f64", "REAL64": "f64", "DOUBLE": "f64",
}
_UNSIGNED_BY_BITS = {8: "u8", 16: "u16", 32: "u32", 64: "u64"}


def entry_type(data_type: str, bits: int) -> tuple:
    """How a config entry describes a value: ({"type": t} or {"bits": n},
    guessed). guessed is True when the data type was unknown and the
    unsigned type of the entry's size was used."""
    name = (data_type or "").strip().upper()
    typ = _TYPES.get(name)
    if typ and TYPE_BITS[typ] == bits:
        return {"type": typ}, False
    if name in ("BOOL", "BIT"):
        return ({"type": "bool"} if bits == 8 else {"bits": bits}), False
    if bits in _UNSIGNED_BY_BITS:
        return {"type": _UNSIGNED_BY_BITS[bits]}, True
    return {"bits": bits}, False
