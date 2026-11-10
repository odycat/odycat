# Python API

The core is imported from `odycat`; everything else from its module, and
device classes by vendor:

```python
from odycat import Bus, Device, Events                     # the core
from odycat.events import MultiPoll, TraceOverrunError
from odycat.device import DeviceLostError, ObjectNotMappedError
from odycat.trace import Trace                             # or bus.trace()
from odycat.monitor import BusMonitor
from odycat.signal import signal, Signal                   # for device class authors
from odycat._config import detect_bus, read_config, slave_config

from odycat.devices.leadshine import ECT60                 # devices: odycat.devices.<vendor>
from odycat.devices.beckhoff import EL1809, EL2809
from odycat.devices.cia402 import Servo                    # standard profiles
```

All classes attach to an engine that is **already running**. Nothing in the
package starts, stops or configures the engine.

## Bus auto-detection

Every constructor takes the bus name: `bus=None` (`Bus(name=None)` for the
`Bus` class), the config's `"bus"`. With `None`, the package looks for
`*_config` in the run directory (`$ODYCAT_RUN_DIR`, else `/run/odycat`):

- exactly one → that engine is used;
- none → `RuntimeError("OdyCAT: no running engine detected ...")`;
- several → `RuntimeError(... "Pass bus= explicitly")`.

Pass `bus="..."` when more than one engine runs (several buses, one engine
per IgH master and network card):

```python
drv = ECT60(alias=60, bus="motion")              # motion bus
io = EC3A_IO1632(alias=40, bus="io")              # I/O bus
ev_motion = Events(bus="motion")                  # one Events per bus
ev_io = Events(bus="io")
ev = MultiPoll(ev_motion, ev_io)                  # waited on as one

while not ev.stopped:
    ...
    ev.sleep(0.01)                                # handlers of both buses run here
```

A device given to the `Events` or `Trace` of another bus raises `ValueError`
naming both buses. `MultiPoll` has `sleep()`, `poll()`, `run()`, `stop()`
and `stopped` (true once any bus's `Events` was stopped), but no
`wait_cycles()`: each bus counts its own cycles. Handlers run bus by bus, in
the order given.

If the user is not in the shm group, constructors raise `RuntimeError` with a
hint (`sudo usermod -aG odycat $USER`).

## `Bus`

Read-only view of the shm header.

```python
bus = Bus()                 # or Bus("bus0"); bus.name is the bus name
bus.is_op                   # True once the engine is in its RT loop
bus.wkc_ok                  # work_counter == expected_wkc
bus.work_counter, bus.expected_wkc
bus.cycle_count             # cycles since the engine started (64-bit, does not wrap)
bus.slaves_ok               # every configured slave online and in OP
bus.last_cycle_ns           # CLOCK_MONOTONIC ns of the last cycle
bus.wkc_errors              # cycles with a wrong WKC since the engine started
bus.wkc_error_streak        # consecutive wrong-WKC cycles right now (0 = last one right)
bus.skipped_cycles          # cycles the engine skipped (late by half a period or more)
bus.rt_scheduling           # engine runs with SCHED_FIFO
bus.memory_locked           # engine memory is locked (mlockall)
bus.engine_info             # engine version and git, config SHA-256, pid, start time, ...
bus.wait_cycles(n)          # block until the engine ran n more cycles
bus.close()
```

`Bus` (and `Device`, `BusMonitor`, `Trace`) refuse to attach to an engine of
another layout version, with a message saying which side is newer: install
the engine and the package from the same version.

`bus.engine_info` (also `odycat._config.engine_info(bus)`) tells which
engine runs and which config it loaded: `engine_version`, `engine_git`,
`config_sha256` (SHA-256 of the config file as loaded, also printed by the
engine at startup), `engine_pid`, `support_end` (YYYYMM or `None`),
`start_realtime_ns`, `instance_id` (random, new at every start) and
`layout_version`. A machine can log it to show which software and
configuration were running.

Every property reads shared memory at the time of the call. A stalled
engine can be detected by comparing `last_cycle_ns` with
`time.clock_gettime_ns(time.CLOCK_MONOTONIC)`.

Typical health check before driving anything:

```python
bus = Bus()
if not (bus.is_op and bus.wkc_ok and bus.slaves_ok):
    raise SystemExit("bus not healthy")
```

### Reacting to a wrong working counter

The engine takes no action on its own when the working counter is wrong:
what it means depends on the machine (an I/O module unplugged on purpose, or
a drive losing its set-points), so the script decides. Two things to know:

- **The fast reaction is in the slaves.** The engine turns on each slave's
  outputs watchdog: a slave that stops receiving valid frames drops its own
  outputs to their safe state, in hardware, within its watchdog time
  (around 100 ms by default). A script reacts in milliseconds at best, so it
  handles the *situation* (alarm, stop the sequence, log), not the first
  milliseconds.
- **Count, then decide.** For example, stop the sequence after 10 wrong
  cycles in a row, or when more than 100 accumulate in a minute:

```python
bus = Bus()
errors_before = bus.wkc_errors
t0 = time.monotonic()
while running:
    if bus.wkc_error_streak >= 10:
        stop_sequence("working counter wrong for 10 cycles")
    if time.monotonic() - t0 >= 60:
        if bus.wkc_errors - errors_before > 100:
            stop_sequence("too many wrong working counters in a minute")
        errors_before, t0 = bus.wkc_errors, time.monotonic()
    bus.wait_cycles(1)
```

`Events` can also call a handler on each wrong-WKC episode (engine events
`trace.WKC_WRONG` and `WKC_OK`, with the cycle and the WKC), for logging.

## `Device`

Base class of every slave class. Usable directly for any slave, using the
entry labels from the config.

### Constructor

```python
Device(position=None, *, alias=0, label=None, bus=None,
       txpdo_fmt=None, rxpdo_fmt=None)
```

Select the slave with **one** of:

| Call | Selects |
|---|---|
| `Device(position=2)` / `Device(2)` | Absolute ring position 2 (config entry without alias) |
| `Device(alias=60)` | Slave with alias 60 (`60:0`) |
| `Device(alias=60, position=1)` | `60:1` |
| `Device(label="stepper_x")` | Config entry with that label (recommended) |

The address must match a config entry exactly as it is written in the config:
`Device(position=1)` does not find an entry written as `alias: 60,
position: 1`. Errors: `KeyError` (no match, lists the available slaves),
`ValueError` (several matches).

The block offset always comes from the config layout. `txpdo_fmt` and
`rxpdo_fmt` are looked up in the config when not given. Subclasses with a fixed layout pass their own formats; the formats must
then match the config's byte layout.

### Access by entry label (recommended)

```python
dev.entries()                    # (inputs, outputs): lists of dicts with
                                 # label, index, subindex, bits, type,
                                 # bit_offset, block
dev.read_entry("statusword")     # input or output, by label
dev.read_entry("0x6041:0")       # or by "index:subindex"
dev.write_entry("controlword", 0x000F)   # outputs only, immediate
dev.stage_entry("target_position", 1000) # deferred, see below
```

- Values are decoded with the entry's `type`; bit entries are unsigned
  ints, 1-bit entries `bool`.
- `write_entry` writes only the bytes of that entry (bit entries are
  read-modify-written within their byte). Out-of-range values raise
  `ValueError`.
- Writing an input raises `ValueError`; an unknown name raises `KeyError`
  listing the available labels.

### Access by struct fields (low level)

The PDO blocks are also exposed as tuples, using the `struct` format built
from the config (or passed to the constructor). Field `n` is the n-th
non-gap byte-aligned run of entries; a run of packed bit entries shows up as
one `B` per byte.

```python
dev.read_txpdo()                  # tuple of all inputs (seqlock snapshot)
dev.read_rxpdo()                  # tuple of all outputs (current shm value)
dev.write_rxpdo(*values)          # all outputs, immediate
dev.set_rxpdo_field(i, value)     # read-modify-write one field
dev.set_rxpdo_bit(i, bit, state)  # read-modify-write one bit
dev.txpdo_bit(i, bit)             # one input bit
dev.rxpdo_bit(i, bit)             # one output bit, as written
```

### Deferred (staged) writes

Staging collects output changes in a per-device buffer; nothing reaches shared
memory until `Device.transmit_outputs()` publishes every staged device. Use
it to change several axes or devices together; put the slaves that must
change in the **same cycle** in one sync group (`"sync_group"` in the
config, see configuration.md):

```python
x.stage_object(0x607A, 10000)     # Servo
y.stage_object(0x607A, -5000)
io.stage_entry("outputs", 0x00FF)
Device.transmit_outputs()         # flush all, clear staging
```

| Method | Stages |
|---|---|
| `stage_entry(name, value)` | One entry by label |
| `stage_rxpdo(*values)` | The whole output tuple |
| `stage_rxpdo_field(i, value)` | One field |
| `stage_rxpdo_bit(i, bit, state)` | One bit |
| `Servo.stage_object(index, value, subindex=0)` | One CiA 402 object |

Notes:

- Only what was staged is written on transmit (the staged fields, entries
  or bits); `stage_rxpdo(*values)` stages every field. Other outputs of the
  device, including ones written by other scripts meanwhile, are untouched.
- If a device raises `DeviceLostError` on transmit, the other devices are
  still written and the first error is raised at the end.
- The staging registry is global to the process (class-level), not
  thread-safe.
- The staged devices of one sync group are published with a single store:
  they go out in the same cycle, guaranteed. Devices in different groups
  (every slave without `"sync_group"` is a group of its own) are published
  group after group, a few microseconds apart: usually the same cycle, not
  guaranteed.

### Several scripts on one device

Every output write changes only the bits it targets: a field
(`set_rxpdo_field`), an entry (`write_entry`, `Servo.write_object`), a bit
(`set_rxpdo_bit`, `set_output`). Writes to the slaves of one sync group are
serialised across processes by a kernel lock, held for microseconds and
released by the kernel if a process dies, so two scripts can drive
different outputs of the same I/O module without undoing each other.
Writers of different groups never wait for each other. Two scripts writing
the **same** output is allowed; the last write wins.

A write never reaches the bus half done: it fills a fresh copy of the
slave's outputs and publishes it in one step, so the engine sends the
outputs from before the write or from after it. A script killed in the
middle of a write publishes nothing. Reading an output back (`read_rxpdo`,
`read_entry`) returns what was last written, which goes out from the next
cycle on; `BusMonitor` and the trace show the outputs as sent.

### Outputs when the script ends

Each `Device` remembers which output bits it wrote. When the script ends,
they are set back to 0, so a program's commands do not outlive it (a drive
gets controlword 0 and disables; its target position and position offset
keep their last value, so a drive still enabled in that cycle does not take
0 as a setpoint). Covered: normal end, `sys.exit()`, an
uncaught exception, Ctrl+C, SIGTERM (`kill`, `systemctl stop`) and SIGHUP
(terminal closed). For the last two the library installs signal handlers
that end the script normally, unless the script installed its own.

Not covered, because the process cannot run any code: `kill -9`, an
out-of-memory kill, a crash, `os._exit()`. A script that hangs keeps its
outputs too.

Only the bits that object wrote are cleared: outputs written by other
scripts, or by other bits of a shared word, are left alone. If two scripts
wrote the same output, the one that ends clears it.

```python
dev.keep_outputs_on_exit()      # opt out, e.g. a tool that switches an output on and exits
```

### Lifecycle

`dev.close()` sets the outputs this object wrote back to 0 (unless
`keep_outputs_on_exit()`) and unmaps the shared memory. A device that wrote
outputs stays alive until the script ends, so garbage collection never
clears them early. `Bus` and `Device` objects do not reconnect after an engine
restart. Device access detects a stopped or stalled engine and raises
`EngineStoppedError`. Create new objects after a restart.

## `BusMonitor`

Whole-bus, read-only decoder, for dashboards and tools. It never writes, does
not need the engine to be running when created, and re-attaches by itself
when the engine restarts with the same or a different config.

```python
mon = BusMonitor()                 # or BusMonitor(bus="bus0")
mon.layout()      # None if no engine; else {"layout_id", "bus_name",
                  #   "cycle_us", "slaves": [{label, name, alias, position,
                  #   vendor_id, product_code, dc,
                  #   inputs: [entry...], outputs: [entry...]}]}
                  # entry: {label, index, subindex, bits, type, ...}
snap = mon.snapshot()
snap["engine"]    # "running" | "starting" | "stalled" | "stopped"
snap["bus"]       # is_op, work_counter, expected_wkc, cycle_count,
                  # wkc_mismatch, slave_lost, last_cycle_age_ms,
                  # slaves_responding, master_al_states, link_up,
                  # rt_sched_fifo, rt_memory_locked, ...
snap["slaves"][i] # {"status": {al_state, al_state_name, online, operational,
                  #   ring_pos, drop_count, al_code, al_status} or None,
                  #   "inputs": [values], "outputs": [values]}
mon.sample([(slave_idx, "inputs", entry_idx), ...])
                  # -> (layout_id, cycle_count, last_cycle_ns, [values]) or None
mon.close()
```

Values in `snapshot()["slaves"][i]["inputs"]` are in the same order as
`layout()["slaves"][i]["inputs"]`. "stalled" means no cycle for more than
500 ms. `layout_id` changes when the engine restarts.

`odycat.monitor.AL_STATE_NAMES` maps AL state codes to names.

The keys listed above are stable. `layout()` also carries offsets into
the engine's shared memory (`shm_offset`, an entry's `bit_offset`): they
are internal and change with the engine's layout.

## `Events`

Calls functions when PDO values change, from the engine's trace: every
cycle is recorded, so no change is missed, however short, as long as the
script keeps up within `history_s` (10 s by default).

Handlers run **where the script waits**, like at the end of a PLC scan: use
`ev.sleep(s)` / `ev.wait_cycles(n)` in your loop instead of `time.sleep(s)` /
`bus.wait_cycles(n)`. They wait the same way and run the handlers inside the
wait, in your thread, one after another, never in the middle of your own code:
no threads, no races, and handler exceptions come out of the wait.

```python
while True:
    if io.in05():           # your logic
        ...
    ev.sleep(0.1)              # "end of scan": handlers run here
```

`ev.wait_cycles(n)` has the same guarantees as `bus.wait_cycles(n)` and
returns the cycle count. A script that only reacts to events calls `ev.run()`;
`ev.poll()` dispatches once. If handlers are registered but nothing dispatches
them within 5 s (`Events(warn_after_s=...)`), a message says so. Calling
these from a second thread, or from inside a handler, raises `RuntimeError`.
`ev.stop()` (from a handler, or `on_error="stop"`) sets `ev.stopped` and ends
`run()`: `while not ev.stopped: ... ev.sleep(0.1)`.

Each decorator takes a **device variable**: the method's name without `()`
(see [devices.md](devices.md#reading-writing-and-variables)).

```python
ev = Events()                      # or Events(bus="bus0", poll_s=0.005)
io = EC3A_IO1632(alias=40)
drive = ECT60(alias=60)

@ev.rising(io.in00)             # the input itself, not io.in00()
def pressed(e):                    # e.value, e.old, e.cycle, e.time_ns, e.realtime_ns
    io.set_out00(True)

@ev.falling(io.in03, debounce=5)
def released():                    # a handler may take no argument
    ...

@ev.change(drive.position_actual_value, deadband=100)
def moved(e): ...

@ev.when(drive.velocity_actual_value, lambda v: v > 5000, hold=10)
def overspeed(e): ...              # once per time the condition becomes true

@ev.rising(drive.is_fault)
def fault(): ...

@ev.lost(drive)                    # slave left OP (outputs were zeroed)
def drive_lost(e): ...
@ev.back(drive)
def drive_back(e): ...

@ev.engine(trace.WKC_WRONG)        # from odycat import trace; all types if none given
def wkc(e): print(e.name, e.cycle, e.arg)

@ev.overrun()                      # without it, poll() raises TraceOverrunError
def behind(o): print(o.cycles, "cycles lost")

while not ev.stopped:
    ev.wait_cycles(100)            # handlers run inside the wait
# or ev.run() if handlers are all the script does
```

- Passing a value by mistake (`ev.rising(io.in00())`) raises `TypeError`
  saying to drop the `()`.
- For a slave without a device class, name the slave and the entry label
  instead: `ev.rising(dev, "inputs", bit=3)` (slave: `Device`, label
  or config index; entries inputs first, then outputs). `rising`/`falling`
  compare truthiness.
- Nothing fires for the values seen when watching starts (`start=` in the
  constructor processes earlier cycles still in the trace instead).
- `debounce=N`: a new value counts once it held N cycles in a row.
  `hold=N` (`when`): the condition must hold N cycles.
- While a slave is out of OP its watches are paused; when it is back they
  restart from its current values, without edges for what changed meanwhile.
- `on_error=` on every decorator: `"raise"` (default) lets the exception out
  of the wait (`sleep`, `wait_cycles`, `poll`, `run`), `"log"` prints it and
  goes on, `"stop"` prints it and calls `ev.stop()`. Events not dispatched yet
  stay queued for the next wait.
- Events are dispatched in cycle order, engine events first within a cycle.
- Latency: within `poll_s` (5 ms) while the script waits; later if it is
  busy elsewhere. Events are not interrupts; a reaction within one or two
  cycles belongs in the slave.
- Cost: about 5 % of one core for 320 watches and 8000 handler calls per
  second at 1 kHz (measured with the fake engine). Slaves whose watched bytes
  did not change are skipped.

## `Trace`

Read-only access to the trace ring, for tools that want the raw cycles.

```python
t = Trace()                        # RuntimeError if the engine runs with history_s 0
t.head, t.oldest                   # newest / oldest cycle still recorded
t.history_s, t.slot_count, t.cycle_us
slots, lost = t.read(after=cycle)  # cycles after 'cycle', oldest first; lost = no longer kept
s = t.slot(cycle)                  # one cycle, or None
s.cycle, s.time_ns, s.realtime_ns, s.work_counter, s.error_flags, s.in_op(i)
t.value(s, "io_module", "di", bit=0)   # decode an entry (or bit) of a slot
t.value(s, drive.position_actual_value)  # or t.value(s, "axis_x", "position_actual_value")
f = t.field(drive.position_actual_value); f.decode(s.data)
events, lost = t.events(after=seq) # EngineEvent(seq, cycle, realtime_ns, type, slave, arg).name
t.skipped_cycles                   # cycles skipped since the engine started
```

Outputs in a slot are what was sent on the wire that cycle.

Cycle numbers count cycles that ran. When the engine is late by half a
period or more, it skips the missed periods (a `cycles_skipped` event, with
the count, and `t.skipped_cycles`) instead of running them back to back, so
frames are always at least half a period apart.
`wait_cycles(n)` therefore always means n real exchanges with the slaves;
code that has to follow real time, such as a trajectory, should use the
slots' `time_ns`, or `cycle + t.skipped_cycles` as the number of periods
elapsed. Like `Bus`,
`Trace` and `Events` do not reconnect after an engine restart.

## Config helpers (`odycat._config`)

| Function | Returns |
|---|---|
| `detect_bus()` | The bus name of the single running engine |
| `run_dir()` | The engine's run directory: `$ODYCAT_RUN_DIR`, else `/run/odycat` |
| `engine_info(bus=None)` | Which engine runs and which config it loaded (see `Bus.engine_info`) |
| `read_config(bus=None)` | The config dict of the running engine (cached until the engine restarts) |
| `slave_config(position=None, bus=None, *, alias=0, label=None)` | One slave's config dict |
| `find_slave(cfg, position, *, alias, label)` | Same, on a config dict |
| `slave_address(slave)` | `(alias, position)` of a config entry |
| `slave_entry_layout(slave)` | `(inputs, outputs)` entry layout (bit offsets from the slave block) |
| `slave_pdos(slave, direction)` | `[(name, pdo), ...]` of one direction (`"in"`/`"out"`), in wire order |
| `slave_pdo_sizes(slave)` | `(input bytes, output bytes)` in shm |
| `direction_format(slave, direction)` | `struct` format of one direction |
| `pdo_format_from_entries(pdo)` | `struct` format of a list of entries (`{"entries": [...]}`) |
| `pdo_size_from_entries(pdo)` | Byte size of a list of entries in shm |
| `resolve_slave_shm_offset(cfg, ...)` | Byte offset of a slave block |
| `decode_entry(buf, field, bit_base=0)` | Decode one entry from a buffer |

`detect_bus`, `read_config` and `slave_config` are re-exported from
`odycat`.

## Exceptions

| Exception | When |
|---|---|
| `RuntimeError` | No engine / several engines / no permission / the engine stopped in the middle of copying the inputs / CiA 402 state or mode timeouts |
| `KeyError` | Unknown slave address/label, unknown entry name. A device variable whose object is not in the PDO map raises `ObjectNotMappedError` (a `KeyError`) |
| `TypeError` | A device variable used as a value (`if io.in03:`): call it, `io.in03()` |
| `ValueError` | Ambiguous match, value out of range, writing an input, bad channel number |
| `EngineStoppedError` (a `RuntimeError`) | Any read or write once the engine stopped, was killed, crashed or hangs (no cycle for 200 ms, or 20 cycles if longer), or before it reached OP. Restart the script once the engine runs again: a new engine makes a new region. A script cannot attach to a region left by an engine that is no longer running |
| `DeviceLostError` (a `RuntimeError`) | Any read or write on a device whose slave is out of OP, and once for every drop since that object last saw one, even if the slave is already back. See below |

### `DeviceLostError`

When a slave leaves OP (cable, power, sync error) the engine zeroes its
outputs, and IgH brings it back on its own, often within a second or two.
A script that only looked at the state when it happens to call the device
would miss a short drop and carry on with outputs it thinks are on but were
zeroed, or a drive it thinks is moving but came back disabled. So every
`Device` checks its slave's status entry on each access and raises
`DeviceLostError`:

- while the slave is out of OP, on every call;
- once for each new drop (the engine counts them), even if the slave is back.

Uncaught, it stops the script with a message naming the slave. Catch it to
recover explicitly:

```python
from odycat.device import DeviceLostError

try:
    io.set_output(8, True)
except DeviceLostError as e:
    print(e)        # OdyCAT: 'io_module' dropped out of OP 1 time(s), now back in OP; ...
    # decide: rewrite outputs, drv.enable() again, or stop the machine
```

`e.label`, `e.drops` (new drops reported, 0 while still out of OP),
`e.al_state` (`0x10` added when the slave refused a state) and `e.al_code`
(the slave's AL status code, the reason it gave, 0 if none) give the
details; the message names both, e.g. `'servo' is out of OP, in SAFEOP +
error, AL status 0x0027 (Freerun not supported)`.

A slave the engine started without (missing, or it did not reach OP; see
[getting-started.md](getting-started.md#slaves-out-of-op)) raises the same
way from the first call, with no drop counted. Once it has stayed in OP for
500 ms the calls succeed; there is nothing to recover, since it never had
outputs set.

Reads raise too, not only writes: the inputs of a slave out of OP are not
updated, and a value that looks valid but stopped changing could hide the
fault. The call that raised did nothing; repeat it
after recovering. Each `Device` object tracks drops on its own, so two
objects for the same slave each report a drop once. Drops before the object
was created are not reported.

## Timing and performance

- Each call reads or writes shared memory directly: a few µs in CPython, no
  syscalls. Values are at most one engine cycle old.
- Python is not real-time, but it can keep up with the bus. A loop with
  `time.sleep()` is fine for supervision, I/O and set-point changes. Targets
  that change every cycle (CSP/CSV/CST) are written from a loop paced with
  `wait_cycles(1)`: a late iteration only repeats the previous target for one
  cycle. See [devices.md](devices.md#cyclic-synchronous-modes-from-python-csp--csv--cst)
  for the recipe, DC, real-time priority for the script, and how to measure
  missed cycles with the trace.
- `wait_cycles(n)` (on `Bus` and on every `Device`) blocks until the engine
  has run `n` more cycles and returns the new `cycle_count`. It polls every
  half cycle, so it costs almost no CPU. Use it instead of `time.sleep()`
  when the wait is about the bus rather than about wall time: the counter
  only advances when a frame is actually sent, so if the engine stalls the
  wait gets longer instead of skipping cycles.

```python
dev.write_entry("enable", 1)
dev.wait_cycles(2)          # outputs written before the call have been sent
dev.wait_cycles(3)          # ...and the frame carrying them has come back
```

  `wait_cycles(1)` is not enough to guarantee a write was sent: it may land
  just after the engine copied the outputs of the cycle in progress. How
  long a slave takes to act on an output is up to the slave (a DC drive
  only samples at Sync0). It raises `RuntimeError` when the counter does
  not advance within `timeout_s` (default 10x the expected time, at least
  1 s), e.g. because the engine stopped. To schedule periodic work, keep a
  cycle stamp and compare with `now - stamp` (the counter is 64-bit and
  does not wrap).
