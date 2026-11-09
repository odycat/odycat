# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/_config.py
"""
Config discovery — reads the config region created by the C engine.

The engine keeps its shared-memory regions as files on tmpfs in its run
directory, /run/odycat by default (ODYCAT_RUN_DIR overrides it, for the
engine and for Python alike). The config region, <run dir>/<bus>_config,
holds the raw config.json with a 4-byte length prefix:
  [ uint32 json_len ] [ json_len bytes of UTF-8 JSON ]

This module provides:
  - the run directory and the region paths
  - opening a region, refusing one that the engine did not create
  - auto-detect bus (scan <run dir>/*_config)
  - read + parse config from the config region
  - per-slave lookup by position
"""

import errno
import glob
import json
import mmap
import os
import stat
import struct
from typing import Optional

from odycat import _shm

# Module-level cache keyed by bus name: (config region inode, config).
# The inode changes when the engine is restarted, which invalidates the entry.
_config_cache: dict[str, tuple[int, dict]] = {}

RUN_DIR_DEFAULT = "/run/odycat"

SHM_GROUP_HINT = (
    "The engine gives its shared memory to the 'odycat' group (mode 0660, "
    "or the config's 'shm_group'). Add this user to it: "
    "sudo usermod -aG odycat $USER, then log in again."
)


def run_dir() -> str:
    """The engine's run directory: $ODYCAT_RUN_DIR, else /run/odycat."""
    return os.environ.get("ODYCAT_RUN_DIR") or RUN_DIR_DEFAULT


def _check_region(fd: int, path: str) -> None:
    """Refuse a region the engine did not create: it must be a regular file,
    closed to other users, owned by root or by the owner of the run
    directory (the user the engine runs as), in a directory only its owner
    can write to. Another local user cannot then feed a script fake inputs
    or collect its outputs through a planted file."""
    st = os.fstat(fd)
    folder = os.path.dirname(path)
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"OdyCAT: {path} is not a regular file; refusing to use it")
    if st.st_mode & 0o007:
        raise RuntimeError(f"OdyCAT: {path} is open to all users (mode "
                           f"{st.st_mode & 0o777:04o}), so an OdyCAT engine did not "
                           f"create it; refusing to use it")
    d = os.stat(folder)
    if d.st_mode & 0o022:
        raise RuntimeError(f"OdyCAT: the run directory {folder} is writable by other "
                           f"users (mode {d.st_mode & 0o777:04o}); refusing to use it. "
                           f"Only its owner may create files there: chmod 0750 {folder}")
    if st.st_uid not in (0, d.st_uid):
        raise RuntimeError(f"OdyCAT: {path} belongs to uid {st.st_uid}, not to the "
                           f"engine (root, or uid {d.st_uid} who owns {folder}); "
                           f"refusing to use it")


def open_shm(path: str, flags: int) -> int:
    """os.open() for an engine region (no symlinks), checked with
    _check_region, with a clear error when access is denied."""
    try:
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC)
    except PermissionError:
        raise RuntimeError(f"OdyCAT: no permission to open {path}. {SHM_GROUP_HINT}") from None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise RuntimeError(f"OdyCAT: {path} is a symbolic link; refusing to use it") from None
        raise
    try:
        _check_region(fd, path)
    except BaseException:
        os.close(fd)
        raise
    return fd


def detect_bus() -> str:
    """Auto-detect the running OdyCAT bus by scanning <run dir>/*_config."""
    folder = run_dir()
    matches = glob.glob(os.path.join(folder, "*_config"))
    if len(matches) == 1:
        # e.g. "/run/odycat/bus0_config" → "bus0"
        basename = os.path.basename(matches[0])
        return basename.removesuffix("_config")
    if len(matches) == 0:
        raise RuntimeError(
            f"OdyCAT: no running engine detected (no *_config in {folder}). "
            f"Start the C engine first, or pass bus= explicitly. An engine "
            f"started with ODYCAT_RUN_DIR needs the same ODYCAT_RUN_DIR here."
        )
    buses = [os.path.basename(m).removesuffix("_config") for m in matches]
    raise RuntimeError(
        f"OdyCAT: multiple engines detected ({', '.join(buses)}). "
        f"Pass bus= explicitly to select one."
    )


def _data_shm_path(bus: str) -> str:
    return os.path.join(run_dir(), f"{bus}_shared")


def _config_shm_path(bus: str) -> str:
    return os.path.join(run_dir(), f"{bus}_config")


def _trace_shm_path(bus: str) -> str:
    return os.path.join(run_dir(), f"{bus}_trace")


def read_config_region(bus: str) -> tuple[dict, dict]:
    """(engine information, parsed config) from the config region, read
    now (no cache)."""
    path = _config_shm_path(bus)
    fd = open_shm(path, os.O_RDONLY)
    try:
        mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
    finally:
        os.close(fd)
    try:
        info, raw = _shm.parse_config_region(mm, path)
    finally:
        mm.close()
    return info, json.loads(raw.decode("utf-8"))


def engine_info(bus: Optional[str] = None) -> dict:
    """Which engine runs and which config it loaded, from the config region:
    engine_version, engine_git, config_sha256 (of the file as loaded),
    engine_pid, support_end (YYYYMM or None), start_realtime_ns, instance_id
    (random, new at every start) and layout_version."""
    if bus is None:
        bus = detect_bus()
    return read_config_region(bus)[0]


def check_engine_running(buf, path: str, bus: str) -> None:
    """Refuse to attach to a region whose engine is gone: it was killed or
    crashed (its process no longer exists), or it stopped running cycles.
    An engine that is still starting is fine.

    A script in a container (its own PID namespace) does not see the engine's
    process, but sees its cycles: an invisible pid with fresh cycles is a
    running engine. Only an invisible pid without cycles is refused."""
    pid = engine_info(bus)["engine_pid"]
    down = _shm.engine_down(buf)
    if pid and down and not _shm.pid_alive(pid):
        raise RuntimeError(
            f"OdyCAT: {path} was left by an engine that is no longer running "
            f"(pid {pid}). Start the engine again; from a container, attach once "
            f"the engine runs the bus.")
    if down and down != _shm.ENGINE_STARTING:
        raise RuntimeError(f"OdyCAT: the engine of bus '{bus}' {down}.")


def read_config(bus: Optional[str] = None) -> dict:
    """Read and parse the config JSON from the engine's config region.

    Results are cached per bus until the engine is restarted.
    """
    if bus is None:
        bus = detect_bus()

    path = _config_shm_path(bus)
    try:
        ino = os.stat(path).st_ino
    except FileNotFoundError:
        raise RuntimeError(
            f"OdyCAT engine for bus '{bus}' not running "
            f"(no config region at {path})"
        ) from None

    cached = _config_cache.get(bus)
    if cached is not None and cached[0] == ino:
        return cached[1]

    config = read_config_region(bus)[1]
    _config_cache[bus] = (ino, config)
    return config


def _as_int(v, default: int = 0) -> int:
    if v is None:
        return default
    return int(v, 0) if isinstance(v, str) else int(v)


def slave_address(slave: dict) -> tuple[int, int]:
    """(alias, position) of a config slave entry.

    Same semantics as IgH / `ethercat slaves`: alias 0 means position is the
    absolute ring position; otherwise position is the offset from the slave
    carrying that station alias (and defaults to 0).
    """
    return _as_int(slave.get("alias")), _as_int(slave.get("position"))


def _describe(slave: dict) -> str:
    alias, pos = slave_address(slave)
    return f"{alias}:{pos} ({slave.get('label', '?')})"


def find_slave(cfg: dict, position: Optional[int] = None, *,
               alias: int = 0, label: Optional[str] = None) -> dict:
    """Find a slave entry by label, or by (alias, position)."""
    slaves = cfg.get("slaves", [])
    if label is not None:
        matches = [s for s in slaves if s.get("label") == label]
        what = f"label '{label}'"
    else:
        if position is None:
            position = 0 if alias else None
        if position is None:
            raise ValueError("OdyCAT: pass a position, an alias, or a label")
        matches = [s for s in slaves if slave_address(s) == (alias, position)]
        what = f"address {alias}:{position}"
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(f"OdyCAT: {what} matches {len(matches)} slaves in config")
    raise KeyError(
        f"OdyCAT: no slave with {what} in config "
        f"(available: {', '.join(_describe(s) for s in slaves)})"
    )


def slave_config(position: Optional[int] = None, bus: Optional[str] = None, *,
                 alias: int = 0, label: Optional[str] = None) -> dict:
    """Look up a slave's config block by label, or by (alias, position).

    Returns the slave dict from config.json (with txpdo, rxpdo, etc.).
    Raises KeyError if no slave matches.
    """
    return find_slave(read_config(bus), position, alias=alias, label=label)


def _entry_is_padding(ent: dict) -> bool:
    idx = ent.get("index")
    if idx is None:
        return False
    if isinstance(idx, str):
        try:
            return int(idx, 0) == 0
        except ValueError:
            return False
    return int(idx) == 0


# The value types of PDO entries and SDOs (the config's "type"), with the
# struct code Python decodes them with. The engine has the same table.
TYPES = {
    "bool": "?", "u8": "B", "i8": "b", "u16": "H", "i16": "h",
    "u32": "I", "i32": "i", "u64": "Q", "i64": "q", "f32": "f", "f64": "d",
}
TYPE_BITS = {name: struct.calcsize(code) * 8 for name, code in TYPES.items()}


def _entry_bits(ent: dict) -> int:
    """Bits of an entry: its "type", or "bits" for a bit field or a gap. A
    value of a whole type's size is given by its type, not by "bits"."""
    typ = ent.get("type")
    if typ is not None:
        if typ not in TYPE_BITS:
            raise ValueError(f"Unknown entry type {typ!r}: use one of {', '.join(TYPES)}")
        return TYPE_BITS[typ]
    bits = ent.get("bits")
    if bits is None:
        raise ValueError(f"Entry requires 'type' or 'bits': {ent}")
    bits = int(bits)
    if bits in (8, 16, 32, 64) and not _entry_is_padding(ent):
        raise ValueError(f"Entry {ent.get('label') or ent.get('index')} has \"bits\": {bits}: "
                         f"give a value of that size its type (\"type\": \"u{bits}\", "
                         f"\"i{bits}\"...)")
    return bits


def _pdo_chunks(pdo: dict):
    """Yield (entries, bits) for each byte-aligned run of PDO entries.

    Mirrors the engine: a run starts on a byte boundary and ends at the next
    one, so sub-byte entries (e.g. 1-bit channels) are grouped into whole
    bytes. Runs made only of padding entries are not mirrored into SHM.
    """
    run: list[dict] = []
    bits = 0
    for ent in pdo.get("entries", []):
        run.append(ent)
        bits += _entry_bits(ent)
        if bits % 8 == 0:
            if not all(_entry_is_padding(e) for e in run):
                yield run, bits
            run, bits = [], 0
    if bits:
        raise ValueError(f"PDO entries do not end on a byte boundary ({bits} trailing bits)")


def pdo_format_from_entries(pdo: dict) -> str:
    """Build a Python struct format string from PDO entries.

    A run holding one typed entry uses its type's code. Runs of packed
    sub-byte entries are exposed as raw bytes ("B" per byte), so device
    classes decode the individual bits.
    """
    codes: list[str] = []
    for run, bits in _pdo_chunks(pdo):
        if len(run) == 1 and run[0].get("type") is not None:
            codes.append(TYPES[run[0]["type"]])
        else:
            codes.append("B" * (bits // 8))
    return "=" + "".join(codes) if codes else ""


def pdo_size_from_entries(pdo: dict) -> int:
    """Compute the SHM byte size of a PDO block (same rules as the engine)."""
    return sum(bits for _, bits in _pdo_chunks(pdo)) // 8


# Config key of each direction: one PDO or a list of PDOs.
_DIRECTION_KEYS = {"in": "txpdo", "out": "rxpdo"}


def slave_pdos(slave: dict, direction: str) -> list[tuple[str, dict]]:
    """PDOs of one direction ("in" or "out") as (name, pdo), in wire order.

    `txpdo`/`rxpdo` hold one PDO object or a list of them. PDOs without
    entries are left out, as the engine does not assign them. Names are
    "txpdo", "txpdo[2]", ...
    """
    key = _DIRECTION_KEYS[direction]
    blk = slave.get(key) or {}
    if isinstance(blk, list):
        pdos = [(f"{key}[{i}]", p) for i, p in enumerate(blk)]
    else:
        pdos = [(key, blk)]
    return [(name, p) for name, p in pdos if p.get("entries")]


def direction_pdo(slave: dict, direction: str) -> dict:
    """All entries of one direction as a single PDO-like block.

    The engine packs a direction as one run of entries across its PDOs, so
    byte alignment, shm size and struct format are those of this block.
    """
    return {"entries": [e for _, p in slave_pdos(slave, direction)
                        for e in p["entries"]]}


def slave_pdo_sizes(slave: dict) -> tuple[int, int]:
    """(input bytes, output bytes) a slave occupies in shm."""
    return (pdo_size_from_entries(direction_pdo(slave, "in")),
            pdo_size_from_entries(direction_pdo(slave, "out")))


def direction_format(slave: dict, direction: str) -> str:
    """struct format of one direction's shm bytes."""
    return pdo_format_from_entries(direction_pdo(slave, direction))


def _align_up_8(v: int) -> int:
    return (v + 7) & ~7


def resolve_slave_shm_offset(cfg: dict, position: Optional[int] = None, *,
                             alias: int = 0, label: Optional[str] = None) -> int:
    """Byte offset of a slave block in the data shm, as the engine places it:
    - header at [0 .. _shm.DATA_HEADER_SIZE)
    - first slave starts at _shm.DATA_HEADER_SIZE
    - each block follows the previous one, aligned to 8 bytes
    """
    target = find_slave(cfg, position, alias=alias, label=label)
    next_offset = _shm.DATA_HEADER_SIZE
    for sl in cfg.get("slaves", []):
        off = _align_up_8(next_offset)
        tx_size, rx_size = slave_pdo_sizes(sl)
        end = off + tx_size + rx_size
        next_offset = _align_up_8(end)

        if sl is target:
            return off
    raise AssertionError("unreachable")


def pdo_entry_layout(pdo: dict, bit_base: int = 0) -> tuple[list[dict], int]:
    """Lay out the entries of one PDO block as they sit in SHM.

    Mirrors the engine's byte-aligned runs: runs made only of padding are
    not mirrored into SHM, so they do not advance the bit cursor. Padding
    entries are omitted. Returns (fields, bits used); each field holds
    label, index, subindex, bits, type and bit_offset (from bit_base).
    """
    fields: list[dict] = []
    run: list[dict] = []
    run_bits = 0
    cursor = bit_base
    for ent in pdo.get("entries", []):
        run.append(ent)
        run_bits += _entry_bits(ent)
        if run_bits % 8:
            continue
        if not all(_entry_is_padding(e) for e in run):
            for e in run:
                bits = _entry_bits(e)
                if not _entry_is_padding(e):
                    index = _as_int(e.get("index"))
                    sub = _as_int(e.get("subindex"))
                    fields.append({
                        "label": e.get("label") or f"0x{index:04X}:{sub}",
                        "index": f"0x{index:04X}",
                        "subindex": sub,
                        "bits": bits,
                        "type": e.get("type"),
                        "bit_offset": cursor,
                    })
                cursor += bits
        run, run_bits = [], 0
    return fields, cursor - bit_base


def slave_entry_layout(slave: dict) -> tuple[list[dict], list[dict]]:
    """(inputs, outputs) entry layout of a slave, bit offsets from its shm block.

    Each direction is laid out as one run across its PDOs, as in the engine;
    outputs start right after the inputs. Each field also names its PDO
    ("block": "txpdo", "txpdo[1]", "rxpdo", ...).
    """
    result = []
    bit = 0
    for direction in ("in", "out"):
        names = [name for name, p in slave_pdos(slave, direction)
                 for e in p["entries"] if not _entry_is_padding(e)]
        fields, n = pdo_entry_layout(direction_pdo(slave, direction), bit)
        for f, name in zip(fields, names):
            f["block"] = name
        result.append(fields)
        bit += n
    return result[0], result[1]


def _entry_struct(field: dict) -> Optional[struct.Struct]:
    """struct for a byte-aligned typed entry, else None."""
    typ = field.get("type")
    if typ not in TYPES or field["bit_offset"] % 8:
        return None
    return struct.Struct("<" + TYPES[typ])


def decode_entry(buf, field: dict, bit_base: int = 0):
    """Decode one entry from buf; its bit_offset is relative to bit_base.

    Byte-aligned typed entries decode as their type; other entries are
    unsigned bit fields (1-bit entries decode to bool).
    """
    bit = bit_base + field["bit_offset"]
    bits = field["bits"]
    st = _entry_struct(field)
    if st is not None and bit % 8 == 0:
        v = st.unpack_from(buf, bit // 8)[0]
        return v if not isinstance(v, bytes) else v.hex()
    first = bit // 8
    last = (bit + bits + 7) // 8
    raw = int.from_bytes(bytes(buf[first:last]), "little") >> (bit % 8)
    v = raw & ((1 << bits) - 1)
    return bool(v) if bits == 1 else v
