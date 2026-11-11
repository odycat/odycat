# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/_scan.py
"""
`odycat-config scan`: a config for the slaves on the bus.

Runs as a normal user: the IgH `ethercat` tool only needs access to
/dev/EtherCAT0, which members of the odycat group have. The config it
prints is written out in full, as the engine binary needs it, so `sudo`
only ever has to run the engine itself.

    odycat-config scan                      # config on stdout, report on stderr
    odycat-config scan -o machine.json      # write it (refuses to overwrite)
    odycat-config scan --short              # {"device": ...} for known slaves
    odycat-config scan --no-profiles        # ignore the device classes
    odycat-config scan --update machine.json   # machine.json, up to date with the bus
    odycat-config expand config.json -o machine.json   # "device" entries written out

For each slave, in order of preference:
  1. a device class whose profile has the slave's vendor id and product
     code: the class's identity, PDOs (with its entry names) and required
     SDOs, written out; with --short only {"device": "leadshine.ECT60",
     "alias": 60, "position": 0, "label": ...}, which the `odycat`
     command fills in at startup but the engine binary alone does not accept;
  2. otherwise the slave in full, with the PDOs it has assigned now to its
     sync managers: SM2 (outputs) and SM3 (inputs), or others written as
     "sm" (SM0/SM1 on slaves without a mailbox). A sync manager whose
     entries end inside a byte gets a gap to the end of it.
     Entry types and names come from the slave's CoE object dictionary,
     then from the standard CiA 402 drive objects; an entry with neither
     gets an unsigned encoding of its size, and the report says so.

Slaves are addressed as IgH shows them (`alias:offset`, see `ethercat
slaves`), or by ring position with --positions. Labels are generated: rename
them before writing scripts against them.

The bus side reads EtherCATInfo XML (`ethercat xml`) with odycat._esi.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from odycat import _esi as esi
from odycat._config import TYPE_BITS

CYCLE_US_ALLOWED = (125, 250, 500, 1000, 2000, 4000, 8000, 10000)

# CiA 402 drive objects (axis 1; axis n adds 0x800 * (n - 1) to the index):
# (index, subindex) -> (label, type). Labels are the cia402.Servo variable
# names, or the CiA name where Servo has none (as the bundled classes label them).
CIA402 = {
    (0x603F, 0): ("error_code", "u16"),
    (0x6040, 0): ("controlword", "u16"),
    (0x6041, 0): ("statusword", "u16"),
    (0x6060, 0): ("modes_of_operation", "i8"),
    (0x6061, 0): ("mode_display", "i8"),
    (0x6062, 0): ("position_demand_value", "i32"),
    (0x6064, 0): ("position_actual_value", "i32"),
    (0x606B, 0): ("velocity_demand_value", "i32"),
    (0x606C, 0): ("velocity_actual_value", "i32"),
    (0x6071, 0): ("target_torque", "i16"),
    (0x6072, 0): ("max_torque", "u16"),
    (0x6074, 0): ("torque_demand_value", "i16"),
    (0x6077, 0): ("torque_actual_value", "i16"),
    (0x607A, 0): ("target_position", "i32"),
    (0x6081, 0): ("profile_velocity", "u32"),
    (0x6083, 0): ("profile_acceleration", "u32"),
    (0x6084, 0): ("profile_deceleration", "u32"),
    (0x60B1, 0): ("velocity_offset", "i32"),
    (0x60B2, 0): ("torque_offset", "i16"),
    (0x60B8, 0): ("touch_probe_function", "u16"),
    (0x60B9, 0): ("touch_probe_status", "u16"),
    (0x60BA, 0): ("touch_probe_pos1_pos", "i32"),
    (0x60BB, 0): ("touch_probe_pos1_neg", "i32"),
    (0x60BC, 0): ("touch_probe_pos2_pos", "i32"),
    (0x60BD, 0): ("touch_probe_pos2_neg", "i32"),
    (0x60F4, 0): ("following_error_actual_value", "i32"),
    (0x60FD, 0): ("digital_inputs", "u32"),
    (0x60FE, 1): ("digital_outputs", "u32"),
    (0x60FF, 0): ("target_velocity", "i32"),
}
AXIS_STRIDE = 0x800

_DIRECTIONS = (("rxpdo", "rx", 2, 0x1600, 0x17FF), ("txpdo", "tx", 3, 0x1A00, 0x1BFF))

DC_NOTE = ("supports DC; runs in free-run. For CSP/CSV/CST add \"dc_sync\": <Sync0 "
           "period in ns> (0 = the bus cycle) and run the engine with real-time scheduling")
DC_REQUIRED_NOTE = ("does not run in free-run (0x1C32:04 = 0x{:04X}): \"dc_sync\": 0 written "
                    "(Sync0 = the bus cycle); run the engine with real-time scheduling")
NO_DC_NOTE = ("runs neither in free-run nor with DC Sync0 (0x1C32:04 = 0x{:04X}): the "
              "engine supports only these two, the slave will refuse SAFEOP")
NO_MODE_NOTE = ("no modes of operation (0x6060/0x6061) in the PDO map: probably a {} "
                "PDO set, and the drive stays in the mode it starts in. Servo.set_mode() "
                "needs 0x6060 in \"rxpdo\" (another PDO set of the drive) or in \"sdos\"")

# 0x1C32:04, synchronization types supported (ETG.1020)
SYNC_FREE_RUN = 0x0001
SYNC_DC_SYNC0 = 0x0004


class BusSlave(NamedTuple):
    ring: int
    alias: int            # IgH address: alias and offset from that alias
    offset: int
    state: str
    dc: bool
    coe: bool
    device: esi.EsiDevice  # from `ethercat xml` (types not trusted)
    od: dict              # (index, subindex) -> (data type, bits, name), CoE only
    od_note: str          # why there is no dictionary, "" if read
    sync_types: Optional[int] = None   # 0x1C32:04 (CoE + DC slaves), None = unknown


# ---------------------------------------------------------------------------
#  Parsing `ethercat` output (pure functions, tested with recorded output)
# ---------------------------------------------------------------------------

_SLAVE_LINE = re.compile(r"^\s*(\d+)\s+(\d+):(\d+)\s+(\S+)\s+\S\s+(.*)$")


def parse_slaves(text: str) -> list:
    """`ethercat slaves`: [(ring, alias, offset, state, name)]."""
    out = []
    for line in text.splitlines():
        m = _SLAVE_LINE.match(line)
        if m:
            out.append((int(m[1]), int(m[2]), int(m[3]), m[4], m[5].strip()))
    return out


def parse_slave_verbose(text: str) -> dict:
    """`ethercat slaves -v -p N`: DC support and mailbox protocols."""
    dc = re.search(r"Distributed clocks:\s*(\w+)", text)
    protocols = re.search(r"Supported protocols:\s*(.*)", text)
    return {"dc": bool(dc and dc[1].lower() == "yes"),
            "protocols": [p.strip() for p in protocols[1].split(",")] if protocols else []}


_SDO_ENTRY = re.compile(r'^\s+0x([0-9a-fA-F]{4}):([0-9a-fA-F]{2}),\s*\S+,\s*([^,]+),\s*(\d+) bit,\s*"(.*)"\s*$')


def parse_sdos(text: str) -> dict:
    """`ethercat sdos -p N`: {(index, subindex): (data type, bits, name)}."""
    od = {}
    for line in text.splitlines():
        m = _SDO_ENTRY.match(line)
        if m:
            od[(int(m[1], 16), int(m[2], 16))] = (m[3].strip(), int(m[4]), m[5])
    return od


def parse_master_mac(text: str) -> Optional[str]:
    """`ethercat master`: MAC address of the main device."""
    m = re.search(r"Main:\s*([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", text)
    return m[1].lower() if m else None


def interface_for_mac(mac: Optional[str], sys_net: str = "/sys/class/net") -> Optional[str]:
    """Network interface with this MAC address."""
    if not mac:
        return None
    try:
        names = sorted(os.listdir(sys_net))
    except OSError:
        return None
    for name in names:
        try:
            if Path(sys_net, name, "address").read_text().strip().lower() == mac:
                return name
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
#  Reading the bus
# ---------------------------------------------------------------------------

def ethercat_runner(tool: str = "ethercat", master: int = 0) -> Callable[[list], str]:
    """run(args) -> stdout of the IgH tool for one master, in the C locale."""
    env = dict(os.environ, LC_ALL="C")

    def run(args: list) -> str:
        cmd = [tool, *args, "-m", str(master)]
        try:
            # slaves report names in all sorts of encodings: never fail on one
            r = subprocess.run(cmd, capture_output=True, env=env,
                               encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise RuntimeError(f"'{tool}' not found: install the IgH EtherCAT master "
                               f"userland, or pass --ethercat PATH") from None
        if r.returncode:
            msg = (r.stderr or r.stdout).strip()
            hint = (f" Your user needs access to /dev/EtherCAT{master}: add it to the "
                    f"odycat group (see docs/getting-started.md)." if "ermission" in msg else "")
            raise RuntimeError(f"`{' '.join(cmd)}` failed: {msg}.{hint}")
        return r.stdout
    return run


def read_sync_types(run: Callable[[list], str], p: str) -> Optional[int]:
    """0x1C32:04 (synchronization types the slave supports), read with an SDO
    upload; None if the slave does not have it."""
    try:
        out = run(["upload", "-p", p, "0x1C32", "4", "--type", "uint16"]).split()
        return int(out[0], 16) if out else None
    except (RuntimeError, ValueError):
        return None


def read_bus(run: Callable[[list], str]) -> list:
    """Every slave IgH sees, with its PDO mapping and, for CoE slaves, its
    object dictionary."""
    slaves = []
    for ring, alias, offset, state, _ in parse_slaves(run(["slaves"])):
        p = str(ring)
        info = parse_slave_verbose(run(["slaves", "-v", "-p", p]))
        devices = esi.parse(esi.igh_document(run(["xml", "-p", p])), types_trusted=False)
        if len(devices) != 1:
            raise RuntimeError(f"`ethercat xml -p {p}` described {len(devices)} devices")
        coe = "CoE" in info["protocols"]
        od, note = {}, ""
        if coe:
            try:
                od = parse_sdos(run(["sdos", "-p", p]))
            except RuntimeError as e:
                note = f"object dictionary not readable ({e})"
        sync_types = read_sync_types(run, p) if coe and info["dc"] else None
        slaves.append(BusSlave(ring, alias, offset, state, info["dc"], coe,
                               devices[0], od, note, sync_types))
    return slaves


# ---------------------------------------------------------------------------
#  Building the config
# ---------------------------------------------------------------------------

def _snake(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s


def _unique(label: str, used: set) -> str:
    out, n = label, 2
    while out in used:
        out, n = f"{label}_{n}", n + 1
    used.add(out)
    return out


def profiles_by_identity() -> dict:
    """{(vendor id, product code): ["vendor.Model", ...]} of the device classes."""
    from odycat._profiles import device_classes
    out: dict = {}
    for name, cls in device_classes().items():
        prof = cls.PROFILE
        try:
            key = (int(prof["vendor_id"], 0), int(prof["product_code"], 0))
        except (KeyError, TypeError, ValueError):
            continue
        out.setdefault(key, []).append(name)
    for names in out.values():
        names.sort()
    return out


def _cia402(index: int, subindex: int) -> Optional[tuple]:
    if not 0x6000 <= index < 0x6000 + 8 * AXIS_STRIDE:
        return None
    axis, base = divmod(index - 0x6000, AXIS_STRIDE)
    hit = CIA402.get((0x6000 + base, subindex))
    if hit is None:
        return None
    label, code = hit
    return (label if axis == 0 else f"{label}_ax{axis + 1}"), code


def _generic_name(name: str) -> bool:
    return not name or re.fullmatch(r"(?i)sub ?index \d+", name) is not None


def _entry(e: esi.EsiEntry, slave: BusSlave, used: set) -> tuple:
    """A config entry for one PDO entry: (dict, guessed, CiA 402 object)."""
    if e.index == 0:
        return {"index": "0x0000", "subindex": 0, "bits": e.bits}, False, False
    od_type, od_bits, od_name = slave.od.get((e.index, e.subindex), ("", 0, ""))
    cia = _cia402(e.index, e.subindex)
    enc, guessed = esi.entry_type(od_type if od_bits == e.bits else "", e.bits)
    if guessed and cia and TYPE_BITS[cia[1]] == e.bits:
        enc, guessed = {"type": cia[1]}, False          # standard drive object
    if not _generic_name(od_name):
        label = _snake(od_name)
    elif not _generic_name(e.name):
        label = _snake(e.name)
    elif cia:
        label = cia[0]
    else:
        label = ""
    label = _unique(label or f"obj_{e.index:04x}_{e.subindex:02x}", used)
    entry = {"index": f"0x{e.index:04X}", "subindex": e.subindex, **enc, "label": label}
    return entry, guessed, cia is not None


def _full_slave(slave: BusSlave, entry: dict, notes: list) -> dict:
    dev = slave.device
    entry.update({"name": dev.type_name or dev.name,
                  "vendor_id": f"0x{dev.vendor_id:08x}",
                  "product_code": f"0x{dev.product_code:08x}"})
    used: set = set()
    guessed = 0
    drive = False
    for key, direction, default_sm, lo, hi in _DIRECTIONS:
        pdos = []
        bits = 0
        # Each sync manager's PDOs together, in sync manager order (slaves
        # without a mailbox use SM0/SM1, some a byte per sync manager)
        assigned = sorted(dev.assigned(direction), key=lambda pdo: pdo.sm)
        for pdo in assigned:
            if not lo <= pdo.index <= hi:
                notes.append(f"PDO 0x{pdo.index:04X} is outside 0x{lo:04X}-0x{hi:04X}: left out")
                continue
            if pdos and pdos[-1].get("sm", default_sm) != pdo.sm:
                bits = _fill_byte(pdos[-1], bits)
            ents = []
            for e in pdo.entries:
                ent, g, cia = _entry(e, slave, used)
                ents.append(ent)
                guessed += g
                drive = drive or cia
                bits += e.bits
            pdos.append({"index": f"0x{pdo.index:04X}",
                         **({"sm": pdo.sm} if pdo.sm != default_sm else {}), "entries": ents})
        if pdos:
            _fill_byte(pdos[-1], bits)
        entry[key] = pdos[0] if len(pdos) == 1 else (pdos or {"entries": []})
    if guessed:
        notes.append(f"{guessed} entr{'y' if guessed == 1 else 'ies'} typed by size only "
                     f"(unsigned): check which values are signed")
    if slave.od_note:
        notes.append(slave.od_note)
    if drive:
        mapped = {(int(e["index"], 16), e.get("subindex", 0))
                  for key, *_ in _DIRECTIONS for p in _pdo_list(entry[key])
                  for e in p["entries"]}
        if (0x6040, 0) in mapped and not {(0x6060, 0), (0x6061, 0)} & mapped:
            targets = [n for i, n in ((0x607A, "CSP"), (0x60FF, "CSV"), (0x6071, "CST"))
                       if (i, 0) in mapped]
            notes.append(NO_MODE_NOTE.format(targets[0] if len(targets) == 1
                                             else "single-mode"))
    _dc(slave, entry, notes, drive)
    return entry


def _fill_byte(pdo: dict, bits: int) -> int:
    """A sync manager holds whole bytes: a gap at the end of its last PDO
    fills the byte its entries end in. Returns the bits, rounded up."""
    if bits % 8:
        pdo["entries"].append({"index": "0x0000", "subindex": 0, "bits": 8 - bits % 8})
    return bits + (-bits % 8)


def _pdo_list(side) -> list:
    return side if isinstance(side, list) else [side]


def _dc(slave: BusSlave, entry: dict, notes: list, suggest: bool) -> None:
    """dc_sync for a slave that cannot run in free-run; else the DC note
    (suggest: a drive, which wants DC for the cyclic modes)."""
    if not slave.dc:
        return
    st = slave.sync_types
    if st is not None and st and not st & SYNC_FREE_RUN:
        if st & SYNC_DC_SYNC0:
            if "dc_sync" not in entry:
                entry["dc_sync"] = 0
            notes.append(DC_REQUIRED_NOTE.format(st))
        else:
            notes.append(NO_DC_NOTE.format(st))
    elif suggest and "dc_sync" not in entry:
        notes.append(DC_NOTE)


def _from_class(name: str, address: dict, label: str) -> dict:
    """A slave entry written out from its device class, without "device" (so
    nothing expands it a second time), keys in the usual order."""
    from odycat._profiles import expand_slave
    full = expand_slave({"device": name, **address, "label": label})
    del full["device"]
    first = {k: full.pop(k) for k in ("alias", "position", "name", "label",
                                      "vendor_id", "product_code") if k in full}
    return {**first, **full}


def _slave_entry(s: BusSlave, profiles: dict, positions: bool, short: bool,
                 labels: set) -> tuple:
    """(entry, report lines) for one bus slave: from its device class if it
    has one, else written in full from what the slave reports."""
    dev = s.device
    alias, position = (0, s.ring) if positions else (s.alias, s.offset)
    entry = {"alias": alias, "position": position} if alias else {"position": position}
    notes: list = []
    classes = profiles.get((dev.vendor_id, dev.product_code), [])
    if classes:
        label = _unique(_snake(classes[0].split(".", 1)[1]), labels)
        if short:
            entry = {"device": classes[0], **entry, "label": label}
            what = f"device {classes[0]}"
        else:
            entry = _from_class(classes[0], entry, label)
            what = f"device {classes[0]}, written out"
        if len(classes) > 1:
            notes.append(f"also matches: {', '.join(classes[1:])}")
        from odycat._profiles import find_device
        from odycat.devices import support, support_text
        cls = find_device(classes[0])
        what += f"; support: {support(cls) or 'none'}"
        tested = vars(cls).get("TESTED_REVISIONS") or ()
        if support(cls) == "tested" and dev.revision is not None and dev.revision not in tested:
            notes.append(f"revision 0x{dev.revision:08X} is not the one tested: "
                         f"{support_text(cls)}")
        if getattr(cls, "_prepare", None) and not dev.sms:
            notes.append(f"needs to be prepared once before IgH can use its PDOs: "
                         f"odycat-config prepare --position {s.ring}")
        template = getattr(cls, "DC_SYNC", None)
        _dc(s, entry, notes, bool(template))
        if not short and entry.get("dc_sync") == 0:
            from odycat._profiles import dc_sync_settings
            entry["dc_sync"] = dc_sync_settings(0, template, label)
    else:
        base = dev.type_name.split(" (")[0] if dev.type_name else dev.name
        entry["label"] = _unique(_snake(base) or f"slave_{s.ring}", labels)
        _full_slave(s, entry, notes)
        what = "written in full"
    return entry, [_report_line(s, f"{entry['label']} ({what})")] + [f"        {n}" for n in notes]


def _report_line(s: BusSlave, text: str) -> str:
    return f"[{s.ring:>3}] {s.alias}:{s.offset:<3} {s.device.name[:40]:<40} -> {text}"


def _alias_clash(slaves: list) -> bool:
    addresses = [(s.alias, s.offset) for s in slaves]
    return len(set(addresses)) != len(addresses)


def build_config(slaves: list, *, bus: str, cycle_us: int = 1000,
                 use_profiles: bool = True, short: bool = False,
                 positions: bool = False, master: int = 0) -> tuple:
    """(config, report): the config for these slaves and one report line per
    slave (plus indented notes). Slaves with a device class are written out
    from it, or as {"device": ...} with short=True."""
    profiles = profiles_by_identity() if use_profiles else {}
    report = []
    if not positions and _alias_clash(slaves):
        positions = True
        report.append("Two slaves share an alias address: addressing all by ring position.")
    labels: set = set()
    out = []
    for s in slaves:
        entry, lines = _slave_entry(s, profiles, positions, short, labels)
        report.extend(lines)
        out.append(entry)
    cfg = {"bus": bus}
    if master:
        cfg["master"] = master                       # default 0 is left out
    return {**cfg, "cycle_us": cycle_us, "slaves": out}, report


# ---------------------------------------------------------------------------
#  Updating an existing config
# ---------------------------------------------------------------------------

def _identity(entry: dict) -> Optional[tuple]:
    """(vendor id, product code) of a config entry, also through "device"."""
    try:
        if "vendor_id" in entry and "product_code" in entry:
            return int(str(entry["vendor_id"]), 0), int(str(entry["product_code"]), 0)
        if "device" in entry:
            from odycat._profiles import find_device
            prof = find_device(entry["device"]).PROFILE
            return int(prof["vendor_id"], 0), int(prof["product_code"], 0)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _address(entry: dict) -> tuple:
    """(alias, position) as the config writes it."""
    return int(entry.get("alias", 0)), int(entry.get("position", -1))


def _at_address(entry: dict, s: BusSlave) -> bool:
    """Whether a config entry addresses this bus slave: alias 0 means a ring
    position, any other alias an offset from that alias (as IgH does)."""
    alias, position = _address(entry)
    return s.ring == position if alias == 0 else (s.alias, s.offset) == (alias, position)


def _describe(entry: dict) -> str:
    alias, position = _address(entry)
    ident = _identity(entry)
    what = entry.get("device") or (f"0x{ident[0]:08x}:0x{ident[1]:08x}" if ident else "?")
    return f"'{entry.get('label', '?')}' at {alias}:{position} ({what})"


def update_config(old: dict, slaves: list, *, use_profiles: bool = True,
                  short: bool = False, positions: bool = False,
                  bus: Optional[str] = None,
                  cycle_us: Optional[int] = None) -> tuple:
    """(config, report): `old` brought up to date with the bus. A slave at
    the same address with the same identity keeps its entry exactly as
    written (label, SDOs, PDO edits, dc_sync ...). New slaves are added with
    generated labels, a different device at a known address replaces the
    entry, and entries for slaves no longer on the bus are removed; the
    report flags each of these. Top-level settings are kept; bus and
    cycle_us replace them only when given."""
    profiles = profiles_by_identity() if use_profiles else {}
    old_slaves = list(old.get("slaves", []))
    report = []
    # new entries use the config's addressing: ring positions if it has no alias
    if not positions:
        positions = bool(old_slaves) and all(_address(e)[0] == 0 for e in old_slaves)
    if not positions and _alias_clash(slaves):
        positions = True
        report.append("Two slaves share an alias address: new entries use ring positions.")

    matched: dict = {}                     # bus index -> old index
    for bi, s in enumerate(slaves):
        for oi, e in enumerate(old_slaves):
            if oi not in matched.values() and _at_address(e, s):
                matched[bi] = oi
                break
    kept = {bi for bi, oi in matched.items()
            if _identity(old_slaves[oi]) == (slaves[bi].device.vendor_id,
                                             slaves[bi].device.product_code)}
    labels = {old_slaves[matched[bi]].get("label") for bi in kept}
    counts = {"kept": 0, "added": 0, "replaced": 0, "removed": 0}
    out = []
    for bi, s in enumerate(slaves):
        if bi in kept:
            entry = old_slaves[matched[bi]]
            report.append(_report_line(s, f"{entry.get('label')} (kept as written)"))
            counts["kept"] += 1
        else:
            entry, lines = _slave_entry(s, profiles, positions, short, labels)
            if bi in matched:
                lines[0] += f"  REPLACED {_describe(old_slaves[matched[bi]])}"
                counts["replaced"] += 1
            else:
                lines[0] += "  ADDED"
                counts["added"] += 1
            report.extend(lines)
        out.append(entry)
    for oi, e in enumerate(old_slaves):
        if oi not in matched.values():
            report.append(f"[  -] REMOVED {_describe(e)}: not on the bus")
            counts["removed"] += 1
    report.append(", ".join(f"{n} {k}" for k, n in counts.items()))

    cfg = dict(old)                        # the config's own key order
    if bus:
        cfg["bus"] = bus
    if cycle_us:
        cfg["cycle_us"] = cycle_us
    cfg["slaves"] = out
    return cfg, report


def format_config(cfg: dict) -> str:
    """JSON in the style of the bundled configs: objects holding only plain
    values (an entry, a short slave) on one line."""
    def simple(v) -> bool:
        return not isinstance(v, (dict, list)) or v in ({}, [])

    def fmt(v, ind: int) -> str:
        pad, pad2 = " " * ind, " " * (ind + 2)
        if isinstance(v, dict) and v:
            if all(simple(x) for x in v.values()):
                return "{ " + ", ".join(f"{json.dumps(k)}: {json.dumps(x)}"
                                        for k, x in v.items()) + " }"
            return "{\n" + ",\n".join(f"{pad2}{json.dumps(k)}: {fmt(x, ind + 2)}"
                                      for k, x in v.items()) + f"\n{pad}}}"
        if isinstance(v, list) and v:
            return "[\n" + ",\n".join(pad2 + fmt(x, ind + 2) for x in v) + f"\n{pad}]"
        return json.dumps(v)
    return fmt(cfg, 0) + "\n"


# ---------------------------------------------------------------------------
#  Command line
# ---------------------------------------------------------------------------

def _scan(args) -> int:
    old = _read_config_file(args.update) if args.update else None
    master = args.master if args.master is not None else int((old or {}).get("master", 0))
    run = ethercat_runner(args.ethercat, master)
    slaves = read_bus(run)
    if not slaves:
        print("odycat-config: IgH sees no slaves (is the master started and the "
              "cable plugged?)", file=sys.stderr)
        return 1
    if old is not None:
        cfg, report = update_config(old, slaves, use_profiles=not args.no_profiles,
                                    short=args.short, positions=args.positions,
                                    bus=args.bus, cycle_us=args.cycle_us)
        bus = cfg.get("bus", "?")
        title = f"OdyCAT config update of {args.update}"
    else:
        bus = args.bus or f"bus{master}"
        cfg, report = build_config(slaves, bus=bus,
                                   cycle_us=args.cycle_us or 1000,
                                   use_profiles=not args.no_profiles, short=args.short,
                                   positions=args.positions, master=master)
        title = "OdyCAT config scan"
    nic = interface_for_mac(parse_master_mac(run(["master"])))
    print(f"{title} — {len(slaves)} slave(s) on master {master}"
          f"{f' ({nic})' if nic else ''}, bus \"{bus}\"", file=sys.stderr)
    for line in report:
        print(line, file=sys.stderr)
    return _write(format_config(cfg), args,
                  "Rename the generated labels, then start the engine with it.")


def _write(text: str, args, hint: str) -> int:
    """The config to -o FILE (refusing to overwrite without --force) or stdout."""
    if args.output:
        out = Path(args.output)
        if out.exists() and not args.force:
            print(f"odycat-config: {out} exists; pass --force to overwrite", file=sys.stderr)
            return 1
        out.write_text(text, encoding="utf-8")
        print(f"Written: {out}. {hint}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def _read_config_file(path: str) -> dict:
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise RuntimeError(f"cannot read {path}: {e.strerror}") from None
    except ValueError as e:
        raise RuntimeError(f"{path} is not valid JSON: {e}") from None
    if not isinstance(cfg, dict) or not isinstance(cfg.get("slaves", []), list):
        raise RuntimeError(f"{path} is not a OdyCAT config")
    return cfg


def _expand(args) -> int:
    """The config with every "device" entry filled in from its class, as the
    engine binary needs it."""
    from odycat._profiles import expand_config, uses_profiles
    cfg = _read_config_file(args.config)
    try:
        full = expand_config(cfg)
    except (KeyError, ValueError) as e:
        raise RuntimeError(e.args[0] if e.args else str(e)) from None
    if not uses_profiles(cfg):
        print(f"odycat-config: {args.config} names no device classes; written as it is",
              file=sys.stderr)
    return _write(format_config(full), args,
                  f"Start the engine with it: sudo odycat {args.output}")


def _prepare_cmd(args) -> int:
    from pathlib import Path
    from odycat import _prepare
    slave = _prepare.Slave(args.position, args.ethercat, args.master)
    if args.restore:
        return _prepare.restore(slave, Path(args.restore))
    return _prepare.prepare(slave, Path(args.backup_dir), args.write)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="odycat-config",
                                 description="Build OdyCAT configs from the bus.")
    sub = ap.add_subparsers(dest="command", required=True)
    sc = sub.add_parser("scan", help="print a config for the slaves on the bus",
                        description=__doc__.split("\n\n")[1])
    sc.add_argument("-o", "--output", help="write the config to this file")
    sc.add_argument("--force", action="store_true", help="overwrite --output")
    sc.add_argument("--bus", help="the bus name in the config, which names its shared "
                    "memory and is what scripts attach to (default: bus<master>, e.g. bus0)")
    sc.add_argument("--update", metavar="CONFIG",
                    help="bring this config up to date with the bus: slaves still at "
                         "the same address keep their entries as written (labels, SDOs, "
                         "edits); new, replaced and removed slaves are reported. Prints "
                         "the result (or -o FILE); the file itself is not changed")
    sc.add_argument("--cycle-us", type=int, choices=CYCLE_US_ALLOWED,
                    help="bus cycle (default 1000; with --update, the config's)")
    sc.add_argument("--positions", action="store_true",
                    help="address slaves by ring position instead of alias:offset")
    sc.add_argument("--short", action="store_true",
                    help='slaves with a device class as {"device": ...}: shorter, but '
                         "the engine needs it written out first (odycat-config expand)")
    sc.add_argument("--no-profiles", action="store_true",
                    help="ignore the device classes: every slave from the bus, with "
                         "the entry names the slave reports")
    sc.add_argument("--master", "-m", type=int,
                    help="IgH master to scan (one per network card; default 0, with "
                         "--update the config's)")
    sc.add_argument("--ethercat", default="ethercat", help="path of the IgH tool")
    ex = sub.add_parser("expand", help='write a config with "device" entries out in full',
                        description='Fill in every "device" entry from its device class '
                                    "(identity, PDOs, SDOs, DC template), as the engine "
                                    "binary needs it. Runs as a normal user.")
    ex.add_argument("config", help="the config to expand")
    ex.add_argument("-o", "--output", help="write the result to this file")
    ex.add_argument("--force", action="store_true", help="overwrite --output")
    pr = sub.add_parser(
        "prepare", help="the one-off change a device needs before IgH can use it",
        description="Some devices need a one-off change before IgH can use them, for "
                    "example an SII without its sync manager and PDO sections. Shows "
                    "what would change and keeps a backup of the slave's SII; writes "
                    "only with --write, while the engine is stopped. Runs as a normal "
                    "user in the odycat group.")
    pr.add_argument("--position", "-p", type=int, required=True,
                    help="the slave's ring position, as `ethercat slaves` numbers it")
    pr.add_argument("--write", action="store_true",
                    help="write the new SII to the slave's EEPROM")
    pr.add_argument("--restore", metavar="FILE",
                    help="write a backup back to the slave's EEPROM")
    pr.add_argument("--backup-dir", default="sii_backup",
                    help="where the backup and the new image go (default ./sii_backup)")
    pr.add_argument("--master", "-m", type=int, default=0, help="IgH master (default 0)")
    pr.add_argument("--ethercat", default="ethercat", help="path of the IgH tool")
    args = ap.parse_args(argv)
    try:
        if args.command == "prepare":
            sys.exit(_prepare_cmd(args))
        sys.exit(_expand(args) if args.command == "expand" else _scan(args))
    except RuntimeError as e:
        sys.exit(f"odycat-config: {e}")


if __name__ == "__main__":
    main()
