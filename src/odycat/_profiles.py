# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/_profiles.py
"""
Device profiles — the config a device class needs, filled in from the class.

A device class can carry a PROFILE: the parts of a slave entry that are the
same for every unit of that product (identity, PDO mapping, DC, required
SDOs). A slave entry that names the class with "device" only has to give its
address and label:

    { "device": "leadshine.ECT60", "alias": 60, "label": "axis_x" }

expand_config() turns it into a complete entry before the engine sees it.
Keys given in the entry win over the profile, except:
  - "sdos": the entry's SDOs are written after the profile's;
  - a key set to null removes it from the profile.

DC is never turned on by a profile: slaves run in free-run unless their
entry has "dc_sync". "dc_sync": <number> turns DC on with that Sync0 period
in ns (0 = the bus cycle); "dc_sync": {...} gives the settings in full. Both
start from the class's DC_SYNC template (its assign_activate word).
"""

import copy
import json
import re

# PDO index ranges (CoE): RxPDOs carry the slave's outputs, TxPDOs its inputs.
PDO_RANGES = {"rxpdo": (0x1600, 0x17FF), "txpdo": (0x1A00, 0x1BFF)}


def _norm(name: str) -> str:
    """Upper case without separators: EC3A-IO1632 = ec3a_io1632 = EC3A_IO1632."""
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def device_classes() -> dict:
    """{"vendor.Model": class} of every device class with a PROFILE."""
    from odycat.devices import device_classes as catalogue
    return catalogue()


def find_device(name: str):
    """The device class called "vendor.Model" (vendor in any case, model
    ignoring case and '-'/'_': "delta.ax-516am" = "delta.AX516AM")."""
    classes = device_classes()
    if "." not in name:
        matches = [k for k in classes if _norm(k.split(".", 1)[1]) == _norm(name)]
        hint = f" Did you mean {' or '.join(repr(m) for m in matches)}?" if matches else ""
        raise KeyError(f"OdyCAT: device names are \"vendor.Model\" (e.g. "
                       f"\"leadshine.ECT60\"); got \"{name}\".{hint}")
    vendor, model = name.split(".", 1)
    for key, cls in classes.items():
        v, m = key.split(".", 1)
        if v == vendor.lower() and _norm(m) == _norm(model):
            return cls
    raise KeyError(f"OdyCAT: unknown device '{name}' (known: "
                   f"{', '.join(sorted(classes))})")


def expand_slave(entry: dict) -> dict:
    """A slave entry with its "device" profile filled in (a new dict)."""
    if "device" not in entry:
        return entry
    cls = find_device(entry["device"])
    out = copy.deepcopy(cls.PROFILE)
    out.setdefault("name", cls.__name__)
    for key, value in entry.items():
        if value is None:
            out.pop(key, None)
        elif key == "sdos":
            out["sdos"] = out.get("sdos", []) + list(value)
        elif key == "dc_sync":
            out[key] = dc_sync_settings(value, getattr(cls, "DC_SYNC", None),
                                        entry.get("label", "?"))
        else:
            out[key] = copy.deepcopy(value)
    return out


def dc_sync_settings(value, template, label: str = "?") -> dict:
    """The "dc_sync" object for a config value: a number is the Sync0 period
    in ns (0 = the bus cycle), an object gives the settings in full; both
    start from the class template (assign_activate)."""
    base = dict(template or {})
    if isinstance(value, bool) or not isinstance(value, (int, dict)):
        raise ValueError(f"OdyCAT: slave '{label}': \"dc_sync\" must be the Sync0 period "
                         f"in ns (0 = the bus cycle), an object, or null; got {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"OdyCAT: slave '{label}': \"dc_sync\" {value} is negative")
        return {**base, "sync0_cycle_ns": value}
    return {**base, **copy.deepcopy(value)}


def _check_pdo_ranges(slave: dict) -> None:
    for key, (lo, hi) in PDO_RANGES.items():
        blk = slave.get(key) or []
        for pdo in blk if isinstance(blk, list) else [blk]:
            if not pdo.get("entries") or "index" not in pdo:
                continue
            idx = pdo["index"]
            idx = int(idx, 0) if isinstance(idx, str) else int(idx)
            if not lo <= idx <= hi:
                raise ValueError(
                    f"OdyCAT: slave '{slave.get('label', '?')}': {key} index "
                    f"0x{idx:04X} is outside 0x{lo:04X}-0x{hi:04X}. \"rxpdo\" "
                    f"holds the slave's RxPDOs (outputs, 0x16xx), \"txpdo\" "
                    f"its TxPDOs (inputs, 0x1Axx)")


def expand_config(cfg: dict) -> dict:
    """The config with every "device" profile filled in; PDO indexes checked."""
    out = dict(cfg)
    out["slaves"] = [expand_slave(s) for s in cfg.get("slaves", [])]
    for s in out["slaves"]:
        _check_pdo_ranges(s)
    return out


def uses_profiles(cfg: dict) -> bool:
    return any("device" in s for s in cfg.get("slaves", []))


def load_config(path: str) -> dict:
    """Read a config file and expand it."""
    with open(path, encoding="utf-8") as f:
        return expand_config(json.load(f))
