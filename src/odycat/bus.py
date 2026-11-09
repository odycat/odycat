# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/bus.py
"""
Bus — read-only view of the OdyCAT shared memory global header.

The C engine is the sole writer of the header. Python only reads.
"""

import os
import mmap
import time
from typing import Optional

from odycat import _shm
from odycat._config import (check_engine_running, detect_bus, open_shm, read_config, _data_shm_path,
                            engine_info as _engine_info)

_RT_SCHED_FIFO = _shm.RT_SCHED_FIFO   # rt_flags: engine runs with SCHED_FIFO
_RT_MEMLOCK = _shm.RT_MEMLOCK         # rt_flags: engine memory is locked

_U32 = _shm.U32


def _read_cycle_count(mm) -> int:
    """The engine's 64-bit cycle counter (written outside the seqlock)."""
    return _shm.read_u64(mm, _shm.OFF_CYCLE_COUNT)


_SPINS_BEFORE_YIELD = 50


def _seqlock_read(mm, start: int, end: int, cycle_s: float = 0.001) -> bytes:
    """mm[start:end] as one consistent copy of a cycle's inputs.

    The engine makes the sequence word odd while it copies the inputs into
    shm and even when done; a copy taken while it was odd, or during which
    it changed, is retried. After a few spins the reader yields the CPU, so
    an engine preempted inside its copy (no RT) can finish. Raises
    RuntimeError only if the engine is stuck: the sequence stays odd and the
    cycle counter does not move for 10 cycles (at least 50 ms), e.g. because
    the engine died in the middle of a copy."""
    stuck_s = max(0.05, 10 * cycle_s)
    spins = 0
    deadline = cycle = None
    while True:
        seq1 = _U32.unpack_from(mm, _shm.OFF_SEQ)[0]
        if not seq1 & 1:
            data = mm[start:end]
            if _U32.unpack_from(mm, _shm.OFF_SEQ)[0] == seq1:
                return data
        spins += 1
        if spins < _SPINS_BEFORE_YIELD:
            continue
        os.sched_yield()
        now = time.monotonic()
        count = _read_cycle_count(mm)
        if deadline is None or count != cycle:           # the engine is moving
            deadline, cycle = now + stuck_s, count
        elif now > deadline:
            raise RuntimeError(f"OdyCAT: the engine stopped in the middle of copying the "
                               f"inputs (no progress for {stuck_s * 1000:.0f} ms); "
                               f"restart odycat")


def _wait_cycles(mm, n: int, cycle_s: float, timeout_s: Optional[float]) -> int:
    """Block until the engine has completed n more cycles: sleep through
    all but the last cycle, then poll every half cycle. Returns the cycle
    count reached.

    The counter only advances when the engine actually runs a cycle, so
    an engine stall makes the wait longer instead of shorter. Raises
    RuntimeError if the counter does not advance n cycles within
    timeout_s (default: 10x the expected time, at least 1 s), e.g.
    because the engine stopped."""
    start = _read_cycle_count(mm)
    if n <= 0:
        return start
    if timeout_s is None:
        timeout_s = max(1.0, 10 * n * cycle_s)
    deadline = time.monotonic() + timeout_s
    while True:
        now = _read_cycle_count(mm)
        remaining = n - (now - start)
        if remaining <= 0:
            return now
        t = time.monotonic()
        if t > deadline:
            raise RuntimeError(f"OdyCAT: engine did not complete {n} cycle(s) "
                               f"within {timeout_s:.3g} s (stopped or stalled?)")
        step = (remaining - 1) * cycle_s if remaining > 1 else cycle_s / 2
        time.sleep(max(min(step, deadline - t), cycle_s / 2))


class Bus:
    """Read-only accessor for the OdyCAT shared memory header.

    Args:
        name: the bus name, the config's "bus" (e.g. "bus0"). If None, the one
              engine running is used (found by scanning ``*_config`` in the
              run directory, ``/run/odycat``).
    """

    def __init__(self, name: Optional[str] = None):
        self._mm = None
        if name is None:
            name = detect_bus()
        self._bus_name = name
        path = _data_shm_path(name)
        if not os.path.exists(path):
            raise RuntimeError(f"OdyCAT engine not running (no shm at {path})")
        fd = open_shm(path, os.O_RDONLY)
        try:
            self._mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
        finally:
            os.close(fd)
        _shm.check_data_header(self._mm, path)
        check_engine_running(self._mm, path, name)
        self._cycle_s = read_config(name).get("cycle_us", 1000) / 1e6

    @property
    def name(self) -> str:
        """The bus name (the config's \"bus\")."""
        return self._bus_name

    def _u32(self, offset: int) -> int:
        return _U32.unpack_from(self._mm, offset)[0]

    def _bus_state(self) -> int:
        return self._u32(_shm.OFF_BUS_STATE)

    @property
    def work_counter(self) -> int:
        return self._u32(_shm.OFF_WORK_COUNTER)

    @property
    def expected_wkc(self) -> int:
        return self._u32(_shm.OFF_EXPECTED_WKC)

    @property
    def cycle_count(self) -> int:
        """Cycles run since the engine started (64-bit, does not wrap)."""
        return _read_cycle_count(self._mm)

    def _error_flags(self) -> int:
        return self._u32(_shm.OFF_ERROR_FLAGS)

    @property
    def last_cycle_ns(self) -> int:
        return _shm.read_u64(self._mm, _shm.OFF_LAST_CYCLE_NS)

    @property
    def wkc_ok(self) -> bool:
        """The working counter of the last cycle was the expected one."""
        return self.work_counter == self.expected_wkc

    @property
    def wkc_errors(self) -> int:
        """Cycles with a wrong working counter since the engine started.
        The engine takes no action on its own: what a wrong WKC means depends
        on the machine, so the script decides (see python-api.md)."""
        return _shm.read_u64(self._mm, _shm.OFF_WKC_WRONG)

    @property
    def wkc_error_streak(self) -> int:
        """Consecutive cycles with a wrong working counter, up to the last
        one; 0 when the last cycle was right."""
        return self._u32(_shm.OFF_WKC_STREAK)

    @property
    def skipped_cycles(self) -> int:
        """Cycles the engine skipped since it started, because it woke up half
        a period late or more (see Trace.skipped_cycles and docs/realtime.md)."""
        return _shm.read_u64(self._mm, _shm.OFF_SKIPPED)

    @property
    def is_op(self) -> bool:
        """The engine has brought the bus to OP and is running its cycle loop
        (False once it stopped, was killed or hangs)."""
        return _shm.engine_down(self._mm) is None

    @property
    def slaves_ok(self) -> bool:
        """Every configured slave is online and in OP, and as many slaves
        respond as the config has. False while any of them is lost, and
        while the engine is not running the bus."""
        return self.is_op and not self._error_flags() & _shm.ERR_SLAVE_LOST

    def _rt_flags(self) -> int:
        return self._u32(_shm.OFF_RT_FLAGS)

    @property
    def engine_info(self) -> dict:
        """Which engine runs and which config it loaded: engine_version,
        engine_git, config_sha256, engine_pid, support_end, start_realtime_ns,
        instance_id, layout_version (see odycat._config.engine_info)."""
        return _engine_info(self._bus_name)

    @property
    def rt_scheduling(self) -> bool:
        """True if the engine runs with SCHED_FIFO."""
        return bool(self._rt_flags() & _RT_SCHED_FIFO)

    @property
    def memory_locked(self) -> bool:
        """True if the engine locked its memory (no page faults in the loop)."""
        return bool(self._rt_flags() & _RT_MEMLOCK)

    def wait_cycles(self, n: int = 1, timeout_s: Optional[float] = None) -> int:
        """Block until the engine has run n more bus cycles.

        A write can land just after the engine copied the outputs of the
        cycle in progress, so: wait_cycles(2) guarantees outputs written
        before the call have been sent; wait_cycles(3) that the frame
        carrying them has come back and its inputs are in shared memory.
        How long a slave takes to act on an output is up to the slave.
        Returns the new cycle count."""
        return _wait_cycles(self._mm, n, self._cycle_s, timeout_s)

    def trace(self):
        """The engine's cycle history for this bus (odycat.trace.Trace)."""
        from odycat.trace import Trace
        return Trace(self._bus_name)

    def close(self):
        if self._mm:
            self._mm.close()
            self._mm = None

    def __del__(self):
        self.close()
