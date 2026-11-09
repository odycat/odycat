# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/trace.py
"""
Trace — read-only view of the engine's cycle history.

The engine records every cycle in a ring in /run/odycat/<bus>_trace
(history_s long, 10 s by default): inputs as received, outputs as sent,
WKC, error flags and which slaves were out of OP. It also keeps a ring of
engine events (slave lost/back, WKC wrong/ok, link down/up, start/stop).
Python only reads it; the region is mode 0640.

Each reader keeps its own position, so any number of them can follow the
same trace. Nothing is lost while a reader stays within the history::

    trace = Trace()
    last = trace.head
    while True:
        slots, lost = trace.read(after=last)
        for s in slots:
            print(s.cycle, trace.value(s, "io_module", "di"))
        if slots:
            last = slots[-1].cycle
        time.sleep(0.01)

A slot decodes with the same layout as the data shm: the slave blocks sit
at the same offsets. Like Bus and Device, a Trace does not reconnect after
an engine restart; create a new one.
"""

import mmap
import os
from typing import NamedTuple, Optional, Union

from odycat import _shm

from odycat._config import (
    _trace_shm_path,
    decode_entry,
    detect_bus,
    open_shm,
    read_config,
)
from odycat.monitor import _Layout        # data-shm offsets of every entry
from odycat.signal import Signal


# magic, version, slot_size, slot_count, slots_offset, blocks_end,
# not_op_offset, event_count, events_offset, cycle_us
_TRACE_HDR = _shm.TRACE_HDR
_HEAD_OFFSET = _shm.OFF_TRACE_HEAD
_EVENT_HEAD_OFFSET = _shm.OFF_TRACE_EVENT_HEAD
_SKIPPED_OFFSET = _shm.OFF_TRACE_SKIPPED
_U64 = _shm.U64
# cycle, mono_ns, real_ns, work_counter, error_flags
_SLOT_HDR = _shm.SLOT_HDR
# seq, cycle, real_ns, type, slave, arg
_EVENT = _shm.EVENT

ENGINE_START = 1
ENGINE_STOP = 2
SLAVE_LOST = 3
SLAVE_BACK = 4
WKC_WRONG = 5
WKC_OK = 6
LINK_DOWN = 7
LINK_UP = 8
CYCLES_SKIPPED = 9                # arg = cycles skipped before this one
EVENT_NAMES = {
    ENGINE_START: "engine_start", ENGINE_STOP: "engine_stop",
    SLAVE_LOST: "slave_lost", SLAVE_BACK: "slave_back",
    WKC_WRONG: "wkc_wrong", WKC_OK: "wkc_ok",
    LINK_DOWN: "link_down", LINK_UP: "link_up",
    CYCLES_SKIPPED: "cycles_skipped",
}
NO_SLAVE = 0xFFFF


class Slot:
    """One recorded cycle. data holds the whole slot: decode entries with
    Trace.value() or Field.decode()."""

    __slots__ = ("cycle", "time_ns", "realtime_ns", "work_counter",
                 "error_flags", "data", "_not_op_offset")

    def __init__(self, data: bytes, not_op_offset: int):
        (self.cycle, self.time_ns, self.realtime_ns,
         self.work_counter, self.error_flags) = _SLOT_HDR.unpack_from(data, 0)
        self.data = data
        self._not_op_offset = not_op_offset

    def in_op(self, slave_index: int) -> bool:
        """Whether slave slave_index (config order) was in OP, as the engine
        last saw it (the status table is refreshed round-robin)."""
        byte = self.data[self._not_op_offset + slave_index // 8]
        return not (byte >> (slave_index % 8)) & 1

    def not_op_bytes(self) -> bytes:
        """The raw not-in-OP bitmap (bit i = slave i out of OP)."""
        return self.data[self._not_op_offset:]

    def __repr__(self) -> str:
        return f"Slot(cycle={self.cycle}, wkc={self.work_counter}, errors={self.error_flags})"


class EngineEvent(NamedTuple):
    seq: int
    cycle: int            # 0 = before the first cycle
    realtime_ns: int
    type: int
    slave: Optional[int]  # config index, None when not about a slave
    arg: int

    @property
    def name(self) -> str:
        return EVENT_NAMES.get(self.type, f"event_{self.type}")


class Field:
    """Where one entry (or one bit of it) sits in a slot."""

    __slots__ = ("slave_index", "slave_label", "label", "direction", "bit",
                 "convert", "_field", "first", "end")

    def __init__(self, slave_index: int, slave_label: str, field: dict,
                 direction: str, bit: Optional[int], convert=None, name: str = ""):
        self.slave_index = slave_index
        self.slave_label = slave_label
        self.label = name or field["label"]   # a signal's name, else the entry's label
        self.direction = direction      # "inputs" or "outputs"
        self.bit = bit
        self.convert = convert          # a signal's conversion, applied last
        self._field = field
        start = field["bit_offset"]
        if bit is not None:
            if not 0 <= bit < field["bits"]:
                raise ValueError(f"'{slave_label}.{self.label}' has {field['bits']} "
                                 f"bit(s); bit {bit} does not exist")
            start += bit
            nbits = 1
        else:
            nbits = field["bits"]
        self.first = start // 8                   # byte span holding the value
        self.end = (start + nbits + 7) // 8

    def decode(self, data):
        """The value in a slot's data (or any buffer in data-shm layout)."""
        v = decode_entry(data, self._field)
        if self.bit is not None:
            if not isinstance(v, int):          # float or byte-string entry
                raise TypeError(f"'{self.slave_label}.{self.label}' is not an integer")
            v = bool((v >> self.bit) & 1)
        return self.convert(v) if self.convert is not None else v

    def __repr__(self) -> str:
        bit = f"[{self.bit}]" if self.bit is not None else ""
        return f"Field({self.slave_label}.{self.label}{bit}, {self.direction})"


SlaveRef = Union[str, int, object]        # label, config index or Device
FieldRef = Union[SlaveRef, Signal]        # ... or a device variable (io.in03)


class Trace:
    """Read-only accessor for the engine's trace ring.

    Args:
        bus: the bus name, the config's "bus". If None, the one engine running.
    """

    def __init__(self, bus: Optional[str] = None):
        self._mm = None
        if bus is None:
            bus = detect_bus()
        self._bus_name = bus
        path = _trace_shm_path(bus)
        if not os.path.exists(path):
            raise RuntimeError(f"OdyCAT trace not available (no shm at {path}): "
                               f"engine not running, or started with history_s 0")
        fd = open_shm(path, os.O_RDONLY)
        try:
            self._mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
        finally:
            os.close(fd)
        (magic, version, self.slot_size, self.slot_count, self._slots_off,
         self._blocks_end, self._not_op_off, self.event_count, self._events_off,
         self.cycle_us) = _TRACE_HDR.unpack_from(self._mm, 0)
        if magic != _shm.TRACE_MAGIC:
            raise RuntimeError("OdyCAT trace: region not initialised (engine starting?)")
        if version != _shm.TRACE_VERSION:
            raise RuntimeError(f"OdyCAT trace: version {version} not supported "
                               f"(expected {_shm.TRACE_VERSION}): install the engine and the "
                               f"package from the same version")
        # A slot holds its 64-byte header, then the data region's bytes from
        # blocks_start on. Slots are handed out in data-region layout (the
        # header, then padding up to blocks_start), so entries decode with
        # the data region's offsets.
        blocks_start = _shm.U32.unpack_from(self._mm, _shm.OFF_TRACE_BLOCKS_START)[0]
        self._pad = bytes(blocks_start - _shm.TRACE_SLOT_HEADER_SIZE)
        self._not_op_off += len(self._pad)
        self.data_size = self.slot_size + len(self._pad)   # len(Slot.data)
        self._layout = _Layout(read_config(bus))

    # ----------------------------------------------------------------
    #  Ring positions
    # ----------------------------------------------------------------

    def _read_u64(self, offset: int) -> int:
        """A u64 the engine stores outside any lock: read until two agree."""
        a = _U64.unpack_from(self._mm, offset)[0]
        while True:
            b = _U64.unpack_from(self._mm, offset)[0]
            if a == b:
                return a
            a = b

    @property
    def head(self) -> int:
        """Cycle of the newest recorded slot (0 before the first cycle)."""
        return self._read_u64(_HEAD_OFFSET)

    @property
    def oldest(self) -> int:
        """Oldest cycle still in the ring (0 if none)."""
        head = self.head
        return max(1, head - self.slot_count + 1) if head else 0

    @property
    def history_s(self) -> float:
        return self.slot_count * self.cycle_us / 1e6

    @property
    def skipped_cycles(self) -> int:
        """Cycles the engine skipped since it started. When it is late by half a
        period or more (no RT, a stall), it skips to the current or next slot
        of its grid instead of catching up with cycles a few µs apart, so
        frames are always at least half a period apart; each time, a
        CYCLES_SKIPPED event records how many. Cycle
        numbers count cycles that ran, so cycle + skipped_cycles is the number
        of bus periods elapsed: use that, or the slots' time_ns, for anything
        that has to follow real time (a trajectory)."""
        return self._read_u64(_SKIPPED_OFFSET)

    # ----------------------------------------------------------------
    #  Slots
    # ----------------------------------------------------------------

    def slot(self, cycle: int) -> Optional[Slot]:
        """The slot of one cycle, or None if it is not (or no longer) in
        the ring. A slot overwritten while it was being copied is None too."""
        if cycle < 1:
            return None
        off = self._slots_off + ((cycle - 1) % self.slot_count) * self.slot_size
        if _U64.unpack_from(self._mm, off)[0] != cycle:
            return None
        data = self._mm[off:off + self.slot_size]
        if _U64.unpack_from(self._mm, off)[0] != cycle or \
                _U64.unpack_from(data, 0)[0] != cycle:
            return None
        h = _shm.TRACE_SLOT_HEADER_SIZE
        return Slot(data[:h] + self._pad + data[h:], self._not_op_off)

    def read(self, after: int, limit: Optional[int] = None) -> tuple[list[Slot], int]:
        """Slots of the cycles after 'after', oldest first, up to the head
        (at most 'limit' of them). Returns (slots, lost): lost counts the
        cycles after 'after' that are no longer in the ring, because the
        reader fell more than history_s behind."""
        head = self.head
        first = after + 1
        if head < first:
            return [], 0
        oldest = max(1, head - self.slot_count + 1)
        lost = max(0, oldest - first)
        last = head if limit is None else min(head, max(first, oldest) + limit - 1)
        slots = []
        for cycle in range(max(first, oldest), last + 1):
            s = self.slot(cycle)
            if s is None:                     # overwritten while reading
                lost += 1
            else:
                slots.append(s)
        return slots, lost

    # ----------------------------------------------------------------
    #  Engine events
    # ----------------------------------------------------------------

    @property
    def event_head(self) -> int:
        """Sequence number of the newest engine event (0 if none)."""
        return self._read_u64(_EVENT_HEAD_OFFSET)

    def events(self, after: int = 0) -> tuple[list[EngineEvent], int]:
        """Engine events with seq > after, oldest first. Returns
        (events, lost), as read()."""
        head = self.event_head
        first = after + 1
        if head < first:
            return [], 0
        oldest = max(1, head - self.event_count + 1)
        lost = max(0, oldest - first)
        out = []
        for seq in range(max(first, oldest), head + 1):
            off = self._events_off + ((seq - 1) % self.event_count) * _EVENT.size
            rec = _EVENT.unpack_from(self._mm, off)
            if rec[0] != seq or _U64.unpack_from(self._mm, off)[0] != seq:
                lost += 1
                continue
            _, cycle, real_ns, typ, slave, arg = rec
            out.append(EngineEvent(seq, cycle, real_ns, typ,
                                   None if slave == NO_SLAVE else slave, arg))
        return out, lost

    # ----------------------------------------------------------------
    #  Fields
    # ----------------------------------------------------------------

    @property
    def slaves(self) -> list[dict]:
        """Slave layout (as BusMonitor.layout()["slaves"])."""
        return self._layout.slaves

    def slave_index(self, slave: SlaveRef) -> int:
        """Config index of a slave given by label, index, Device or one of
        its variables."""
        if isinstance(slave, Signal):
            slave = slave.device
        other = getattr(slave, "_bus_name", None)
        if other is not None and other != self._bus_name:
            label = getattr(slave, "_slave_label", None) or type(slave).__name__
            raise ValueError(f"'{label}' is on the engine of bus '{other}'; this "
                             f"Trace/Events reads '{self._bus_name}'. Use "
                             f"Events(bus='{other}') for its devices.")
        if isinstance(slave, int):
            if not 0 <= slave < len(self._layout.slaves):
                raise KeyError(f"no slave with index {slave}")
            return slave
        if isinstance(slave, str):
            label = slave
        else:
            index = getattr(slave, "_status_index", None)
            if index is not None:
                return index
            label = getattr(slave, "_slave_label", None)
            if label is None:
                raise TypeError(f"expected a slave label, index or Device, got {slave!r}")
        for i, sl in enumerate(self._layout.slaves):
            if sl["label"] == label:
                return i
        raise KeyError(f"no slave labelled '{label}' in the config")

    def signal_field(self, sig: Signal) -> Field:
        """Locate a device variable (io.in03, servo.position_actual_value)
        by its object index, as the device reads it."""
        i = self.slave_index(sig.device)
        sl = self._layout.slaves[i]
        for direction in ("inputs", "outputs"):
            for f in sl[direction]:
                if int(f["index"], 16) == sig.index and f["subindex"] == sig.subindex:
                    return Field(i, sl["label"], f, direction, sig.bit,
                                 sig.spec.convert, sig.name)
        raise KeyError(f"{sig.name} (slave '{sl['label']}'): object "
                       f"0x{sig.index:04X}:{sig.subindex:02X} is not in this slave's "
                       f"PDO map. Add it to the config \"txpdo\" or \"rxpdo\".")

    def field(self, slave: FieldRef, entry: Optional[str] = None,
              bit: Optional[int] = None) -> Field:
        """Locate an entry: a device variable (io.in03), or a slave and an
        entry label (inputs first, then outputs), optionally one bit of it
        (bit 0 = least significant)."""
        if isinstance(slave, Signal):
            if entry is not None or bit is not None:
                raise TypeError(f"{slave.name} already names its entry and bit")
            return self.signal_field(slave)
        if entry is None:
            raise TypeError("name the entry: field(device, \"entry\"), or pass a "
                            "device variable such as io.in03")
        i = self.slave_index(slave)
        sl = self._layout.slaves[i]
        for direction in ("inputs", "outputs"):
            for f in sl[direction]:
                if f["label"] == entry:
                    return Field(i, sl["label"], f, direction, bit)
        names = [f["label"] for d in ("inputs", "outputs") for f in sl[d]]
        raise KeyError(f"'{sl['label']}' has no entry '{entry}' "
                       f"(entries: {', '.join(names)})")

    def value(self, slot: Slot, slave: FieldRef, entry: Optional[str] = None,
              bit: Optional[int] = None):
        """Decode one entry (or bit) of a slot: value(slot, io.in03) or
        value(slot, "io_module", "inputs", bit=3)."""
        return self.field(slave, entry, bit).decode(slot.data)

    def close(self):
        if self._mm:
            self._mm.close()
            self._mm = None

    def __del__(self):
        self.close()
