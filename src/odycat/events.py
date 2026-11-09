# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/events.py
"""
Events — call Python functions when PDO values change.

Built on the engine's trace ring (see odycat.trace): every cycle is
recorded, so no change is missed, however short, as long as the script
keeps up within the trace history (10 s by default).

Handlers run where the script waits, like a PLC scan: write your loop with
ev.sleep() / ev.wait_cycles() instead of time.sleep() / bus.wait_cycles(),
and the handlers run inside that wait, in your thread, one after another,
never in the middle of your own code. No threads, no races::

    while True:
        ...                     # your logic
        ev.sleep(0.1)           # "end of scan": handlers run here

A script that only reacts to events calls ev.run() instead. With several
buses (one Events each), MultiPoll(ev_a, ev_b).sleep() waits on all of them.
Example::

    io = EC3A_IO1632(alias=40)
    drive = ECT60(alias=60)
    ev = Events()

    # pass a device variable: its name without () (io.in00() would be
    # the value now, io.in00 is the input itself)
    @ev.rising(io.in00)
    def pressed(e):
        io.set_out00(True)

    @ev.change(drive.position_actual_value, deadband=100)
    def moved(e):
        print(e.cycle, e.old, "->", e.value)

    @ev.when(drive.velocity_actual_value, lambda v: v > 5000, hold=10)
    def overspeed(e):
        drive.disable()

    # a slave without a device class: name the slave and the entry label
    @ev.rising(dev, "inputs", bit=3)
    def other(e): ...

    while not ev.stopped:       # until a handler calls ev.stop()
        ev.wait_cycles(100)     # handlers run inside the wait

See examples/events_demo.py for a complete script.

Semantics:
  - Nothing fires for the values seen when watching starts: an edge needs a
    change after that.
  - While a slave is out of OP its watches are paused; when it is back they
    restart from its current values (no edge for what changed meanwhile).
    lost()/back() handlers report the drop and the return.
  - debounce=N: a new value counts only once it held N cycles in a row.
  - Handler errors: on_error="raise" (default) lets the exception out of
    the wait (sleep, wait_cycles, poll, run), in your thread; "log" prints
    it and goes on; "stop" prints it and calls ev.stop(). Events not
    dispatched yet stay queued for the next wait.
  - If handlers are registered but nothing dispatches them for warn_after_s
    (5 s), a message says so, once.
  - Dispatching from a second thread, or waiting for events inside a
    handler, raises RuntimeError.
  - Falling more than the trace history behind is reported, never skipped
    quietly: overrun() handlers get it, otherwise poll() raises
    TraceOverrunError. Watches restart from current values afterwards.

Events are not interrupts: a handler runs within poll_s (5 ms) of the
cycle that triggered it while the script waits, later if the script is busy
elsewhere. A reaction within one or two cycles belongs in the slave.
"""

import inspect
import sys
import threading
import time
import traceback
from collections import deque
from typing import Callable, NamedTuple, Optional

from odycat.signal import Signal
from odycat.trace import EngineEvent, Field, FieldRef, SlaveRef, Trace

ON_ERROR = ("raise", "log", "stop")


class TraceOverrunError(RuntimeError):
    """The script fell more than the trace history behind: cycles were lost."""

    def __init__(self, overrun: "Overrun"):
        super().__init__(f"OdyCAT events: {overrun.cycles} cycle(s) and "
                         f"{overrun.engine_events} engine event(s) lost: the script "
                         f"fell more than the trace history behind")
        self.overrun = overrun


class Event:
    """What a handler receives.

    value / old: the new and previous value (for lost/back: in OP or not).
    cycle, time_ns (CLOCK_MONOTONIC), realtime_ns: when it happened.
    slave: slave label; field: the watched entry (None for lost/back).
    """

    __slots__ = ("value", "old", "cycle", "time_ns", "realtime_ns", "slave", "field")

    def __init__(self, value, old, slot, slave: str, field: Optional[Field]):
        self.value = value
        self.old = old
        self.cycle = slot.cycle
        self.time_ns = slot.time_ns
        self.realtime_ns = slot.realtime_ns
        self.slave = slave
        self.field = field

    def __repr__(self) -> str:
        what = repr(self.field) if self.field else self.slave
        return f"Event({what}, {self.old!r} -> {self.value!r}, cycle={self.cycle})"


class Overrun(NamedTuple):
    cycles: int           # cycles no longer in the trace when read
    engine_events: int    # engine events no longer in their ring
    cycle: int            # first cycle read after the gap (0 if none)


class _Handler:
    def __init__(self, func: Callable, on_error: str):
        if on_error not in ON_ERROR:
            raise ValueError(f"on_error must be one of {', '.join(ON_ERROR)}")
        self.func = func
        self.on_error = on_error
        try:
            params = [p for p in inspect.signature(func).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                      and p.default is p.empty]
            self.takes_arg = len(params) > 0
        except (TypeError, ValueError):       # builtins without a signature
            self.takes_arg = True

    def __call__(self, arg) -> None:
        if self.takes_arg:
            self.func(arg)
        else:
            self.func()


class _Watch:
    """One value watch: change, rising, falling or when."""

    def __init__(self, kind: str, field: Field, handler: _Handler, *,
                 deadband=0, debounce: int = 0,
                 predicate: Optional[Callable] = None, hold: int = 1):
        self.kind = kind
        self.field = field
        self.handler = handler
        self.deadband = deadband
        self.debounce = max(1, int(debounce))
        self.predicate = predicate
        self.hold = max(1, int(hold))
        self.active = False       # False: (re)take the baseline at the next slot
        self.raw = b""
        self.stable = None        # accepted value
        self.reported = None      # last value a change() handler got (deadband)
        self.cand = None          # debounce candidate and how long it held
        self.cand_count = 0
        self.true_count = 0       # when(): cycles the predicate held
        self.fired = False        # when(): fired for the current true period
        self.busy = False         # re-evaluate even if the bytes did not change

    def baseline(self, raw: bytes, value) -> None:
        self.active = True
        self.raw = raw
        self.stable = self.reported = value
        self.cand, self.cand_count = None, 0
        self.busy = False
        if self.kind == "when":
            self.fired = bool(self.predicate(value))
            self.true_count = self.hold if self.fired else 0

    def step(self, slot) -> Optional[Event]:
        data = slot.data
        raw = data[self.field.first:self.field.end]
        if not self.active:
            self.baseline(raw, self.field.decode(data))
            return None
        if raw == self.raw and not self.busy:
            return None
        self.raw = raw
        value = self.field.decode(data)
        old = self.stable

        # debounce: a new value is accepted once it held self.debounce cycles
        if value != self.stable:
            if self.debounce > 1:
                if value != self.cand:
                    self.cand, self.cand_count = value, 1
                else:
                    self.cand_count += 1
                if self.cand_count < self.debounce:
                    self.busy = True
                    return None
            self.stable = value
        self.cand, self.cand_count = None, 0
        self.busy = False

        kind = self.kind
        if kind == "when":
            if not self.predicate(self.stable):
                self.true_count, self.fired = 0, False
                return None
            if self.fired:
                return None
            self.true_count += 1
            if self.true_count < self.hold:
                self.busy = True
                return None
            self.fired = True
            return Event(self.stable, old, slot, self.field.slave_label, self.field)
        if self.stable == old:
            return None
        if kind == "rising":
            return Event(self.stable, old, slot, self.field.slave_label, self.field) \
                if not old and self.stable else None
        if kind == "falling":
            return Event(self.stable, old, slot, self.field.slave_label, self.field) \
                if old and not self.stable else None
        # change
        if self.deadband and abs(self.stable - self.reported) <= self.deadband:
            return None
        e = Event(self.stable, self.reported, slot, self.field.slave_label, self.field)
        self.reported = self.stable
        return e


class Events:
    """Dispatch PDO changes and engine events to handlers.

    Args:
        bus: the bus name, the config's "bus". If None, the one engine running.
        poll_s: sleep between polls in run(): the reaction latency.
        start: first cycle to process; its values are the baseline, and
               engine events from that cycle on are dispatched. None
               (default) = from now on; Trace.oldest = also what is still
               in the history.
    """

    def __init__(self, bus: Optional[str] = None, poll_s: float = 0.005,
                 start: Optional[int] = None, warn_after_s: Optional[float] = 5.0):
        self.trace = Trace(bus)
        self.poll_s = poll_s
        self._after = self.trace.head if start is None else max(0, start - 1)
        # engine events: from now on, or those of cycle start onwards
        self._event_after = self.trace.event_head if start is None else 0
        self._event_min_cycle = 0 if start is None or start <= 1 else start
        self._watches: list[_Watch] = []
        # slave index -> [watches, first byte, end byte, [last bytes, re-evaluate]]
        self._groups: dict[int, list] = {}
        self._slaves: dict[int, list] = {}     # index -> [in_op or None, lost, back]
        self._engine: list[tuple[Optional[set], _Handler]] = []
        self._overrun: list[_Handler] = []
        self._pending: deque = deque()         # (handler, argument), in order
        self._running = False
        self._stop_batch = False
        self._stopped = False
        self._handlers = 0                     # registered, for the warning
        self._polled = False                   # dispatched at least once
        self._owner: Optional[int] = None      # the one thread that dispatches
        self._in_poll = False
        self._bus = None                       # for wait_cycles(), opened on first use
        # Handlers only run when the script dispatches them: say so, once, if
        # it never does. The timer only reads two fields and prints.
        self._warn_timer = None
        if warn_after_s:
            self._warn_timer = threading.Timer(warn_after_s, self._warn_not_dispatched,
                                               (warn_after_s,))
            self._warn_timer.daemon = True
            self._warn_timer.start()

    def _warn_not_dispatched(self, after_s: float) -> None:
        if self._handlers and not self._polled:
            print(f"OdyCAT events: {self._handlers} handler(s) registered, but "
                  f"nothing has dispatched them in {after_s:g} s. They run inside "
                  f"ev.sleep(s) / ev.wait_cycles(n): use those in your loop instead "
                  f"of time.sleep() / bus.wait_cycles() (or call ev.poll() / "
                  f"ev.run()).", file=sys.stderr, flush=True)

    # ----------------------------------------------------------------
    #  Registration (decorators)
    # ----------------------------------------------------------------

    def _field(self, what: str, target, entry, bit) -> Field:
        """The watched field: a device variable (io.in03), or a slave and
        an entry label. A value passed by mistake (io.in03()) is refused."""
        if entry is None and not isinstance(target, Signal) and \
                isinstance(target, (bool, int, float)):
            raise TypeError(
                f"ev.{what}() got the value {target!r}, not a variable: pass the "
                f"variable without (), e.g. ev.{what}(io.in03) or "
                f"ev.{what}(servo.position_actual_value)")
        return self.trace.field(target, entry, bit)

    def _watch(self, kind: str, target: FieldRef, entry: Optional[str], bit,
               on_error: str, **kw):
        field = self._field(kind, target, entry, bit)
        if kind == "change" and kw.get("deadband") and field.bit is None and \
                not isinstance(field.decode(bytes(self.trace.data_size)), (int, float)):
            raise TypeError(f"deadband needs a numeric entry, not {field!r}")

        def register(func: Callable) -> Callable:
            w = _Watch(kind, field, _Handler(func, on_error), **kw)
            self._watches.append(w)
            self._handlers += 1
            self._slave_state(field.slave_index)
            group = self._groups.setdefault(field.slave_index, [[], field.first, field.end,
                                                                [None, True]])
            group[0].append(w)
            group[1] = min(group[1], field.first)
            group[2] = max(group[2], field.end)
            group[3][1] = True                       # evaluate at the next slot
            return func
        return register

    # Every decorator takes a device variable, ev.rising(io.in03), or a
    # slave and an entry label, ev.rising(io, "inputs", bit=3).

    def change(self, target: FieldRef, entry: Optional[str] = None, *,
               bit: Optional[int] = None, deadband=0, debounce: int = 0,
               on_error: str = "raise"):
        """Call the handler when the value changes (by more than deadband)."""
        return self._watch("change", target, entry, bit, on_error,
                           deadband=deadband, debounce=debounce)

    def rising(self, target: FieldRef, entry: Optional[str] = None, *,
               bit: Optional[int] = None, debounce: int = 0, on_error: str = "raise"):
        """Call the handler when the value goes from false/0 to true/non-zero."""
        return self._watch("rising", target, entry, bit, on_error, debounce=debounce)

    def falling(self, target: FieldRef, entry: Optional[str] = None, *,
                bit: Optional[int] = None, debounce: int = 0, on_error: str = "raise"):
        """Call the handler when the value goes from true/non-zero to false/0."""
        return self._watch("falling", target, entry, bit, on_error, debounce=debounce)

    def when(self, target: FieldRef, entry=None, predicate: Optional[Callable] = None, *,
             bit: Optional[int] = None, hold: int = 1, debounce: int = 0,
             on_error: str = "raise"):
        """Call the handler when predicate(value) becomes true and stays
        true for hold cycles; again only after it was false:
        ev.when(servo.position_actual_value, lambda v: v > 1000) or
        ev.when(servo, "position_actual_value", lambda v: v > 1000)."""
        if predicate is None and callable(entry):        # when(variable, predicate)
            entry, predicate = None, entry
        if predicate is None:
            raise TypeError("ev.when() needs a condition, e.g. lambda v: v > 1000")
        return self._watch("when", target, entry, bit, on_error, predicate=predicate,
                           hold=hold, debounce=debounce)

    def _slave_state(self, index: int) -> list:
        return self._slaves.setdefault(index, [None, [], []])

    def lost(self, slave: SlaveRef, *, on_error: str = "raise"):
        """Call the handler when the slave leaves OP (its outputs were zeroed)."""
        index = self.trace.slave_index(slave)

        def register(func: Callable) -> Callable:
            self._slave_state(index)[1].append(_Handler(func, on_error))
            self._handlers += 1
            return func
        return register

    def back(self, slave: SlaveRef, *, on_error: str = "raise"):
        """Call the handler when the slave is back in OP."""
        index = self.trace.slave_index(slave)

        def register(func: Callable) -> Callable:
            self._slave_state(index)[2].append(_Handler(func, on_error))
            self._handlers += 1
            return func
        return register

    def engine(self, *types: int, on_error: str = "raise"):
        """Call the handler with each engine event (odycat.trace.EngineEvent),
        or only those of the given types (trace.SLAVE_LOST, trace.WKC_WRONG...)."""
        wanted = set(types) or None

        def register(func: Callable) -> Callable:
            self._engine.append((wanted, _Handler(func, on_error)))
            self._handlers += 1
            return func
        return register

    def overrun(self, *, on_error: str = "raise"):
        """Call the handler with an Overrun when cycles were lost, instead of
        raising TraceOverrunError."""
        def register(func: Callable) -> Callable:
            self._overrun.append(_Handler(func, on_error))
            self._handlers += 1
            return func
        return register

    # ----------------------------------------------------------------
    #  Dispatch
    # ----------------------------------------------------------------

    def _collect(self) -> None:
        """Read new slots and engine events into the pending queue, in
        cycle order (engine events first within a cycle)."""
        slots, lost = self.trace.read(self._after, limit=self.trace.slot_count)
        events, events_lost = self.trace.events(self._event_after)
        if events:
            self._event_after = events[-1].seq
        items = []                                   # (cycle, order, handler, arg)
        if lost or events_lost:
            first = slots[0].cycle if slots else 0
            ov = Overrun(lost, events_lost, first)
            for w in self._watches:
                w.active = False                     # restart from current values
            for group in self._groups.values():
                group[3][1] = True
            for state in self._slaves.values():
                state[0] = None
            handlers = self._overrun or [None]
            items += [(first, 0, h, ov) for h in handlers]
        for e in events:
            if e.cycle < self._event_min_cycle:
                continue
            for wanted, h in self._engine:
                if wanted is None or e.type in wanted:
                    items.append((e.cycle, 1, h, e))
        for slot in slots:
            items += self._step(slot)
            self._after = slot.cycle
        items.sort(key=lambda it: (it[0], it[1]))
        self._pending.extend((h, arg) for _, _, h, arg in items)

    def _step(self, slot) -> list:
        items = []
        for index, state in self._slaves.items():
            in_op = slot.in_op(index)
            if state[0] is not None and in_op != state[0]:
                label = self.trace.slaves[index]["label"]
                e = Event(in_op, state[0], slot, label, None)
                items += [(slot.cycle, 2, h, e) for h in state[2 if in_op else 1]]
            state[0] = in_op
        data = slot.data
        for index, group in self._groups.items():
            watches, first, end, state = group
            if not slot.in_op(index):
                for w in watches:
                    w.active = False                 # paused; baseline when back
                state[1] = True
                continue
            raw = data[first:end]                    # every byte this slave's watches use
            if raw == state[0] and not state[1]:
                continue
            state[0] = raw
            again = False
            for w in watches:
                e = w.step(slot)
                if e is not None:
                    items.append((slot.cycle, 2, w.handler, e))
                again = again or w.busy or not w.active
            state[1] = again
        return items

    def _dispatch(self, handler: Optional[_Handler], arg) -> None:
        if handler is None:                          # overrun nobody handles
            raise TraceOverrunError(arg)
        try:
            handler(arg)
        except Exception:
            if handler.on_error == "raise":
                raise
            name = getattr(handler.func, "__qualname__", repr(handler.func))
            print(f"OdyCAT events: handler {name} failed for {arg!r}:",
                  file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            if handler.on_error == "stop":
                self.stop()

    def _enter(self, what: str) -> None:
        """Dispatch happens in one thread, never from inside a handler."""
        me = threading.get_ident()
        if self._owner is None:
            self._owner = me
        elif self._owner != me:
            raise RuntimeError(
                f"OdyCAT events: ev.{what}() called from a second thread; events are "
                f"dispatched by the thread that first did it. Use ev.sleep() / "
                f"ev.wait_cycles() in your main loop and no extra thread.")
        if self._in_poll:
            raise RuntimeError(
                f"OdyCAT events: ev.{what}() called from inside a handler. A handler "
                f"cannot wait for other events; to wait inside one, use "
                f"time.sleep() or bus.wait_cycles().")

    def poll(self) -> int:
        """Dispatch everything that happened since the last poll. Returns the
        number of handler calls (including failed ones). stop() ends the
        batch early; the rest stays queued for the next poll()."""
        self._enter("poll")
        self._polled = True
        self._in_poll = True
        try:
            self._stop_batch = False
            if not self._pending:
                self._collect()
            n = 0
            while self._pending and not self._stop_batch:
                handler, arg = self._pending.popleft()
                n += 1
                self._dispatch(handler, arg)
            return n
        finally:
            self._in_poll = False

    def sleep(self, seconds: float) -> None:
        """time.sleep() that dispatches events while waiting: handlers run
        within poll_s of what triggered them, in this thread, never in the
        middle of your own code. Handler exceptions come out of here."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(self.poll_s, remaining))

    def wait_cycles(self, n: int = 1, timeout_s: Optional[float] = None) -> int:
        """Bus.wait_cycles() that dispatches events while waiting: blocks
        until the engine has run n more cycles (same guarantees: 2 = outputs
        written before the call were sent, 3 = their frame came back) and
        returns the cycle count. Raises RuntimeError if the engine does not
        advance within timeout_s (default 10x the expected time, at least 1 s)."""
        from odycat.bus import Bus
        if self._bus is None:
            self._bus = Bus(self.trace._bus_name)
        cycle_s = self.trace.cycle_us / 1e6
        start = self._bus.cycle_count
        if timeout_s is None:
            timeout_s = max(1.0, 10 * n * cycle_s)
        deadline = time.monotonic() + timeout_s
        while True:
            self.poll()
            now = self._bus.cycle_count
            remaining = n - (now - start)
            if remaining <= 0:
                return now
            t = time.monotonic()
            if t > deadline:
                raise RuntimeError(f"OdyCAT: engine did not complete {n} cycle(s) "
                                   f"within {timeout_s:.3g} s (stopped or stalled?)")
            time.sleep(min(self.poll_s, max(cycle_s / 2, (remaining - 1) * cycle_s),
                           deadline - t))

    def run(self) -> None:
        """Dispatch until stop() is called (by a handler or another thread)
        or the script is interrupted: for scripts that only react to events."""
        self._running = True
        self._stopped = False
        while self._running:
            self.poll()
            if self._running:
                time.sleep(self.poll_s)

    def stop(self) -> None:
        """Make run() return after the current handler, and set stopped, for
        loops written as `while not ev.stopped: ... ev.sleep(0.1)`."""
        self._running = False
        self._stop_batch = True
        self._stopped = True

    @property
    def stopped(self) -> bool:
        """True once stop() was called (a handler, or on_error="stop")."""
        return self._stopped

    @property
    def cycle(self) -> int:
        """Last cycle processed."""
        return self._after

    def close(self) -> None:
        if self._warn_timer is not None:
            self._warn_timer.cancel()
        if self._bus is not None:
            self._bus.close()
        self.trace.close()


class MultiPoll:
    """Several Events (one per bus) waited on as one::

        ev_motion = Events(bus="motion")
        ev_io = Events(bus="io")
        ev = MultiPoll(ev_motion, ev_io)
        while not ev.stopped:
            ...
            ev.sleep(0.01)          # handlers of both buses run inside the wait

    Handlers run in this thread, bus by bus in the order given. There is no
    wait_cycles(): the buses' cycles are unrelated.
    """

    def __init__(self, *events: Events):
        if not events:
            raise TypeError("MultiPoll needs at least one Events")
        for ev in events:
            if not isinstance(ev, Events):
                raise TypeError(f"MultiPoll takes Events objects, not {ev!r}")
        if len({id(ev) for ev in events}) != len(events):
            raise ValueError("MultiPoll: the same Events was given twice")
        self.events = events
        self.poll_s = min(ev.poll_s for ev in events)
        self._in_poll = False

    def poll(self) -> int:
        """Dispatch what happened on every bus since the last poll; returns
        the number of handler calls. A handler exception comes out here; the
        buses after it are dispatched on the next call."""
        if self._in_poll:
            raise RuntimeError("OdyCAT events: MultiPoll waited on from inside a handler. "
                               "A handler cannot wait for other events; to wait inside "
                               "one, use time.sleep() or bus.wait_cycles().")
        self._in_poll = True
        try:
            return sum(ev.poll() for ev in self.events)
        finally:
            self._in_poll = False

    def sleep(self, seconds: float) -> None:
        """time.sleep() that dispatches the events of every bus while waiting."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(self.poll_s, remaining))

    def wait_cycles(self, n: int = 1, timeout_s: Optional[float] = None) -> int:
        raise TypeError("MultiPoll has no wait_cycles(): each bus counts its own cycles. "
                        "Use ev.sleep(seconds), or wait on one bus with "
                        "ev_motion.wait_cycles(n) and dispatch the others with poll().")

    def run(self) -> None:
        """Dispatch every bus until stop() is called on this or any of them."""
        for ev in self.events:
            ev._stopped = False
        while not self.stopped:
            self.poll()
            if not self.stopped:
                time.sleep(self.poll_s)

    def stop(self) -> None:
        """Stop every bus's Events (run() returns, stopped becomes true)."""
        for ev in self.events:
            ev.stop()

    @property
    def stopped(self) -> bool:
        """True once any of the Events was stopped (a handler, on_error="stop")."""
        return any(ev.stopped for ev in self.events)


__all__ = ["Events", "MultiPoll", "Event", "Overrun", "TraceOverrunError", "EngineEvent"]
