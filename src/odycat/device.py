# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/device.py
"""
Device — base class for all EtherCAT slave abstractions.

Opens the OdyCAT shared memory region and provides PDO access: inputs
through a seqlock, outputs through buffers the engine never sends while
they are being written.

Supports two write modes:
  - **Immediate** (default): write_rxpdo / set_rxpdo_field / set_rxpdo_bit
    take effect immediately in shared memory.
  - **Deferred**: call stage_rxpdo / stage_rxpdo_field / stage_rxpdo_bit to
    buffer changes, then flush all staged devices at once with the class
    method Device.transmit_outputs(). This is essential for multi-axis
    motion synchronisation.

Single PDO entries can also be accessed by their config label with
read_entry / write_entry (immediate mode).

Every output write changes only the bits it targets (a field, an entry, a
bit), so several scripts can share an output module without overwriting
each other's outputs. A write fills a fresh copy of the slave's outputs
and publishes it in one step: the engine sends either the copy before the
write or the copy after it, never a half-written value. The slaves of one
sync group ("sync_group" in the config) are published together, so the
devices of one transmit_outputs() call in the same group go out in the
same cycle. Writers of a group take a kernel lock for microseconds, which
the kernel releases if a process dies; a process that dies mid-write
publishes nothing.

When the script ends (normally, by an exception, Ctrl+C, SIGTERM or SIGHUP)
every output bit a Device wrote is set back to 0, unless
keep_outputs_on_exit() was called. kill -9 or a crash skip this.
"""

import atexit
import fcntl
import os
import mmap
import re
import signal
import struct
import sys
import threading
import time
from contextlib import contextmanager
from typing import Optional

from odycat._config import (
    check_engine_running,
    detect_bus,
    find_slave,
    read_config,
    slave_config,
    direction_format,
    resolve_slave_shm_offset,
    slave_entry_layout,
    decode_entry,
    open_shm,
    _entry_struct,
    _data_shm_path,
)
from odycat import _shm
from odycat._al import describe as _describe_state
from odycat.bus import _RT_SCHED_FIFO, _seqlock_read, _wait_cycles


_HDR_STATUS = struct.Struct("<II")        # status_offset, status_count
_LIVENESS = _shm.LIVENESS
_OFF_LIVENESS = _shm.OFF_LAST_CYCLE_NS
_monotonic_ns = time.monotonic_ns
_STATUS = _shm.STATUS                     # al_state, online, operational, refreshed,
                                          # ring_pos, drop_count, block_offset, in, out,
                                          # al_code


class ObjectNotMappedError(KeyError):
    """An object is not in the slave's PDO map (as configured)."""

    def __str__(self) -> str:
        return str(self.args[0])


class DeviceLostError(RuntimeError):
    """The slave dropped out of OP.

    Raised by any read or write on a Device whose slave is out of OP, and
    once for every drop that happened since the object last saw one, even
    if the slave is already back: the engine zeroed its outputs when it
    left OP (drives come back disabled), so the script must decide how to
    carry on. Catch it to recover; the next call succeeds once the slave
    is back in OP."""

    def __init__(self, label: str, message: str, drops: int, al_state: int,
                 al_code: int = 0):
        super().__init__(f"OdyCAT: '{label}' {message}")
        self.label = label
        self.drops = drops          # new drops reported by this exception
        self.al_state = al_state    # AL state when raised (8 = OP; +0x10 = error)
        self.al_code = al_code      # AL status code (0x0134) when raised, 0 if none

class EngineStoppedError(RuntimeError):
    """The engine is not running the bus: it stopped, was killed, crashed or
    hangs (no cycle for 200 ms, or 20 cycles if longer), or has not reached
    OP yet.

    Raised by any read or write on a Device. Inputs read now would be stale
    and outputs written would not be sent. A restarted engine creates a new
    region: restart the script to use it."""


# Global registry of devices with staged (deferred) RX data.
_staged_devices: list["Device"] = []

# Devices that wrote outputs, zeroed at exit. Strong references: a writer
# stays alive until exit so its outputs can be cleared.
_writers: list["Device"] = []
_exit_hooks_installed = False


def _zero_written_outputs() -> None:
    """atexit: set back to 0 every output bit a Device wrote."""
    for dev in list(_writers):
        try:
            dev._release_outputs()
        except Exception:
            pass    # best effort: the engine may be gone
    _writers.clear()


def _exit_on_signal(signum, frame):
    raise SystemExit(128 + signum)


def _install_exit_hooks() -> None:
    """Run the atexit cleanup on SIGTERM/SIGHUP too (Python's default for
    them is to die without running atexit), unless the script set its own
    handlers or this is not the main thread."""
    global _exit_hooks_installed
    if _exit_hooks_installed:
        return
    _exit_hooks_installed = True
    atexit.register(_zero_written_outputs)
    if threading.current_thread() is not threading.main_thread():
        return
    for sig in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(sig) is signal.SIG_DFL:
            signal.signal(sig, _exit_on_signal)


_FIELD_RE = re.compile(r"(\d*)([xcbB?hHiIlLqQnNefdspP])")

# One lock per sync group. Between processes: an OFD byte-range lock on
# byte <group> of the data region (also excludes other Device objects of
# this process: each holds its own open file description; released by the
# kernel when the process dies). Between threads sharing a Device: this
# lock, keyed by the region's inode so a restarted engine gets new ones.
_group_tlocks: dict[tuple[int, int], threading.Lock] = {}
_group_tlocks_guard = threading.Lock()
_FLOCK = struct.Struct("hhqqi4x")         # struct flock (l_type, l_whence, l_start, l_len, l_pid)


def _group_tlock(inode: int, group: int) -> threading.Lock:
    with _group_tlocks_guard:
        return _group_tlocks.setdefault((inode, group), threading.Lock())


def _ofd_lock(fd: int, group: int, kind: int) -> None:
    fcntl.fcntl(fd, fcntl.F_OFD_SETLKW, _FLOCK.pack(kind, os.SEEK_SET, group, 1, 0))


def _publish_group(items, check_link: bool = True) -> list:
    """Write output patches [(device, data, mask)] of one sync group and
    publish them together. For each slave: a buffer that is neither the
    published one nor the one the engine is sending gets the published
    outputs (zeros if the slave left OP since, see the buffer epoch) with
    the patch on top; then one store of the group word publishes every
    slave at once. Returns the DeviceLostErrors of slaves that were skipped
    (out of OP); the others are still written."""
    dev0 = items[0][0]
    mm, errors, written = dev0._mm, [], []
    with dev0._group_lock():
        pub = _shm.load_word(mm, dev0._pub_off)
        busy = _shm.load_word(mm, dev0._busy_off)
        word, filled = pub, {}
        for dev, data, mask in items:
            if check_link:
                try:
                    dev._check_link()
                except DeviceLostError as e:
                    errors.append(e)
                    continue
            shift = 2 * dev._member
            w = filled.get(dev._member)
            if w is None:
                cur, eng = (pub >> shift) & 3, (busy >> shift) & 3
                w = next(i for i in range(_shm.OUT_BUFFERS) if i != cur and i != eng)
                dev._fill_buffer(w, cur)
                filled[dev._member] = w
                word = (word & ~(3 << shift)) | (w << shift)
            dev._merge_into_buffer(w, data, mask)
            written.append((dev, mask))
        if word != pub:
            _shm.store_word(mm, dev0._pub_off, word)
    if check_link:
        for dev, mask in written:
            dev._note_written(mask)
    return errors


def _field_spans(fmt: str) -> list[tuple[int, int, str]]:
    """(byte offset, size, struct code) of every field of a packed format."""
    spans, off = [], 0
    for count, code in _FIELD_RE.findall(fmt.lstrip("@=<>!")):
        n = int(count) if count else 1
        if code in "sp":
            spans.append((off, n, f"{n}{code}"))
            off += n
        elif code == "x":
            off += n
        else:
            size = struct.calcsize("=" + code)
            for _ in range(n):
                spans.append((off, size, code))
                off += size
    return spans


class Device:
    """Base class for OdyCAT EtherCAT slave devices.

    Can be instantiated in two ways:

    1. **From the config (preferred)**::

           dev = Device(position=0)                # absolute ring position
           dev = Device(alias=3, position=1)       # offset 1 from alias 3 ("3:1")
           dev = Device(label="stepper_x")         # config "label"
           dev = Device(position=0, bus="bus0")

       Addresses follow IgH / `ethercat slaves`: with alias 0 the position is
       absolute; with an alias it is the offset from the slave carrying that
       station alias, so rewiring before that slave does not change it.

       The block offset and the txpdo_fmt / rxpdo_fmt formats are looked up
       from the config SHM that the C engine populates at startup.

    2. **With fixed formats** (device classes)::

           dev = Device(position=0, txpdo_fmt="=H", rxpdo_fmt="=H")

       The formats must match the slave's configured PDOs.
    """

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0,
                 label: Optional[str] = None,
                 bus: Optional[str] = None,
                 txpdo_fmt: Optional[str] = None,
                 rxpdo_fmt: Optional[str] = None):
        self._mm = None
        self._fd = None
        if bus is None:
            bus = detect_bus()
        self._bus_name = bus
        self._position = position
        self._alias = alias
        self._label = label

        # Block offset from the layout; formats from the config unless given
        cfg = read_config(bus)
        shm_offset = resolve_slave_shm_offset(cfg, position, alias=alias, label=label)
        if txpdo_fmt is None or rxpdo_fmt is None:
            sc = slave_config(position, bus, alias=alias, label=label)
            if txpdo_fmt is None:
                txpdo_fmt = direction_format(sc, "in")
            if rxpdo_fmt is None:
                rxpdo_fmt = direction_format(sc, "out")

        path = _data_shm_path(bus)
        if not os.path.exists(path):
            raise RuntimeError(f"OdyCAT engine not running (no shm at {path})")
        fd = open_shm(path, os.O_RDWR)
        try:
            self._mm = mmap.mmap(fd, 0)
            _shm.check_data_header(self._mm, path)
            check_engine_running(self._mm, path, bus)
        except BaseException:
            if self._mm is not None:
                self._mm.close()
                self._mm = None
            os.close(fd)
            raise
        self._fd = fd                      # kept open for the group lock
        self._inode = os.fstat(fd).st_ino
        self._off = shm_offset
        self._tx_fmt = txpdo_fmt
        self._rx_fmt = rxpdo_fmt
        self._tx_size = struct.calcsize(txpdo_fmt) if txpdo_fmt else 0
        self._rx_size = struct.calcsize(rxpdo_fmt) if rxpdo_fmt else 0
        self._rx_off = shm_offset + self._tx_size   # outputs as sent (written by the engine)
        self._rx_spans = _field_spans(rxpdo_fmt) if rxpdo_fmt else []
        self._staged: Optional[tuple[bytearray, bytearray]] = None  # (data, mask)
        self._written = bytearray(self._rx_size)   # bits written, cleared at exit
        self._keep_on_exit = False
        self._entries: Optional[tuple[list[dict], list[dict]]] = None
        self._objects: Optional[dict] = None   # see _object_map()
        self._cycle_s = read_config(bus).get("cycle_us", 1000) / 1e6
        self._stale_ns = _shm.stale_after_ns(read_config(bus).get("cycle_us", 1000))
        self._status_index: Optional[int] = None   # entry in the status table
        self._status_off: Optional[int] = None
        self._drops_seen = 0
        self._dc = False                   # the config runs this slave with DC
        try:
            cfg = read_config(bus)
            sc = find_slave(cfg, position, alias=alias, label=label)
            self._status_index = cfg["slaves"].index(sc)
            self._slave_label = sc.get("label", f"{alias}:{position}")
            self._dc = sc.get("dc_sync") is not None
        except (KeyError, ValueError):
            self._slave_label = label or f"{alias}:{position}"   # not in config: no check
        self._resolve_status()
        if self._status_off is not None:
            self._drops_seen = _STATUS.unpack_from(self._mm, self._status_off)[_shm.STATUS_DROPS]
        self._group: Optional[int] = None  # sync group, None without outputs
        if self._rx_size and self._status_index is not None:
            self._resolve_outputs()

    def _resolve_outputs(self) -> None:
        """This slave's output buffers and sync group, from the output table
        the engine writes when it creates the region."""
        table, groups = _shm.U32.unpack_from(self._mm, _shm.OFF_OUT_TABLE)[0], \
            _shm.U32.unpack_from(self._mm, _shm.OFF_GROUP_COUNT)[0]
        buf, stride, group, member = _shm.OUT_ENTRY.unpack_from(
            self._mm, table + _shm.OUT_ENTRY.size * self._status_index)[:4]
        if (group == _shm.NO_GROUP or group >= groups or
                stride < _shm.OUT_BUF_HEADER + self._rx_size or
                member >= _shm.GROUP_MAX_MEMBERS or
                buf + _shm.OUT_BUFFERS * stride > len(self._mm)):
            raise RuntimeError(
                f"OdyCAT: '{self._slave_label}': the engine has no output buffers for "
                f"this slave's {self._rx_size} output bytes. The device class does not "
                f"match the slave's PDOs in the config, or the engine and the package "
                f"are not the same version.")
        self._group, self._member = group, member
        self._buf_off, self._buf_stride = buf, stride
        self._pub_off = _shm.U32.unpack_from(self._mm, _shm.OFF_GROUPS)[0] + 8 * group
        self._busy_off = _shm.U32.unpack_from(self._mm, _shm.OFF_BUSY)[0] + 8 * group
        self._tlock = _group_tlock(self._inode, group)

    # ----------------------------------------------------------------
    #  Link supervision
    # ----------------------------------------------------------------

    def _resolve_status(self) -> None:
        """Find this slave's status entry (the engine publishes the table
        when the bus reaches OP) and check the block the engine placed for
        this slave against the one this object uses."""
        if self._status_index is None or self._status_off is not None:
            return
        off, count = _HDR_STATUS.unpack_from(self._mm, _shm.OFF_STATUS_OFFSET)
        if off and self._status_index < count:
            entry = off + _STATUS.size * self._status_index
            block, in_bytes, out_bytes = _STATUS.unpack_from(self._mm, entry)[6:9]
            if (block, in_bytes, out_bytes) != (self._off, self._tx_size, self._rx_size):
                raise RuntimeError(
                    f"OdyCAT: '{self._slave_label}': the engine placed this slave at "
                    f"byte {block} with {in_bytes} input and {out_bytes} output bytes, "
                    f"this {type(self).__name__} uses byte {self._off} with "
                    f"{self._tx_size} and {self._rx_size}. The device class does not "
                    f"match the slave's PDOs in the config, or the engine and the "
                    f"package are not the same version.")
            self._status_off = entry

    def _engine_stopped(self) -> None:
        """Raise EngineStoppedError with the reason (the fast check failed)."""
        down = _shm.engine_down(self._mm)
        if down:
            raise EngineStoppedError(
                f"OdyCAT: the engine of bus '{self._bus_name}' {down}; inputs are not "
                f"updated and outputs are not sent"
                + ("" if down == _shm.ENGINE_STARTING else
                   ". Restart this script once the engine runs again"))

    def _check_link(self) -> None:
        """Raise EngineStoppedError if the engine is not running the bus, and
        DeviceLostError if the slave is out of OP or dropped out since the
        last check."""
        # Fast path: one read and a clock call (engine_down() only on failure)
        last, state = _LIVENESS.unpack_from(self._mm, _OFF_LIVENESS)
        if state != 8 or not last or _monotonic_ns() - last > self._stale_ns:
            self._engine_stopped()
        if self._status_off is None:
            self._resolve_status()

            if self._status_off is None:
                return
        entry = _STATUS.unpack_from(self._mm, self._status_off)
        al, online, op, refreshed, _, drops = entry[:6]
        al_code = entry[_shm.STATUS_AL_CODE]
        if not refreshed:                  # engine has not written this entry yet
            return
        state = "back in OP" if online and op else _describe_state(al, online, al_code)
        if drops != self._drops_seen:
            new = (drops - self._drops_seen) & 0xFFFF
            self._drops_seen = drops
            raise DeviceLostError(
                self._slave_label,
                f"dropped out of OP {new} time(s), now {state}; its outputs "
                f"were zeroed (drives come back disabled){self._drop_hint()}", new, al,
                al_code)
        if not (online and op):
            raise DeviceLostError(self._slave_label, f"is out of OP, {state}"
                                  f"{self._drop_hint()}", 0, al, al_code)

    def _drop_hint(self) -> str:
        """The likely cause, when the slave uses DC and the engine is not
        real-time: a late cycle left a Sync0 period without data."""
        if not self._dc or _shm.U32.unpack_from(self._mm, _shm.OFF_RT_FLAGS)[0] & _RT_SCHED_FIFO:
            return ""
        return (". It uses DC and the engine runs without real-time scheduling: a late "
                "cycle leaves a Sync0 period without data. Run the engine with real-time "
                "scheduling (sudo, or CAP_SYS_NICE), or remove \"dc_sync\" from this "
                "slave if it does not need it (only CSP/CSV/CST and coordinated axes do)")

    # ----------------------------------------------------------------
    #  Internal primitives
    # ----------------------------------------------------------------

    def _read_txpdo_raw(self) -> bytes:
        """This slave's inputs, one consistent copy (see bus._seqlock_read)."""
        self._check_link()
        return _seqlock_read(self._mm, self._off, self._off + self._tx_size, self._cycle_s)

    def _read_rxpdo_raw(self) -> bytes:
        """This slave's outputs as last written (published): sent from the
        next cycle on."""
        self._check_link()
        return self._published_outputs()

    # -- output buffers --------------------------------------------------

    @contextmanager
    def _group_lock(self):
        if self._group is None:
            raise RuntimeError(f"OdyCAT: '{self._slave_label}' has no outputs in the "
                               f"engine's config")
        with self._tlock:
            _ofd_lock(self._fd, self._group, fcntl.F_WRLCK)
            try:
                yield
            finally:
                _ofd_lock(self._fd, self._group, fcntl.F_UNLCK)

    def _drop_count(self) -> int:
        """The slave's drop_count, the epoch of a current output buffer
        (0 until the engine publishes the status table)."""
        if self._status_off is None:
            self._resolve_status()
            if self._status_off is None:
                return 0
        return _STATUS.unpack_from(self._mm, self._status_off)[_shm.STATUS_DROPS]

    def _buffer_outputs(self, idx: int) -> Optional[int]:
        """Offset of the outputs in buffer idx, or None if that buffer is
        not current (an invalid index, or written before the slave last left
        OP: the engine sends zeros for it)."""
        if idx >= _shm.OUT_BUFFERS:
            return None
        off = self._buf_off + idx * self._buf_stride
        if _shm.U32.unpack_from(self._mm, off)[0] != self._drop_count():
            return None
        return off + _shm.OUT_BUF_HEADER

    def _published_outputs(self) -> bytes:
        if not self._rx_size:
            return b""
        with self._group_lock():
            idx = (_shm.load_word(self._mm, self._pub_off) >> (2 * self._member)) & 3
            off = self._buffer_outputs(idx)
            if off is None:
                return bytes(self._rx_size)
            return bytes(self._mm[off: off + self._rx_size])

    def _fill_buffer(self, w: int, cur: int) -> None:
        """Buffer w (not visible to the engine) := the published outputs,
        or zeros if they are not current; stamped with the current epoch.
        Caller holds the group lock."""
        dst = self._buf_off + w * self._buf_stride
        src = self._buffer_outputs(cur)
        h = _shm.OUT_BUF_HEADER
        self._mm[dst + h: dst + h + self._rx_size] = (
            self._mm[src: src + self._rx_size] if src is not None else bytes(self._rx_size))
        _shm.U32.pack_into(self._mm, dst, self._drop_count())

    def _merge_into_buffer(self, w: int, data, mask) -> None:
        """Put the masked bits of data into buffer w; the other bits keep
        their value (other writers' outputs). Caller holds the group lock."""
        off = self._buf_off + w * self._buf_stride + _shm.OUT_BUF_HEADER
        n = self._rx_size
        m = int.from_bytes(mask, "little")
        old = int.from_bytes(self._mm[off: off + n], "little")
        new = (old & ~m) | (int.from_bytes(data, "little") & m)
        self._mm[off: off + n] = new.to_bytes(n, "little")

    def _note_written(self, mask) -> None:
        """Remember the bits this object wrote, to set them back to 0 at exit."""
        if not any(self._written):
            _writers.append(self)
            _install_exit_hooks()
        for i, m in enumerate(mask):
            self._written[i] |= m

    # -- output patches: (data, mask) over the RX block -----------------

    def _new_patch(self) -> tuple[bytearray, bytearray]:
        return bytearray(self._rx_size), bytearray(self._rx_size)

    @staticmethod
    def _patch_bits(patch, bit: int, nbits: int, value: int) -> None:
        """Put value into nbits bits at bit offset 'bit' of the RX block."""
        data, mask = patch
        first, last = bit // 8, (bit + nbits + 7) // 8
        shift = bit % 8
        m = ((1 << nbits) - 1) << shift
        old_d = int.from_bytes(data[first:last], "little")
        old_m = int.from_bytes(mask[first:last], "little")
        data[first:last] = ((old_d & ~m) | ((value << shift) & m)).to_bytes(last - first, "little")
        mask[first:last] = (old_m | m).to_bytes(last - first, "little")

    @staticmethod
    def _patch_bytes(patch, byte: int, raw: bytes) -> None:
        data, mask = patch
        data[byte: byte + len(raw)] = raw
        mask[byte: byte + len(raw)] = b"\xff" * len(raw)

    def _patch_field(self, patch, field_index: int, value) -> None:
        off, _, code = self._rx_spans[field_index]
        self._patch_bytes(patch, off, struct.pack("=" + code, value))

    def _patch_field_bit(self, patch, field_index: int, bit: int, value: bool) -> None:
        off, size, _ = self._rx_spans[field_index]
        if not 0 <= bit < size * 8:
            raise ValueError(f"OdyCAT: bit {bit} out of range for a {size}-byte field")
        byte = bit // 8 if sys.byteorder == "little" else size - 1 - bit // 8
        self._patch_bits(patch, (off + byte) * 8 + bit % 8, 1, 1 if value else 0)

    def _patch_entry(self, patch, entry: dict, value) -> None:
        """Encode one output entry (bit_offset from the slave block start)."""
        data, mask = patch
        bit = entry["bit_offset"] - self._tx_size * 8
        self._encode_entry(data, bit, entry, value)
        st = _entry_struct(entry)
        nbits = st.size * 8 if st is not None else entry["bits"]
        first, last = bit // 8, (bit + nbits + 7) // 8
        m = ((1 << nbits) - 1) << (bit % 8)
        mask[first:last] = (int.from_bytes(mask[first:last], "little") | m).to_bytes(
            last - first, "little")

    @staticmethod
    def _encode_entry(buf, bit: int, entry: dict, value) -> None:
        """Write value into buf at bit offset bit; only the entry's bits
        change. Raises ValueError for a value that does not fit."""
        name = entry["label"]
        bits = entry["bits"]
        st = _entry_struct(entry)
        if st is not None:
            try:
                data = st.pack(value)
            except struct.error as e:
                raise ValueError(f"OdyCAT: bad value {value!r} for '{name}': {e}") from None
            buf[bit // 8: bit // 8 + st.size] = data
            return
        v = int(value)
        if not 0 <= v < (1 << bits):
            raise ValueError(f"OdyCAT: value {value!r} out of range for {bits}-bit '{name}'")
        first, last = bit // 8, (bit + bits + 7) // 8
        shift = bit % 8
        mask = ((1 << bits) - 1) << shift
        old = int.from_bytes(buf[first:last], "little")
        new = (old & ~mask) | (v << shift)
        buf[first:last] = new.to_bytes(last - first, "little")

    def _apply_patch(self, patch) -> None:
        """Write a patch and publish it (see _publish_group)."""
        data, mask = patch
        if not any(mask):
            return
        errors = _publish_group([(self, data, mask)])
        if errors:
            raise errors[0]

    def _release_outputs(self) -> None:
        """Set every output bit this object wrote back to 0 (no link check:
        if the slave is out of OP the engine sends zeros anyway)."""
        if self._mm is None or self._keep_on_exit or not any(self._written):
            return
        mask = self._release_mask()
        if any(mask):
            _publish_group([(self, bytes(self._rx_size), mask)], check_link=False)
        self._written = bytearray(self._rx_size)

    def _release_mask(self) -> bytes:
        """The output bits _release_outputs() sets back to 0: by default
        every bit this object wrote. A subclass leaves out outputs where 0
        may not be a safe value."""
        return bytes(self._written)

    def keep_outputs_on_exit(self, keep: bool = True) -> None:
        """Leave this device's outputs as they are when the script ends
        (default: set back to 0), e.g. for a tool that switches an output on
        and exits."""
        self._keep_on_exit = keep

    # ----------------------------------------------------------------
    #  Public primitives — immediate mode
    # ----------------------------------------------------------------

    def read_txpdo(self) -> tuple:
        """Read all TX PDO fields (seqlock-protected)."""
        return struct.unpack(self._tx_fmt, self._read_txpdo_raw())

    def read_rxpdo(self) -> tuple:
        """Read current RX PDO fields from shared memory."""
        return struct.unpack(self._rx_fmt, self._read_rxpdo_raw())

    def write_rxpdo(self, *values) -> None:
        """Write all RX PDO fields immediately."""
        patch = self._new_patch()
        self._patch_bytes(patch, 0, struct.pack(self._rx_fmt, *values))
        self._apply_patch(patch)

    def set_rxpdo_field(self, field_index: int, value) -> None:
        """Write a single field of the RX PDO (immediate); other fields are
        not touched."""
        patch = self._new_patch()
        self._patch_field(patch, field_index, value)
        self._apply_patch(patch)

    def set_rxpdo_bit(self, field_index: int, bit: int, value: bool) -> None:
        """Set or clear a single bit inside an RxPDO field (immediate); no
        other bit is touched."""
        patch = self._new_patch()
        self._patch_field_bit(patch, field_index, bit, value)
        self._apply_patch(patch)

    def txpdo_bit(self, field_index: int, bit: int) -> bool:
        """A single bit of a TxPDO field."""
        return bool((self.read_txpdo()[field_index] >> bit) & 1)

    def rxpdo_bit(self, field_index: int, bit: int) -> bool:
        """A single bit of an RxPDO field, as written."""
        return bool((self.read_rxpdo()[field_index] >> bit) & 1)

    # ----------------------------------------------------------------
    #  Entry access by config label — immediate mode
    # ----------------------------------------------------------------

    def entries(self) -> tuple[list[dict], list[dict]]:
        """(inputs, outputs) entry layouts of this slave, from the config.

        Each entry has label, index, subindex, bits, encoding and
        bit_offset (from the start of this slave's shm block).
        """
        if self._entries is None:
            sc = slave_config(self._position, self._bus_name,
                              alias=self._alias, label=self._label)
            self._entries = slave_entry_layout(sc)
        return self._entries

    def _object_map(self) -> dict:
        """{(index, subindex): (entry, is_output)} of the mapped objects; the
        first mapping of an object wins."""
        if self._objects is None:
            self._objects = {}
            inputs, outputs = self.entries()
            for entries, is_output in ((inputs, False), (outputs, True)):
                for e in entries:
                    key = (int(e["index"], 16), e["subindex"])
                    self._objects.setdefault(key, (e, is_output))
        return self._objects

    def _read_object(self, index: int, subindex: int = 0):
        """Read a mapped object (input or output) by its absolute index."""
        found = self._object_map().get((index, subindex))
        if found is None:
            raise ObjectNotMappedError(
                f"{type(self).__name__} '{self._slave_label}': object "
                f"0x{index:04X}:{subindex:02X} is not in this slave's PDO map. "
                f"Add it to the config \"txpdo\" or \"rxpdo\".")
        return self._read_field(*found)

    def _find_entry(self, name: str) -> tuple[dict, bool]:
        """Entry by label, or by "0xINDEX:SUB". Returns (entry, is_output)."""
        inputs, outputs = self.entries()
        matches = [(e, False) for e in inputs if e["label"] == name]
        matches += [(e, True) for e in outputs if e["label"] == name]
        if not matches and ":" in name:
            idx, sub = name.split(":", 1)
            try:
                key = (int(idx, 0), int(sub, 0))
            except ValueError:
                key = None
            matches = [(e, out) for es, out in ((inputs, False), (outputs, True))
                       for e in es if (int(e["index"], 16), e["subindex"]) == key]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError(f"OdyCAT: entry '{name}' is ambiguous ({len(matches)} matches)")
        raise KeyError(f"OdyCAT: no entry '{name}' (available: "
                       f"{', '.join(e['label'] for e in inputs + outputs)})")

    def read_entry(self, name: str):
        """Read one PDO entry (input or output) by label or "0xINDEX:SUB"."""
        return self._read_field(*self._find_entry(name))

    def write_entry(self, name: str, value) -> None:
        """Write one output entry by label or "0xINDEX:SUB" (immediate).
        Only the bits of that entry change."""
        entry, is_output = self._find_entry(name)
        if not is_output:
            raise ValueError(f"OdyCAT: '{name}' is an input; only outputs can be written")
        self._write_field(entry, value)

    def _write_field(self, entry: dict, value) -> None:
        """Write one output entry from entries() (immediate)."""
        patch = self._new_patch()
        self._patch_entry(patch, entry, value)
        self._apply_patch(patch)

    def write_entry_bit(self, name: str, bit: int, value: bool) -> None:
        """Set or clear one bit of an output entry (immediate); no other bit
        changes, so scripts sharing the entry do not undo each other."""
        entry, is_output = self._find_entry(name)
        if not is_output:
            raise ValueError(f"OdyCAT: '{name}' is an input; only outputs can be written")
        self._write_field_bit(entry, bit, value)

    def _write_field_bit(self, entry: dict, bit: int, value: bool) -> None:
        """One bit of an output entry from entries(); entries are
        little-endian, so bit k sits k bits after the entry start."""
        if not 0 <= bit < entry["bits"]:
            raise ValueError(f"OdyCAT: bit {bit} out of range for "
                             f"{entry['bits']}-bit '{entry['label']}'")
        patch = self._new_patch()
        self._patch_bits(patch, entry["bit_offset"] - self._tx_size * 8 + bit,
                         1, 1 if value else 0)
        self._apply_patch(patch)

    def stage_entry(self, name: str, value) -> None:
        """Stage one output entry by label or "0xINDEX:SUB" for
        Device.transmit_outputs() (see deferred mode below)."""
        entry, is_output = self._find_entry(name)
        if not is_output:
            raise ValueError(f"OdyCAT: '{name}' is an input; only outputs can be written")
        self._stage_field(entry, value)

    def _read_field(self, entry: dict, is_output: bool):
        """Decode one entry from entries(); inputs via the seqlock."""
        if is_output:
            self._check_link()
            return decode_entry(self._published_outputs(), entry, -self._tx_size * 8)
        return decode_entry(self._read_txpdo_raw(), entry)

    def _staged_patch(self) -> tuple[bytearray, bytearray]:
        if self._staged is None:
            self._staged = self._new_patch()
            if self not in _staged_devices:
                _staged_devices.append(self)
        return self._staged

    def _stage_field(self, entry: dict, value) -> None:
        """Encode one output entry into the staged patch."""
        self._patch_entry(self._staged_patch(), entry, value)

    # ----------------------------------------------------------------
    #  Deferred (staged) write mode — for multi-axis sync
    # ----------------------------------------------------------------

    def stage_rxpdo(self, *values) -> None:
        """Stage all RX PDO fields for deferred transmission."""
        self._patch_bytes(self._staged_patch(), 0, struct.pack(self._rx_fmt, *values))

    def stage_rxpdo_field(self, field_index: int, value) -> None:
        """Stage a single field change."""
        self._patch_field(self._staged_patch(), field_index, value)

    def stage_rxpdo_bit(self, field_index: int, bit: int, value: bool) -> None:
        """Stage a single bit change."""
        self._patch_field_bit(self._staged_patch(), field_index, bit, value)

    @classmethod
    def transmit_outputs(cls) -> None:
        """Publish all staged RX PDO writes.

        The staged devices of one sync group ("sync_group" in the config)
        are published together and go out in the same bus cycle; devices
        in different groups each go out in the first cycle after their
        group is published. Only the staged fields/bits are written. If a
        device raises DeviceLostError, the other devices are still written
        and the first error is raised at the end.
        """
        devices = list(_staged_devices)
        _staged_devices.clear()
        groups: dict[tuple[int, int], list] = {}
        for dev in devices:
            patch, dev._staged = dev._staged, None
            if patch is None or not any(patch[1]):
                continue
            groups.setdefault((dev._inode, dev._group), []).append((dev, *patch))
        errors = []
        for items in groups.values():
            errors += _publish_group(items)
        if errors:
            raise errors[0]

    # ----------------------------------------------------------------
    #  Bus cycles
    # ----------------------------------------------------------------

    def wait_cycles(self, n: int = 1, timeout_s: Optional[float] = None) -> int:
        """Block until the engine has run n more bus cycles (see
        Bus.wait_cycles): 2 = outputs written before the call were sent,
        3 = the frame carrying them came back. Returns the cycle count."""
        return _wait_cycles(self._mm, n, self._cycle_s, timeout_s)

    # ----------------------------------------------------------------
    #  Lifecycle
    # ----------------------------------------------------------------

    def close(self):
        """Set this device's written outputs back to 0 (unless
        keep_outputs_on_exit()) and unmap the shared memory."""
        if self._mm:
            try:
                self._release_outputs()
            finally:
                if self in _writers:
                    _writers.remove(self)
                self._mm.close()
                self._mm = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __del__(self):
        self.close()
