# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/monitor.py
"""
BusMonitor — read-only, whole-bus view of a running OdyCAT engine.

Decodes every PDO entry of every configured slave (by entry label, with
bit-level entries resolved), plus the per-slave AL state table the engine
publishes after the last data block. For dashboards and tools, or on its
own::

    mon = BusMonitor()
    snap = mon.snapshot()
    snap["slaves"][0]["inputs"]   # [{"label": ..., "value": ...}, ...]

The monitor never writes to shared memory. It survives engine restarts:
when the data SHM disappears or is recreated it re-reads the config SHM
and rebuilds the layout.
"""

import mmap
import os
import time
from typing import Optional

from odycat import _shm
from odycat._al import AL_STATE_NAMES, al_status_text
from odycat._config import (
    detect_bus,
    slave_address,
    slave_entry_layout,
    decode_entry,
    open_shm,
    read_config_region,
    slave_pdo_sizes,
    _align_up_8,
    _data_shm_path,
)
from odycat.bus import _RT_MEMLOCK, _RT_SCHED_FIFO, _read_cycle_count, _seqlock_read


_STATUS = _shm.STATUS            # see engine/shm_abi.h odycat_slave_status_t


# The engine is considered stalled when its last cycle is older than this
_STALL_TIMEOUT_NS = 500_000_000


class _Layout:
    """Decoded slave layout for one engine instance."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.slaves: list[dict] = []
        next_offset = _shm.DATA_HEADER_SIZE
        for sl in cfg.get("slaves", []):
            # Same layout rules as resolve_slave_shm_offset(), in one pass
            alias, pos = slave_address(sl)
            off = _align_up_8(next_offset)
            tx_bytes, rx_bytes = slave_pdo_sizes(sl)
            next_offset = _align_up_8(off + tx_bytes + rx_bytes)
            inputs, outputs = slave_entry_layout(sl)
            for f in inputs + outputs:
                f["bit_offset"] += off * 8   # absolute within the data shm
            self.slaves.append({
                "label": sl.get("label", ""),
                "name": sl.get("name", ""),
                "alias": alias,
                "position": pos,
                "vendor_id": sl.get("vendor_id"),
                "product_code": sl.get("product_code"),
                "dc": bool(sl.get("dc_sync")),
                "shm_offset": off,
                "inputs": inputs,
                "outputs": outputs,
            })


class BusMonitor:
    """Read-only snapshot source for a running OdyCAT engine.

    Args:
        bus: the bus name, the config's "bus". If None, auto-detects on every
                   attach attempt, so the monitor can be started before
                   the engine.
    """

    def __init__(self, bus: Optional[str] = None):
        self._want_bus_name = bus
        self._bus_name: Optional[str] = None
        self._mm: Optional[mmap.mmap] = None
        self._ino: Optional[int] = None
        self._layout: Optional[_Layout] = None

    # ----------------------------------------------------------------
    #  Attach / detach
    # ----------------------------------------------------------------

    def _detach(self) -> None:
        if self._mm is not None:
            self._mm.close()
        self._mm = None
        self._ino = None
        self._layout = None

    def _read_config(self, bus: str) -> dict:
        # Not odycat._config.read_config: that one caches per process, and
        # the engine may be restarted with a different config.
        return read_config_region(bus)[1]

    def _attach(self) -> Optional[str]:
        """(Re)attach if needed. Returns an error string when not attached."""
        bus = self._want_bus_name
        if bus is None:
            try:
                bus = detect_bus()
            except RuntimeError as e:
                self._detach()
                return str(e)
        path = _data_shm_path(bus)
        try:
            ino = os.stat(path).st_ino
        except FileNotFoundError:
            self._detach()
            return f"OdyCAT engine not running (no shm at {path})"
        if self._mm is not None and ino == self._ino and bus == self._bus_name:
            return None

        self._detach()
        try:
            cfg = self._read_config(bus)
            fd = open_shm(path, os.O_RDONLY)
            try:
                mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
            finally:
                os.close(fd)
            try:
                _shm.check_data_header(mm, path)
            except RuntimeError:
                mm.close()
                raise
        except (OSError, ValueError, RuntimeError) as e:
            return f"could not attach to engine on '{bus}': {e}"
        self._layout = _Layout(cfg)
        self._mm = mm
        self._ino = ino
        self._bus_name = bus
        return None

    # ----------------------------------------------------------------
    #  Snapshot
    # ----------------------------------------------------------------

    def _copy_shm(self) -> bytes:
        """Copy the whole data SHM inside one seqlock read window."""
        cycle_us = self._layout.cfg.get("cycle_us", 1000) if self._layout else 1000
        return _seqlock_read(self._mm, 0, len(self._mm), cycle_us / 1e6)

    def layout(self) -> Optional[dict]:
        """Static description of the bus (config + field layout), or None."""
        if self._attach() is not None:
            return None
        return {"layout_id": self._ino,
                "bus_name": self._bus_name,
                "cycle_us": self._layout.cfg.get("cycle_us"),
                "slaves": self._layout.slaves}

    def snapshot(self) -> dict:
        """Current bus state and decoded PDO values of every slave."""
        err = self._attach()
        if err is not None:
            return {"engine": "stopped", "error": err, "time": time.time()}
        try:
            buf = self._copy_shm()
        except (RuntimeError, ValueError) as e:
            self._detach()
            return {"engine": "stopped", "error": str(e), "time": time.time()}

        u32 = lambda off: _shm.U32.unpack_from(buf, off)[0]    # noqa: E731
        bus_state, wkc, exp_wkc = (u32(_shm.OFF_BUS_STATE), u32(_shm.OFF_WORK_COUNTER),
                                   u32(_shm.OFF_EXPECTED_WKC))
        err_flags, st_off, st_count = (u32(_shm.OFF_ERROR_FLAGS), u32(_shm.OFF_STATUS_OFFSET),
                                       u32(_shm.OFF_STATUS_COUNT))
        responding, al_states = u32(_shm.OFF_SLAVES_RESPONDING), u32(_shm.OFF_AL_STATES)
        link_up, rt_flags = u32(_shm.OFF_LINK_UP), u32(_shm.OFF_RT_FLAGS)
        last_ns = _shm.U64.unpack_from(buf, _shm.OFF_LAST_CYCLE_NS)[0]
        cycles = self._cycle_count(buf)

        now_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        age_ns = now_ns - last_ns if last_ns else None
        if bus_state != 8:
            engine = "starting"
        elif age_ns is not None and age_ns > _STALL_TIMEOUT_NS:
            engine = "stalled"
        else:
            engine = "running"

        slaves = []
        for i, sl in enumerate(self._layout.slaves):
            status = None
            if st_off and i < st_count:
                entry = _STATUS.unpack_from(buf, st_off + _STATUS.size * i)
                al, online, operational, _, ring_pos, drops = entry[:6]
                al_code = entry[_shm.STATUS_AL_CODE]
                status = {
                    "al_state": al,
                    "al_state_name": AL_STATE_NAMES.get(al & 0x0F, "?" if al else "-"),
                    "online": bool(online),
                    "operational": bool(operational),
                    "ring_pos": ring_pos,
                    "drop_count": drops,
                    "al_code": al_code,
                    "al_status": al_status_text(al_code) if al_code else "",
                }
            slaves.append({
                "status": status,
                "inputs": [decode_entry(buf, f) for f in sl["inputs"]],
                "outputs": [decode_entry(buf, f) for f in sl["outputs"]],
            })

        return {
            "engine": engine,
            "layout_id": self._ino,
            "bus_name": self._bus_name,
            "time": time.time(),
            "bus": {
                "is_op": bus_state == 8,
                "work_counter": wkc,
                "expected_wkc": exp_wkc,
                "cycle_count": cycles,
                "wkc_errors": _shm.read_u64(self._mm, _shm.OFF_WKC_WRONG),
                "wkc_error_streak": u32(_shm.OFF_WKC_STREAK),
                "skipped_cycles": _shm.read_u64(self._mm, _shm.OFF_SKIPPED),
                "wkc_mismatch": bool(err_flags & _shm.ERR_WKC_MISMATCH),
                "slave_lost": bool(err_flags & _shm.ERR_SLAVE_LOST),
                "last_cycle_age_ms": age_ns / 1e6 if age_ns is not None else None,
                "has_status": bool(st_off),
                "slaves_responding": responding if st_off else None,
                "master_al_states": al_states if st_off else None,
                "link_up": bool(link_up) if st_off else None,
                "rt_sched_fifo": bool(rt_flags & _RT_SCHED_FIFO) if st_off else None,
                "rt_memory_locked": bool(rt_flags & _RT_MEMLOCK) if st_off else None,
            },
            "slaves": slaves,
        }

    def sample(self, selection: list[tuple[int, str, int]]) -> Optional[tuple]:
        """Decode only the selected entries, for fast periodic sampling.

        selection: (slave index, "inputs" | "outputs", entry index) tuples,
        indexing the lists returned by layout(). Returns
        (layout_id, cycle_count, last_cycle_ns, [values]) or None when the
        engine is not running. Raises IndexError/KeyError on a bad selection.
        """
        if self._attach() is not None:
            return None
        fields = [self._layout.slaves[s][d][e] for s, d, e in selection]
        try:
            buf = self._copy_shm()
        except (RuntimeError, ValueError):
            self._detach()
            return None
        last_ns = _shm.U64.unpack_from(buf, _shm.OFF_LAST_CYCLE_NS)[0]
        return self._ino, self._cycle_count(buf), last_ns, [decode_entry(buf, f) for f in fields]

    def _cycle_count(self, buf: bytes) -> int:
        """The 64-bit cycle counter, read live (the engine writes it outside
        the seqlock, so the copy in buf may be torn): at most one cycle
        newer than the data in buf."""
        return _read_cycle_count(self._mm)

    def close(self) -> None:
        self._detach()

    def __del__(self):
        self.close()
