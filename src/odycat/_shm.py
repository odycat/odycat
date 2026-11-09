# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/_shm.py
"""
The binary layout of the engine's shared-memory regions: the mirror of
engine/shm_abi.h, the one place in the package that knows it. odycat.testing
keeps an independent copy on purpose, so the tests compare the two.

Layout version: a new field in reserved space raises the minor (older
readers ignore it); an incompatible change raises the major, and this
package refuses a region whose major differs from LAYOUT_MAJOR.
"""

import os
import platform
import struct
import time
from typing import Optional

LAYOUT_MAJOR = 3
LAYOUT_MINOR = 0

DATA_MAGIC = 0x4144434F           # "OCDA"
CONFIG_MAGIC = 0x4643434F         # "OCCF"
TRACE_MAGIC = 0x5254434F          # "OCTR"
TRACE_VERSION = 2

DATA_HEADER_SIZE = 256            # the first slave block starts here
CONFIG_HEADER_SIZE = 256          # the config JSON starts here
TRACE_HEADER_SIZE = 128
TRACE_SLOT_HEADER_SIZE = 64

# ---------------------------------------------------------------------------
#  Data region header (odycat_header_t)
# ---------------------------------------------------------------------------

OFF_MAGIC = 0                     # u32
OFF_LAYOUT_MAJOR = 4              # u16
OFF_LAYOUT_MINOR = 6              # u16
OFF_HEADER_SIZE = 8               # u32
OFF_FEATURES = 12                 # u32
OFF_SEQ = 16                      # u32, inputs seqlock
OFF_CYCLE_COUNT = 24              # u64
OFF_LAST_CYCLE_NS = 32            # u64
OFF_BUS_STATE = 40                # u32
OFF_ERROR_FLAGS = 44              # u32
OFF_WORK_COUNTER = 48             # u32
OFF_EXPECTED_WKC = 52             # u32
OFF_WKC_STREAK = 56               # u32
OFF_WKC_WRONG = 64                # u64
OFF_SKIPPED = 72                  # u64
OFF_STATUS_OFFSET = 80            # u32
OFF_STATUS_COUNT = 84             # u32
OFF_SLAVES_RESPONDING = 88        # u32
OFF_AL_STATES = 92                # u32
OFF_LINK_UP = 96                  # u32
OFF_RT_FLAGS = 100                # u32
OFF_CYCLE_US = 104                # u32
OFF_OUT_TABLE = 108               # u32, output table (OUT_ENTRY per slave)
OFF_GROUP_COUNT = 112             # u32
OFF_GROUPS = 116                  # u32, pub words: u64 per sync group
OFF_BUSY = 120                    # u32, busy words: u64 per sync group (engine)
OFF_OUT_GROUP_SKIPS = 128         # u64

FEAT_TRACE = 1 << 0
ERR_WKC_MISMATCH = 1 << 0
ERR_SLAVE_LOST = 1 << 1
RT_SCHED_FIFO = 1 << 0
RT_MEMLOCK = 1 << 1

U16 = struct.Struct("<H")
U32 = struct.Struct("<I")
U64 = struct.Struct("<Q")

# ---------------------------------------------------------------------------
#  Output buffers and sync groups (odycat_out_entry_t, odycat_out_buf_header_t)
# ---------------------------------------------------------------------------

OUT_BUFFERS = 3                   # per slave with outputs
GROUP_MAX_MEMBERS = 32            # 2 bits each in a 64-bit group word
NO_GROUP = 0xFFFF                 # output table: slave without outputs
OUT_BUF_HEADER = 8                # epoch u32, reserved u32; then the outputs
# buf_offset, buf_stride, group, member, reserved, reserved
OUT_ENTRY = struct.Struct("<IIHBBI")

# Stores from one CPU become visible to the others in program order on
# x86-64, so a plain store of the group word publishes the buffer written
# before it. Other architectures reorder stores and need a release store,
# which Python cannot issue: odycat._atomic (C) does it.
STORES_ORDERED = platform.machine().lower() in ("x86_64", "amd64")
try:
    from odycat import _atomic
except ImportError:          # built without it (no compiler): fine on x86-64
    _atomic = None

# ---------------------------------------------------------------------------
#  Slave status table entry (odycat_slave_status_t)
# ---------------------------------------------------------------------------

# al_state, online, operational, refreshed, ring_pos, drop_count,
# block_offset, in_bytes, out_bytes, al_code, reserved
STATUS = struct.Struct("<BBBBHHIHHHH")
STATUS_DROPS = 5                  # index of drop_count in STATUS
STATUS_AL_CODE = 9                # index of al_code: AL status code while not in OP

# ---------------------------------------------------------------------------
#  Config region header (odycat_config_header_t)
# ---------------------------------------------------------------------------

# magic, major, minor, header_size, json_len, engine_version, engine_git,
# config_sha256, engine_pid, support_end, start_realtime_ns, instance_id
CONFIG_HEADER = struct.Struct("<IHHII32s48s32sIIQ16s")

# ---------------------------------------------------------------------------
#  Trace region (odycat_trace_header_t, odycat_slot_header_t, events)
# ---------------------------------------------------------------------------

# magic, version, slot_size, slot_count, slots_offset, blocks_end,
# not_op_offset, event_count, events_offset, cycle_us
TRACE_HDR = struct.Struct("<10I")
OFF_TRACE_HEAD = 40               # u64
OFF_TRACE_EVENT_HEAD = 48         # u64
OFF_TRACE_SKIPPED = 56            # u64
OFF_TRACE_HEADER_SIZE = 64        # u32
OFF_TRACE_BLOCKS_START = 68       # u32
SLOT_HDR = struct.Struct("<QQQII")        # cycle, mono_ns, real_ns, wkc, error_flags
EVENT = struct.Struct("<QQQHHI")          # seq, cycle, real_ns, type, slave, arg


def read_u64(buf, offset: int) -> int:
    """A u64 the engine stores outside any lock: read until two reads agree
    (a torn read cannot repeat)."""
    a = U64.unpack_from(buf, offset)[0]
    while True:
        b = U64.unpack_from(buf, offset)[0]
        if a == b:
            return a
        a = b


def load_word(buf, offset: int) -> int:
    """A 64-bit group word, read with one aligned 8-byte acquire load."""
    if _atomic is not None:
        return _atomic.load_acquire_u64(buf, offset)
    return memoryview(buf)[offset:offset + 8].cast("Q")[0]


def store_word(buf, offset: int, value: int) -> None:
    """Publish a group word with one aligned 8-byte release store. The one
    place in the package that publishes to the engine. Without
    odycat._atomic, a typed memoryview store (one instruction; struct packs
    byte by byte), which is a release store only where STORES_ORDERED."""
    if _atomic is not None:
        _atomic.store_release_u64(buf, offset, value)
        return
    if not STORES_ORDERED:
        raise RuntimeError(
            f"OdyCAT: writing outputs on {platform.machine()} needs the odycat._atomic "
            f"extension, which this installation was built without (no C compiler?). "
            f"Reinstall the package with a compiler available, e.g. "
            f"'apt install build-essential python3-dev' then 'pip install --force-reinstall "
            f"--no-binary odycat odycat'.")
    memoryview(buf)[offset:offset + 8].cast("Q")[0] = value


def _check_version(what: str, path: str, magic: int, expected_magic: int,
                   major: int) -> None:
    if magic == 0:
        raise RuntimeError(f"OdyCAT: {path} is not initialised yet (engine starting?)")
    if magic != expected_magic:
        raise RuntimeError(f"OdyCAT: {path} is not an OdyCAT {what} region of this "
                           f"generation (magic 0x{magic:08X}): the engine is older or "
                           f"newer than this package. Install the engine and the "
                           f"package from the same version.")
    if major != LAYOUT_MAJOR:
        newer = "engine" if major > LAYOUT_MAJOR else "package"
        raise RuntimeError(f"OdyCAT: {path} has layout version {major}, this package "
                           f"reads version {LAYOUT_MAJOR}: the {newer} is newer. "
                           f"Install the engine and the package from the same version.")


# The engine is taken as stopped when its last cycle is older than this, or
# than ENGINE_STALE_CYCLES cycles if that is longer: it crashed, was killed
# or hangs (a clean stop sets bus_state to 0 before removing the region).
ENGINE_STALE_MIN_NS = 200_000_000
ENGINE_STALE_CYCLES = 20
ENGINE_STARTING = "has not brought the bus to OP yet"


# last_cycle_ns (u64 at 32) and bus_state (u32 at 40) in one read: the
# per-access liveness check of Device (engine_down() explains a failure)
LIVENESS = struct.Struct("<QI")
assert OFF_BUS_STATE == OFF_LAST_CYCLE_NS + 8


def stale_after_ns(cycle_us: int) -> int:
    """How long without a cycle before the engine is taken as stopped."""
    return max(ENGINE_STALE_MIN_NS, ENGINE_STALE_CYCLES * (cycle_us or 1000) * 1000)


def engine_down(buf) -> Optional[str]:
    """None while the engine runs the bus; otherwise why not, for a message:
    ENGINE_STARTING, "stopped", or how long it has not run a cycle."""
    state = U32.unpack_from(buf, OFF_BUS_STATE)[0]
    last = read_u64(buf, OFF_LAST_CYCLE_NS)
    if not last:
        return ENGINE_STARTING
    if state != 8:
        return "stopped"
    age = time.monotonic_ns() - last
    if age > stale_after_ns(U32.unpack_from(buf, OFF_CYCLE_US)[0]):
        return f"has not run a cycle for {age / 1e6:.0f} ms (stopped, killed or stalled)"
    return None


def pid_alive(pid: int) -> bool:
    """The process exists (it may belong to another user)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def check_data_header(buf, path: str) -> None:
    """Refuse a data region of another layout generation or major version."""
    _check_version("data", path, U32.unpack_from(buf, OFF_MAGIC)[0], DATA_MAGIC,
                   U16.unpack_from(buf, OFF_LAYOUT_MAJOR)[0])
    size = U32.unpack_from(buf, OFF_HEADER_SIZE)[0]
    if size != DATA_HEADER_SIZE:
        raise RuntimeError(f"OdyCAT: {path} has a {size}-byte header, this package "
                           f"expects {DATA_HEADER_SIZE}. Install the engine and the "
                           f"package from the same version.")


def parse_config_region(buf, path: str) -> tuple[dict, bytes]:
    """(engine information, config JSON bytes) from a config region."""
    if len(buf) < CONFIG_HEADER_SIZE:
        raise RuntimeError(f"OdyCAT: {path} is not initialised yet (engine starting?)")
    (magic, major, minor, header_size, json_len, version, git, sha, pid,
     support_end, start_ns, instance) = CONFIG_HEADER.unpack_from(buf, 0)
    _check_version("config", path, magic, CONFIG_MAGIC, major)
    info = {
        "engine_version": version.split(b"\0", 1)[0].decode("utf-8", "replace"),
        "engine_git": git.split(b"\0", 1)[0].decode("utf-8", "replace"),
        "config_sha256": sha.hex(),
        "engine_pid": pid,
        "support_end": support_end or None,     # YYYYMM
        "start_realtime_ns": start_ns,
        "instance_id": instance.hex(),
        "layout_version": f"{major}.{minor}",
    }
    return info, bytes(buf[header_size: header_size + json_len])
