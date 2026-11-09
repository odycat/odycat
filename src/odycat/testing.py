# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""
A fake OdyCAT engine, for testing code that uses odycat without hardware,
IgH or root: device classes (also in vendor packages), machine logic,
event handlers.

Not covered by OdyCAT's compatibility promise: this module follows the
engine and the test tooling, and may change in any release.

The C engine exposes three shared-memory regions, files on tmpfs in its run
directory (odycat._config.run_dir()):

    <run dir>/<iface>_shared  256-byte header + slave blocks + output buffers + tables
    <run dir>/<iface>_config  256-byte header + the config JSON as loaded
    <run dir>/<iface>_trace   128-byte header + one slot per cycle + engine events

FakeEngine creates the same regions as ordinary files in a temp directory
and patches the path helpers the odycat modules use, so Bus, Device, Events
and the trace run their real code paths against it. The layout helpers here
mirror the engine (the headers, align_up_8, walk_pdo_chunks, the sync
groups and output buffers, the status table and the expected-WKC formula)
independently of the package's own code (odycat._shm), so tests compare the
package against a separate model of the engine.

    from odycat.testing import fake_engine, mini_config, word_slave

    with fake_engine(mini_config(word_slave("io", 0))) as eng:
        eng.publish(inputs={"io": bytes([0x01, 0x00, 0x00])})
        ...                      # Device(label="io") sees input bit 0 set
        eng.read_outputs("io")   # what the code under test wrote (sent next cycle)
"""

from __future__ import annotations

import hashlib
import importlib
import json
import mmap
import os
import struct
import tempfile
import threading
import time
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest import mock

__all__ = [
    "FakeEngine", "fake_engine", "mini_config", "device_slave", "word_slave", "bit_slave",
    "TYPE_BITS", "SHM_HEADER_SIZE", "align_up_8", "entry_bits", "pdo_chunks",
    "mirrored_chunks", "pdo_bytes", "direction_entries", "slave_bytes",
    "engine_offsets", "engine_shm_size", "engine_layout", "sync_groups", "expected_wkc",
]

# ---------------------------------------------------------------------------
#  Constants mirrored from the engine (shm_abi.h, layout.c, config.c)
# ---------------------------------------------------------------------------

SHM_HEADER_SIZE = 256
CONFIG_HEADER_SIZE = 256
SLAVE_STATUS_SIZE = 20
DATA_MAGIC = 0x4144434F           # "OCDA"
CONFIG_MAGIC = 0x4643434F         # "OCCF"
LAYOUT_MAJOR = 3
LAYOUT_MINOR = 0
OUT_BUFFERS = 3                # per slave with outputs
GROUP_MAX_MEMBERS = 32         # 2 bits each in a 64-bit group word
NO_GROUP = 0xFFFF
OUT_BUF_HEADER = 8             # epoch u32, reserved u32
OUT_ENTRY_FMT = struct.Struct("<IIHBBI")   # buf_offset, buf_stride, group, member, -, -
PAGE_SIZE = 4096
MAX_SLAVES = 512
MAX_PDOS_PER_DIR = 32
MAX_PDO_ENTRIES = 256          # per direction, over all its PDOs
MAX_SDOS_PER_SLAVE = 32

# al_state, online, operational, refreshed, ring_pos, drop_count,
# block_offset, in_bytes, out_bytes, al_code, reserved (odycat_slave_status_t)
STATUS_FMT = struct.Struct("<BBBBHHIHHHH")
# magic, major, minor, header_size, json_len, engine_version, engine_git,
# config_sha256, engine_pid, support_end, start_realtime_ns, instance_id
CONFIG_HEADER_FMT = struct.Struct("<IHHII32s48s32sIIQ16s")

# odycat_header_t: name -> offset (u32 unless in U64_FIELDS / U16_FIELDS)
HEADER_FIELDS = {
    "magic": 0,
    "layout_major": 4,
    "layout_minor": 6,
    "header_size": 8,
    "features": 12,
    "seq_counter": 16,
    "cycle_count": 24,
    "last_cycle_ns": 32,
    "bus_state": 40,
    "error_flags": 44,
    "work_counter": 48,
    "expected_wkc": 52,
    "wkc_wrong_streak": 56,
    "wkc_wrong_cycles": 64,
    "skipped_cycles": 72,
    "status_offset": 80,
    "status_count": 84,
    "slaves_responding": 88,
    "master_al_states": 92,
    "link_up": 96,
    "rt_flags": 100,
    "cycle_us": 104,
    "out_table_offset": 108,
    "group_count": 112,
    "groups_offset": 116,
    "busy_offset": 120,
    "out_group_skips": 128,
}
U64_FIELDS = {"cycle_count", "last_cycle_ns", "wkc_wrong_cycles", "skipped_cycles",
              "out_group_skips"}
U16_FIELDS = {"layout_major", "layout_minor"}

# Trace region (odycat_trace_header_t, odycat_slot_header_t, odycat_trace_event_t)
TRACE_MAGIC = 0x5254434F          # "OCTR", must match TRACE_MAGIC in the engine
TRACE_VERSION = 2
TRACE_HEADER_SIZE = 128
TRACE_SLOT_HEADER_SIZE = 64
TRACE_EVENTS = 1024
TRACE_HDR_FMT = struct.Struct("<10I")      # magic .. cycle_us; head, event_head at 40, 48
SLOT_HDR_FMT = struct.Struct("<QQQII")     # cycle, mono_ns, real_ns, wkc, error_flags
EVENT_FMT = struct.Struct("<QQQHHI")       # seq, cycle, real_ns, type, slave, arg
HISTORY_S_DEFAULT = 10.0

# pdo_type_bits() in the engine; odycat._config.TYPE_BITS must match.
TYPE_BITS = {
    "bool": 8, "u8": 8, "i8": 8, "u16": 16, "i16": 16, "u32": 32, "i32": 32,
    "f32": 32, "u64": 64, "i64": 64, "f64": 64,
}


def align_up_8(v: int) -> int:
    return (v + 7) & ~7


def align_up_64(v: int) -> int:
    return (v + 63) & ~63


# ---------------------------------------------------------------------------
#  The engine's layout rules, mirrored
# ---------------------------------------------------------------------------

def entry_bits(entry: dict) -> int:
    """Bits of one PDO entry, as the engine derives them."""
    typ = entry.get("type")
    if typ:
        if typ not in TYPE_BITS:
            raise ValueError(f"unsupported type {typ!r}")
        return TYPE_BITS[typ]
    return int(entry["bits"])


def entry_index(entry: dict) -> int:
    idx = entry.get("index")
    return 0 if idx is None else int(idx, 0) if isinstance(idx, str) else int(idx)


def pdo_chunks(entries: list[dict]) -> tuple[list[tuple[int, int, int]], int]:
    """walk_pdo_chunks(): ([(first, last, bits)], trailing_bits)."""
    chunks, start, bits = [], 0, 0
    for i, ent in enumerate(entries):
        bits += entry_bits(ent)
        if bits % 8 == 0:
            chunks.append((start, i, bits))
            start, bits = i + 1, 0
    return chunks, bits


def mirrored_chunks(entries: list[dict]) -> list[tuple[int, int, int]]:
    """Chunks the engine mirrors into shm: byte-aligned runs holding real data."""
    chunks, trailing = pdo_chunks(entries)
    if trailing:
        raise ValueError(f"{trailing} trailing bits: block is not byte aligned")
    return [(first, last, bits) for first, last, bits in chunks
            if any(entry_index(e) for e in entries[first:last + 1])]


def pdo_bytes(entries: list[dict]) -> int:
    """Bytes a PDO block occupies in shm (chunks of gaps are not mirrored)."""
    return sum(bits // 8 for _, _, bits in mirrored_chunks(entries))


def direction_entries(slave: dict, key: str) -> list[dict]:
    """parse_pdo_dir(): all entries of one direction in wire order.

    `key` ("txpdo" or "rxpdo") holds one PDO or a list. The engine walks
    byte-aligned chunks over this whole list.
    """
    blk = slave.get(key, {})
    pdos = list(blk) if isinstance(blk, list) else [blk]
    return [e for p in pdos for e in p.get("entries", [])]


def slave_bytes(slave: dict) -> tuple[int, int]:
    """(tx_bytes, rx_bytes) of a config slave."""
    return (pdo_bytes(direction_entries(slave, "txpdo")),
            pdo_bytes(direction_entries(slave, "rxpdo")))


def engine_offsets(cfg: dict) -> list[int]:
    """shm_offset of every slave, in config order, exactly as the engine computes it."""
    offsets, nxt = [], SHM_HEADER_SIZE
    for sl in cfg.get("slaves", []):
        off = align_up_8(nxt)
        offsets.append(off)
        tx, rx = slave_bytes(sl)
        nxt = align_up_8(off + tx + rx)
    return offsets


def sync_groups(cfg: dict) -> list[list[int]]:
    """assign_sync_groups(): the config indices of the slaves of each sync
    group. Every slave with outputs is in one: its "sync_group", or a group
    of its own; groups in order of their first slave."""
    groups: list[list[int]] = []
    by_name: dict[str, int] = {}
    for i, sl in enumerate(cfg.get("slaves", [])):
        if slave_bytes(sl)[1] == 0:
            continue
        name = sl.get("sync_group")
        g = by_name.get(name) if name else None
        if g is None:
            g = len(groups)
            groups.append([])
            if name:
                by_name[name] = g
        if len(groups[g]) == GROUP_MAX_MEMBERS:
            raise ValueError(f"sync_group {name!r} has more than {GROUP_MAX_MEMBERS} slaves")
        groups[g].append(i)
    return groups


def engine_layout(cfg: dict) -> dict:
    """The data region as layout_place_blocks() lays it out: slave blocks,
    output buffers (per slave: offset of buffer 0, stride, group, member),
    the pub and busy words, the output table and the status table."""
    slaves = cfg.get("slaves", [])
    size = SHM_HEADER_SIZE
    for sl, off in zip(slaves, engine_offsets(cfg)):
        tx, rx = slave_bytes(sl)
        size = max(size, off + tx + rx)
    blocks_end = align_up_8(size)
    groups = sync_groups(cfg)
    where = {i: (g, m) for g, members in enumerate(groups) for m, i in enumerate(members)}
    outputs, off = [], align_up_64(blocks_end)
    for i, sl in enumerate(slaves):
        if i not in where:
            outputs.append(None)
            continue
        stride = align_up_64(OUT_BUF_HEADER + slave_bytes(sl)[1])
        outputs.append({"buf_offset": off, "stride": stride,
                        "group": where[i][0], "member": where[i][1]})
        off += OUT_BUFFERS * stride
    words = 8 * len(groups)
    groups_offset = align_up_64(off)
    busy_offset = align_up_64(groups_offset + words)
    out_table_offset = align_up_64(busy_offset + words)
    status_offset = out_table_offset + OUT_ENTRY_FMT.size * len(slaves)
    total = status_offset + SLAVE_STATUS_SIZE * len(slaves)
    return {"blocks_end": blocks_end, "outputs": outputs, "groups": groups,
            "groups_offset": groups_offset, "busy_offset": busy_offset,
            "out_table_offset": out_table_offset, "status_offset": status_offset,
            "total_size": (total + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)}


def engine_shm_size(cfg: dict) -> tuple[int, int]:
    """(status_table_offset, total_data_size) as the engine computes them."""
    lay = engine_layout(cfg)
    return lay["status_offset"], lay["total_size"]


def expected_wkc(cfg: dict) -> int:
    """Engine formula: one FMMU per direction per slave with data chunks."""
    n_in = n_out = 0
    for sl in cfg.get("slaves", []):
        tx, rx = slave_bytes(sl)
        n_in += tx > 0
        n_out += rx > 0
    return n_out * 2 + n_in if n_in and n_out else n_out + n_in


# ---------------------------------------------------------------------------
#  Small configs used by the round-trip tests
# ---------------------------------------------------------------------------

def device_slave(device: str, label: str, position: int = 0, alias: int = 0) -> dict:
    """A config entry for a device class ("vendor.Model"), filled in from its
    PROFILE as `odycat-config expand` does."""
    from odycat._profiles import expand_slave
    return expand_slave({"device": device, "position": position, "alias": alias,
                         "label": label})


def word_slave(label: str, position: int, alias: int = 0) -> dict:
    """One 16-bit input + 8-bit input, one 16-bit output (all byte aligned)."""
    return {
        "alias": alias, "position": position, "name": "MINI-WORD", "label": label,
        "vendor_id": "0x00001111", "product_code": "0x00002222",
        "sdos": [],
        "txpdo": {"index": "0x1A00", "entries": [
            {"index": "0x6000", "subindex": 1, "type": "u16", "label": "inputs"},
            {"index": "0x6000", "subindex": 2, "type": "i8", "label": "status_byte"},
        ]},
        "rxpdo": {"index": "0x1600", "entries": [
            {"index": "0x7000", "subindex": 1, "type": "u16", "label": "outputs"},
        ]},
    }


def bit_slave(label: str, position: int, alias: int = 0) -> dict:
    """Sub-byte entries: 1-bit channels LSB-first, a bit run straddling a byte,
    a byte-aligned word after them, a gap-only run and a second bit run."""
    tx = [{"index": "0x6000", "subindex": i, "bits": 1, "label": f"din{i - 1}"}
          for i in range(1, 5)]
    tx += [
        {"index": "0x6000", "subindex": 9, "type": "i8", "label": "byte_in"},
        {"index": "0x6000", "subindex": 10, "bits": 4, "label": "nibble_in"},
        {"index": "0x6001", "subindex": 1, "type": "u16", "label": "word_in"},
    ]
    rx = [{"index": "0x7000", "subindex": i, "bits": 1, "label": f"dout{i - 1}"}
          for i in range(1, 5)]
    rx += [{"index": "0x7000", "subindex": 9, "bits": 4, "label": "preset"},
           {"index": "0x0000", "subindex": 0, "bits": 8, "label": "gap"}]
    rx += [{"index": "0x7001", "subindex": i, "bits": 1, "label": f"dout{i + 3}"}
           for i in range(1, 9)]
    return {
        "alias": alias, "position": position, "name": "MINI-BIT", "label": label,
        "vendor_id": "0x00001111", "product_code": "0x00003333",
        "sdos": [],
        "txpdo": {"index": "0x1A00", "entries": tx},
        "rxpdo": {"index": "0x1600", "entries": rx},
    }


def mini_config(*slaves: dict, bus: str = "fake0") -> dict:
    """A config for the fake engine; defaults to a word slave and a bit slave."""
    if not slaves:
        slaves = (word_slave("word_io", 0), bit_slave("bit_io", 1, alias=3))
    return {"bus": bus, "cycle_us": 1000, "slaves": list(slaves)}


# ---------------------------------------------------------------------------
#  Fake engine: the shm regions as ordinary files
# ---------------------------------------------------------------------------

_PATCHED = ("odycat._config", "odycat.bus", "odycat.device", "odycat.monitor",
            "odycat.trace")


class FakeEngine:
    """Stand-in for the odycat engine: owns the regions, the header and the seqlock.

    Publish input bytes, read back what a Device wrote, poke the header to
    fake stalls or WKC mismatches. See the fake_engine() context manager.
    """

    def __init__(self, cfg: dict, bus: str = "fake0"):
        self.cfg = cfg
        self.bus = bus
        self._tmp = tempfile.TemporaryDirectory(prefix="odycat-test-")
        self.root = Path(self._tmp.name)
        self.config_path = self.root / f"{bus}_config"
        self.data_path = self.root / f"{bus}_shared"
        self.trace_path = self.root / f"{bus}_trace"
        self._trace_mm = None
        self._event_seq = 0
        self._build_layout()
        self._write_regions()

    def _build_layout(self) -> None:
        self.layout = engine_layout(self.cfg)
        self.status_offset = self.layout["status_offset"]
        self.total_size = self.layout["total_size"]
        self.blocks_end = self.layout["blocks_end"]
        self.slaves = []
        for i, (sl, off) in enumerate(zip(self.cfg.get("slaves", []), engine_offsets(self.cfg))):
            tx, rx = slave_bytes(sl)
            self.slaves.append({"label": sl.get("label"), "index": i, "offset": off,
                                "tx": tx, "rx": rx, "out": self.layout["outputs"][i]})
        self._by_label = {s["label"]: s for s in self.slaves}

    # -- regions ------------------------------------------------------------

    def _write_regions(self) -> None:
        text = json.dumps(self.cfg).encode("utf-8")
        self.instance_id = os.urandom(16)
        self.config_header = CONFIG_HEADER_FMT.pack(
            CONFIG_MAGIC, LAYOUT_MAJOR, LAYOUT_MINOR, CONFIG_HEADER_SIZE, len(text),
            b"0.0.0-fake", b"fake-engine", hashlib.sha256(text).digest(), os.getpid(),
            0, time.time_ns(), self.instance_id)
        self.config_path.write_bytes(self.config_header.ljust(CONFIG_HEADER_SIZE, b"\0") + text)
        self.data_path.write_bytes(bytes(self.total_size))
        os.chmod(self.config_path, 0o640)        # the engine's modes: odycat._config
        os.chmod(self.data_path, 0o660)          # refuses regions open to others
        fd = os.open(str(self.data_path), os.O_RDWR)
        try:
            self._mm = mmap.mmap(fd, 0)
        finally:
            os.close(fd)
        self.set(layout_major=LAYOUT_MAJOR, layout_minor=LAYOUT_MINOR,
                 header_size=SHM_HEADER_SIZE, cycle_us=self.cfg.get("cycle_us", 1000),
                 expected_wkc=expected_wkc(self.cfg), bus_state=8, cycle_count=0,
                 error_flags=0, status_offset=self.status_offset,
                 status_count=len(self.slaves), slaves_responding=len(self.slaves),
                 master_al_states=8, link_up=1, rt_flags=3,
                 last_cycle_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC),
                 out_table_offset=self.layout["out_table_offset"],
                 group_count=len(self.layout["groups"]),
                 groups_offset=self.layout["groups_offset"],
                 busy_offset=self.layout["busy_offset"])
        # A running engine stamps every cycle: keep last_cycle_ns fresh, so
        # the package does not take the fake engine for a stopped one. Stops
        # as soon as a test sets last_cycle_ns or bus_state itself.
        self._alive = threading.Event()
        self._alive.set()
        self._keepalive = threading.Thread(target=self._stamp_cycles, daemon=True)
        self._keepalive.start()
        for i, s in enumerate(self.slaves):          # the output table
            o = s["out"]
            OUT_ENTRY_FMT.pack_into(
                self._mm, self.layout["out_table_offset"] + OUT_ENTRY_FMT.size * i,
                *((o["buf_offset"], o["stride"], o["group"], o["member"], 0, 0) if o
                  else (0, 0, NO_GROUP, 0, 0, 0)))
        for i in range(len(self.slaves)):           # publish_bus_info(): ring_pos, blocks
            self.status(i, 0, online=0, operational=0, refreshed=0)
        self._write_trace()
        if self._trace_mm is not None:
            self.set(features=1)                    # ODYCAT_FEAT_TRACE
        self.set(magic=DATA_MAGIC)                  # last, as the engine does

    def _write_trace(self) -> None:
        """trace_create(): header, history_s of slots, the event ring."""
        self._trace_mm = None
        self._event_seq = 0
        history_s = self.cfg.get("history_s", HISTORY_S_DEFAULT)
        if history_s <= 0:
            return
        bitmap_bytes = (len(self.slaves) + 63) // 64 * 8
        self.not_op_offset = TRACE_SLOT_HEADER_SIZE + self.blocks_end - SHM_HEADER_SIZE
        self.slot_size = align_up_8(self.not_op_offset + bitmap_bytes)
        cycle_us = self.cfg.get("cycle_us", 1000)
        self.slot_count = max(2, int(history_s * 1e6 / cycle_us + 0.5))
        self.slots_offset = TRACE_HEADER_SIZE
        self.events_offset = self.slots_offset + self.slot_count * self.slot_size
        size = self.events_offset + TRACE_EVENTS * EVENT_FMT.size
        self.trace_path.write_bytes(bytes(size))
        os.chmod(self.trace_path, 0o640)
        fd = os.open(str(self.trace_path), os.O_RDWR)
        try:
            self._trace_mm = mmap.mmap(fd, 0)
        finally:
            os.close(fd)
        TRACE_HDR_FMT.pack_into(self._trace_mm, 0, TRACE_MAGIC, TRACE_VERSION,
                                self.slot_size, self.slot_count, self.slots_offset,
                                self.blocks_end, self.not_op_offset, TRACE_EVENTS,
                                self.events_offset, cycle_us)
        struct.pack_into("<II", self._trace_mm, 64, TRACE_HEADER_SIZE, SHM_HEADER_SIZE)

    def _trace_slot(self, cycle: int) -> None:
        """trace_slot(): the 64-byte slot header, the data region's blocks
        from SHM_HEADER_SIZE on, and the not-in-OP bitmap (set for a refreshed
        status entry that is not OP)."""
        if self._trace_mm is None:
            return
        off = self.slots_offset + (cycle - 1) % self.slot_count * self.slot_size
        tm = self._trace_mm
        struct.pack_into("<Q", tm, off, 0)
        h = TRACE_SLOT_HEADER_SIZE
        tm[off + h: off + self.not_op_offset] = self._mm[SHM_HEADER_SIZE: self.blocks_end]
        bitmap = bytearray((len(self.slaves) + 63) // 64 * 8)
        for i in range(len(self.slaves)):
            al, online, op, refreshed = STATUS_FMT.unpack_from(
                self._mm, self.status_offset + SLAVE_STATUS_SIZE * i)[:4]
            if refreshed and not (online and op):
                bitmap[i // 8] |= 1 << (i % 8)
        tm[off + self.not_op_offset: off + self.not_op_offset + len(bitmap)] = bitmap
        SLOT_HDR_FMT.pack_into(tm, off, 0, self.get("last_cycle_ns"), time.time_ns(),
                               self.get("work_counter"), self.get("error_flags"))
        struct.pack_into("<Q", tm, off, cycle)
        struct.pack_into("<Q", tm, 40, cycle)

    def skip_cycles(self, n: int) -> None:
        """The engine was late and skipped n cycles: the header counter and a
        CYCLES_SKIPPED event, as the RT loop records them."""
        skipped = self.get("skipped_cycles") + n
        self.set(skipped_cycles=skipped)
        if self._trace_mm is not None:
            struct.pack_into("<Q", self._trace_mm, 56, skipped)
        self.engine_event(9, arg=n, cycle=self.get("cycle_count") + 1)

    def engine_event(self, type_: int, slave: int | None = None, arg: int = 0,
                     cycle: int | None = None) -> None:
        """trace_event(): append one engine event."""
        self._event_seq += 1
        seq = self._event_seq
        off = self.events_offset + (seq - 1) % TRACE_EVENTS * EVENT_FMT.size
        EVENT_FMT.pack_into(self._trace_mm, off, 0,
                            self.get("cycle_count") if cycle is None else cycle,
                            time.time_ns(), type_, 0xFFFF if slave is None else slave, arg)
        struct.pack_into("<Q", self._trace_mm, off, seq)
        struct.pack_into("<Q", self._trace_mm, 48, seq)

    def _stamp_cycles(self) -> None:
        while self._alive.is_set():
            self._store_u64(HEADER_FIELDS["last_cycle_ns"],
                            time.clock_gettime_ns(time.CLOCK_MONOTONIC))
            time.sleep(0.02)

    def close(self) -> None:
        self._alive.clear()
        self._keepalive.join()
        if self._trace_mm is not None:
            self._trace_mm.close()
        self._mm.close()
        self._tmp.cleanup()

    def restart(self, cfg: dict | None = None) -> None:
        """Engine restart: all regions unlinked and recreated (new inodes)."""
        if cfg is not None:
            self.cfg = cfg
            self._build_layout()
        self._mm.close()
        if self._trace_mm is not None:
            self._trace_mm.close()
        for path in (self.config_path, self.data_path, self.trace_path):
            if path.exists():
                path.unlink()
        self._write_regions()

    @contextmanager
    def patched(self):
        """Point every odycat module's shm path helpers at this fake engine."""
        from odycat import _config as _cfg_mod
        import odycat.device as _dev_mod
        stack = ExitStack()
        try:
            for name in _PATCHED:
                mod = importlib.import_module(name)
                for attr, target in (("_data_shm_path", self.data_path),
                                     ("_config_shm_path", self.config_path),
                                     ("_trace_shm_path", self.trace_path)):
                    if hasattr(mod, attr):
                        stack.enter_context(mock.patch.object(
                            mod, attr, lambda iface, _t=target: str(_t)))
            _cfg_mod._config_cache.clear()
            yield self
        finally:
            stack.close()
            _cfg_mod._config_cache.clear()
            _dev_mod._staged_devices.clear()

    # -- header -------------------------------------------------------------

    @staticmethod
    def _fmt(field: str) -> str:
        return "<Q" if field in U64_FIELDS else "<H" if field in U16_FIELDS else "<I"

    def get(self, field: str) -> int:
        """A header field, by its name in odycat_header_t."""
        return struct.unpack_from(self._fmt(field), self._mm, HEADER_FIELDS[field])[0]

    def set(self, **fields: int) -> None:
        """Write header fields, by their names in odycat_header_t. Setting
        last_cycle_ns or bus_state stops the fake engine's own stamping of
        last_cycle_ns (it then stalls, stops or starts as the test says)."""
        if ("last_cycle_ns" in fields or "bus_state" in fields) and hasattr(self, "_alive"):
            self._stop_stamping()
        self._write(**fields)

    def _stop_stamping(self) -> None:
        if self._alive.is_set():
            self._alive.clear()
            self._keepalive.join()

    def _write(self, **fields: int) -> None:
        for name, value in fields.items():
            if name in U64_FIELDS:
                self._store_u64(HEADER_FIELDS[name], value)
            else:
                struct.pack_into(self._fmt(name), self._mm, HEADER_FIELDS[name], value)

    def _store_u64(self, offset: int, value: int) -> None:
        """One aligned 8-byte store, as the engine's: struct.pack_into does not
        write the 8 bytes at once, and a reader in another process can see a
        half-written value (0, or an old cycle time)."""
        view = memoryview(self._mm)[offset:offset + 8].cast("Q")
        view[0] = value
        view.release()

    def header(self) -> dict:
        return {name: self.get(name) for name in HEADER_FIELDS}

    # -- data ---------------------------------------------------------------

    def slave(self, label: str) -> dict:
        return self._by_label[label]

    def read_inputs(self, label: str) -> bytes:
        s = self.slave(label)
        return bytes(self._mm[s["offset"]: s["offset"] + s["tx"]])

    # -- outputs: buffers, sync groups ----------------------------------------

    def _word(self, kind: str, group: int) -> int:
        return struct.unpack_from("<Q", self._mm, self.layout[kind] + 8 * group)[0]

    def _set_word(self, kind: str, group: int, value: int) -> None:
        struct.pack_into("<Q", self._mm, self.layout[kind] + 8 * group, value)

    def _drop_count(self, index: int) -> int:
        return STATUS_FMT.unpack_from(self._mm, self.status_offset + SLAVE_STATUS_SIZE * index)[5]

    def _current_buffer(self, s: dict, idx: int) -> bytes | None:
        """The outputs in buffer idx of slave s, or None if the engine would
        send zeros for it (invalid index, or epoch older than drop_count)."""
        o = s["out"]
        if idx >= OUT_BUFFERS:
            return None
        off = o["buf_offset"] + idx * o["stride"]
        if struct.unpack_from("<I", self._mm, off)[0] != self._drop_count(s["index"]):
            return None
        return bytes(self._mm[off + OUT_BUF_HEADER: off + OUT_BUF_HEADER + s["rx"]])

    def published_index(self, label: str) -> int:
        """Index of the slave's published output buffer (its 2 bits of the
        group's pub word)."""
        o = self.slave(label)["out"]
        return (self._word("groups_offset", o["group"]) >> (2 * o["member"])) & 3

    def read_outputs(self, label: str) -> bytes:
        """What the code under test wrote: the outputs the engine sends from
        the next cycle on (zeros if the slave left OP since)."""
        s = self.slave(label)
        if s["out"] is None:
            return b""
        data = self._current_buffer(s, self.published_index(label))
        return bytes(s["rx"]) if data is None else data

    def read_sent(self, label: str) -> bytes:
        """The outputs sent in the last cycle (publish()): the second half of
        the slave's block."""
        s = self.slave(label)
        return bytes(self._mm[s["offset"] + s["tx"]: s["offset"] + s["tx"] + s["rx"]])

    def write_outputs(self, label: str, data: bytes) -> None:
        """Another writer replaces the slave's outputs whole and publishes
        them, as a script would (a free buffer, the current epoch, then the
        group word)."""
        s = self.slave(label)
        o = s["out"]
        if len(data) != s["rx"]:
            raise ValueError(f"{label}: {len(data)} B for a {s['rx']} B output block")
        g, shift = o["group"], 2 * o["member"]
        pub = self._word("groups_offset", g)
        busy = self._word("busy_offset", g)
        cur, eng = (pub >> shift) & 3, (busy >> shift) & 3
        w = next(i for i in range(OUT_BUFFERS) if i not in (cur, eng))
        off = o["buf_offset"] + w * o["stride"]
        self._mm[off + OUT_BUF_HEADER: off + OUT_BUF_HEADER + s["rx"]] = data
        struct.pack_into("<I", self._mm, off, self._drop_count(s["index"]))
        self._set_word("groups_offset", g, (pub & ~(3 << shift)) | (w << shift))

    def claim_group(self, group: int) -> int:
        """The engine's first step for a group each cycle: busy := pub."""
        word = self._word("groups_offset", group)
        self._set_word("busy_offset", group, word)
        return word

    def _send_outputs(self) -> None:
        """exchange_process_data(), outputs half: each group's published
        buffers (or zeros, if stale) into the slaves' "as sent" halves."""
        for g, members in enumerate(self.layout["groups"]):
            word = self.claim_group(g)
            for m, i in enumerate(members):
                s = self.slaves[i]
                data = self._current_buffer(s, (word >> (2 * m)) & 3)
                start = s["offset"] + s["tx"]
                self._mm[start: start + s["rx"]] = bytes(s["rx"]) if data is None else data

    def status(self, index: int, al_state: int, online: int = 1,
               operational: int = 1, ring_pos: int | None = None,
               drop_count: int = 0, refreshed: int = 1, al_code: int = 0) -> None:
        """One status table entry, with the slave's block as the engine
        publishes it (offset, input bytes, output bytes). al_code is the
        AL status code (0x0134) of a slave not in OP."""
        blk = self.slaves[index]
        STATUS_FMT.pack_into(self._mm,
                             self.status_offset + SLAVE_STATUS_SIZE * index,
                             al_state, online, operational, refreshed,
                             index if ring_pos is None else ring_pos, drop_count,
                             blk["offset"], blk["tx"], blk["rx"], al_code, 0)

    def publish(self, inputs: dict[str, bytes] | None = None, *,
                bus_state: int = 8, work_counter: int | None = None,
                error_flags: int = 0, cycles: int = 1,
                last_cycle_ns: int | None = None,
                stalled_for_ns: int | None = None,
                status: list[tuple[int, int, int]] | None = None) -> None:
        """One RT cycle: seqlock window around the input copy and the outputs
        sent (each group's published buffers), then the header.

        'status' is a per-slave list of (al_state, online, operational), with
        the AL status code as an optional fourth item.
        """
        base = self.get("seq_counter")
        if base % 2:
            base += 1
        self.set(seq_counter=base + 1)                    # odd: inputs in flight
        for label, data in (inputs or {}).items():
            blk = self.slave(label)
            if len(data) != blk["tx"]:
                raise ValueError(f"{label}: {len(data)} B for a {blk['tx']} B input block")
            self._mm[blk["offset"]: blk["offset"] + blk["tx"]] = data
        self._send_outputs()
        self.set(seq_counter=base + 2)                    # even: snapshot consistent
        fields = {"bus_state": bus_state, "error_flags": error_flags,
                  "cycle_count": self.get("cycle_count") + cycles,
                  "work_counter": expected_wkc(self.cfg) if work_counter is None
                  else work_counter}
        if stalled_for_ns is not None:
            fields["last_cycle_ns"] = time.clock_gettime_ns(time.CLOCK_MONOTONIC) - stalled_for_ns
        else:
            fields["last_cycle_ns"] = last_cycle_ns if last_cycle_ns is not None \
                else time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        cycle0 = self.get("cycle_count")
        if fields["work_counter"] != self.get("expected_wkc"):    # rt_check_wkc()
            fields["wkc_wrong_cycles"] = self.get("wkc_wrong_cycles") + cycles
            fields["wkc_wrong_streak"] = self.get("wkc_wrong_streak") + cycles
        else:
            fields["wkc_wrong_streak"] = 0
        if stalled_for_ns is None and last_cycle_ns is None and bus_state == 8:
            self._write(**fields)              # a normal cycle: keeps running
        else:
            self.set(**fields)
        for i, entry in enumerate(status or ()):
            al_state, online, operational, *code = entry
            self.status(i, al_state, online, operational, al_code=code[0] if code else 0)
        for cycle in range(cycle0 + 1, cycle0 + cycles + 1):
            self._trace_slot(cycle)


@contextmanager
def fake_engine(cfg: dict | None = None, bus: str = "fake0"):
    """Fake engine with the shm path helpers patched; cleans up on exit."""
    eng = FakeEngine(cfg if cfg is not None else mini_config(bus=bus),
                     bus)
    try:
        with eng.patched():
            yield eng
    finally:
        eng.close()
