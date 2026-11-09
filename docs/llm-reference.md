# OdyCAT — reference for AI coding assistants

Self-contained summary for writing code that **uses** the `odycat` library.
Copy this file into a project (or reference it from its `AGENTS.md` /
`CLAUDE.md`). Full docs: `docs/` in the OdyCAT repository.

## What it is

- `odycat` (C, real time) runs the IgH EtherCAT master, cycles the bus
  every `cycle_us`, and mirrors every slave's process data into POSIX shared
  memory `/run/odycat/<bus>_shared` (run directory: `$ODYCAT_RUN_DIR`,
  else `/run/odycat`). It copies its config JSON into
  `/run/odycat/<bus>_config`.
- The Python package `odycat` maps that memory. Reads use shared memory;
  output writers use short locks to coordinate with other writers.
- The engine is started separately (`sudo odycat config.json`). Python
  cannot start/stop it, change bus state, or read/write SDOs at runtime.
- The bus topology, PDO mapping and startup SDOs live **only** in the engine's
  `config.json`. Python discovers everything from it. Changing a mapping =
  edit the config + restart the engine.

## Hard rules

1. **Direction names follow EtherCAT (slave side).** `rxpdo` = slave
   **outputs**, what the slave receives (controlword, targets, digital
   outputs; RxPDO `0x16xx`). `txpdo` = slave **inputs**, what it transmits
   (statusword, actual values, digital inputs; TxPDO `0x1Axx`). An `rxpdo`
   with a `0x1Axx` index (or a `txpdo` with `0x16xx`) is rejected.
   Python can write only outputs.
2. **No runtime SDO.** An object can be read/written only if it is mapped in a
   PDO in the config. One-time parameters go in the config's `sdos` (1/2/4-byte
   values only).
3. **Outputs and exit.** When a script ends normally (also by exception,
   Ctrl+C, SIGTERM, SIGHUP) every output bit its devices wrote goes back to 0
   (`dev.keep_outputs_on_exit()` opts out). After `kill -9`, a crash or a hang
   the engine keeps sending the last values. Put devices in a safe state in
   `finally` where it matters.
4. **Engine restarts require new objects.** `Bus` and `Device` do not reconnect.
   Device access detects a stopped or stalled engine and raises
   `EngineStoppedError`; recreate the objects after a restart.
5. **Writers can share outputs.** Writes are coordinated per sync group;
   if two scripts write the same output, the last write wins.
6. **Channel numbering differs per class** (see table below). Check before
   writing loops.
7. **Reading is named after the thing, writing keeps its verb:**
   `io.in03()`, `drv.position_actual_value()`, `drv.is_fault()`;
   `io.set_out03(True)`, `drv.set_target_velocity(v)`. There are no
   `get_*` methods. Without `()` the name is the variable itself (for
   `Events`); using it as a value (`if io.in03:`) raises `TypeError`.
8. Python is not real-time, but CSP/CSV/CST from Python works: pace the loop
   with `bus.wait_cycles(1)` and compute each target from the cycle count
   (`target = path(now - t0)`), so a late iteration only repeats one target
   and the path keeps its timing. Use DC (`"dc_sync": 0`), the engine at
   real-time priority, staged writes + `Device.transmit_outputs()` for several
   axes. Profile modes (PP/PV/PT/HM) need neither DC nor a cycle-paced loop.

## Imports

The core comes from `odycat`, the rest from its module, devices from
`odycat.devices.<vendor>` (there are no device classes in `odycat` itself):

```python
from odycat import Bus, Device, Events
from odycat.events import MultiPoll, TraceOverrunError
from odycat.device import DeviceLostError, ObjectNotMappedError
from odycat.monitor import BusMonitor
from odycat._config import detect_bus, read_config, slave_config
from odycat.devices.cia402 import Servo                    # any CiA 402 drive
from odycat.devices.mdp import DigitalIO                   # any ETG.5001 digital I/O
from odycat.devices.cia401 import GenericIO                # any CiA 401 I/O
from odycat.devices.cia402.servo import OP_MODE           # and CW_*/SW_* bits, object indices
from odycat.devices.leadshine import ECT60
from odycat.devices.beckhoff import EL1809, EL2809
from odycat.devices.amsamotion import EC3A_IO1632
from odycat.devices.delta import AX516AM, AX516AN
```

In a config, a device class is named `"vendor.Model"` (`"leadshine.ECT60"`);
unqualified names are refused.

## Selecting a slave

All device constructors accept: `position=` (also first positional arg),
`alias=`, `label=`, `bus=`.

```python
Device(label="stepper_x")          # by config "label" (preferred: stable, readable)
Device(position=2)                 # config entry {"position": 2} without alias
Device(alias=60)                   # config entry {"alias": 60} (= 60:0)
Device(alias=60, position=1)       # config entry {"alias": 60, "position": 1}
```

The address must match the config entry exactly as written. `bus=` is
needed only if several engines run. Errors: `RuntimeError` (no engine / no
permission), `KeyError` (no match; message lists available slaves).

## Bus health

```python
import time
bus = Bus()
bus.is_op, bus.wkc_ok, bus.slaves_ok     # health: OP, WKC right, every slave in OP
bus.work_counter, bus.expected_wkc, bus.cycle_count, bus.last_cycle_ns

def engine_alive(bus, max_age_s=0.2):
    age = time.clock_gettime_ns(time.CLOCK_MONOTONIC) - bus.last_cycle_ns
    return bus.is_op and age < max_age_s * 1e9
```

Wait in bus cycles instead of `time.sleep()` when the wait is about the bus
(counted frames, so an engine stall makes the wait longer, never shorter):
`bus.wait_cycles(n)` / `dev.wait_cycles(n)` return the new `cycle_count`.
`wait_cycles(2)` = outputs written before were sent; `wait_cycles(3)` = the
frame carrying them came back. Raises `RuntimeError` if the engine stops.

A slave that leaves OP gets its outputs zeroed by the engine (drives come
back disabled). Every read/write on its `Device` then raises
`DeviceLostError` (a `RuntimeError`): on every call while it is out of OP,
and once per drop even if it is already back. Catch it to recover (rewrite
outputs, `enable()` drives again) or let it stop the script. `e.al_code` is
the slave's AL status code (why it refused, e.g. 0x0027 "Freerun not
supported" = needs `dc_sync`). The engine starts without a configured slave
that is missing or does not reach OP (unless run with `--strict`); such a
slave raises the same way until it has stayed in OP for 500 ms (the engine
keeps asking for OP).

Output writes change only the targeted bits under a cross-process lock, so
several scripts can share one I/O module. When a script ends (normally, by
exception, Ctrl+C, SIGTERM, SIGHUP) every output bit its Devices wrote goes
back to 0; `dev.keep_outputs_on_exit()` opts out. `kill -9` and crashes
skip this.

## Generic access by entry label (works for any slave)

Entry labels come from the config (`"label"` of each PDO entry); without a
label the name is `"0xINDEX:SUB"` (e.g. `"0x6041:0"`).

```python
dev = Device(label="io_module")
inputs, outputs = dev.entries()          # dicts: label, index, subindex, bits, type, bit_offset, block
dev.read_entry("inputs")         # int / float / bool (1-bit entries)
dev.write_entry("outputs", 0x00FF)   # outputs only; writes only this entry's bytes
dev.stage_entry("outputs", 0)    # deferred, see below
```

Low level (tuple of all fields, `struct` format derived from the config):
`read_txpdo()`, `read_rxpdo()`, `write_rxpdo(*vals)`,
`set_rxpdo_field(i, v)`, `set_rxpdo_bit(i, bit, state)`, `txpdo_bit(i, bit)`, `rxpdo_bit(i, bit)`.

## Synchronised writes across devices

```python
x.stage_object(0x607A, 1000)            # Servo object
y.stage_object(0x607A, 2000)
io.stage_entry("outputs", 1)
Device.transmit_outputs()               # writes all staged devices back-to-back
```

Staging snapshots a device's current outputs at the first stage call; the
transmit writes all of that device's output bytes, overwriting immediate writes
made in between. Process-global, not thread-safe. Near-simultaneous, not
guaranteed same-cycle.

## CiA 402 drives: `Servo`

Finds objects by index in the config mapping (entry order irrelevant).
`axis=1` addresses the second axis of a multi-axis drive (+0x800).

```python
drv = Servo(label="axis_x")
try:
    drv.set_mode("CSV")                 # needs 0x6060 out + 0x6061 in; waits for 0x6061
    drv.enable(timeout_s=2.0)           # fault reset, zero targets, 0x06 -> 0x07 -> 0x0F
    drv.set_target_velocity(1000)
    time.sleep(2)
    drv.set_target_velocity(0)
finally:
    drv.disable()                       # controlword 0x0006
```

Profile position:

```python
drv.set_mode("PP"); drv.enable()
drv.move_to(10000)                       # handshake + wait target reached (timeout_s=30)
drv.move_to(500, relative=True, wait=False)
```

Members: `enable`, `disable`, `quick_stop`, `reset_fault`, `halt(state)`,
`resume`, `set_mode(mode|int)`, `status_bit(bit)`, `set_controlword(v)`,
`set_controlword_bit`. Variables (call them for the value, pass them without
`()` to `Events`): `mode_display`, `statusword`, `controlword`,
`is_ready_to_switch_on`, `is_switched_on`, `is_operation_enabled`, `is_fault`,
`is_warning`, `is_target_reached`, `is_internal_limit_active`,
`is_following_error`, `error_code`, `position_actual_value`,
`velocity_actual_value`, `torque_actual_value`, `current_actual_value`,
`position/velocity/torque_demand_value`, `following_error_actual_value`,
`digital_inputs`, `target_position/velocity/torque` (as written). Writers:
`set_target_position/velocity/torque`,
`set_position/velocity/torque_offset`, `set_max_torque`,
`set_torque_limits(pos, neg)`, `set_torque_slope`, `set_max_current`,
`set_max_motor_speed`, `set_profile_velocity/acceleration/deceleration`,
`set_quick_stop_deceleration`, `move_to`, `trigger_new_set_point`,
`has_object(idx, sub=0)`, `read_object(idx, sub=0)`,
`write_object(idx, value, subindex=0)`, `stage_object(idx, value, subindex=0)`.

Modes: `"PP"`, `"VL"`, `"PV"`, `"PT"`/`"TQ"`, `"HM"`, `"IP"`, `"CSP"`, `"CSV"`, `"CST"`.
Units: drive units; torque/current in 0.1 % of rated.
An unmapped object raises `ObjectNotMappedError` (subclass of `KeyError`):
the fix is in the engine config, not in Python.

Minimum mapping: in `0x6041 H`; out `0x6040 H`. Add `0x6060 b`/`0x6061 b` for
`set_mode`, `0x607A i`/`0x6064 i` for position, `0x60FF i`/`0x606C i` for
velocity, `0x6071 h`/`0x6077 h` for torque, `0x603F H` for error codes.
Every slave runs in free-run unless its entry has `"dc_sync"`: the Sync0
period in ns (`0` = bus cycle; the device class adds `assign_activate`) or
the full object. CSP/CSV/CST need it (`set_mode("CSP")` refuses without it),
and then the engine must run with real-time scheduling. PP/PV/PT/HM do not.

## Vendor classes

Digital I/O classes all have: `input(n)`, `inputs()` (word); `output(n)`,
`outputs()` (as written), `set_output(n, s)`, `set_outputs(word or 16
bools)`; and per channel `input_<n>()`, `output_<n>()`, `set_output_<n>(s)`
(variables for `Events`). EC3A `inputs`/`outputs` are variables too.

Generic standard-profile I/O (no config profile; channels found by object
index): `mdp.DigitalIO` (0x6000-0x6FFF in / 0x7000-0x7FFF out) and
`cia401.GenericIO` (0x6000/0x6100/0x6120 in, 0x6200/0x6300/0x6320 out,
analog 0x6401/0x6411 by subindex): `input(n)`, `inputs()`, `output(n)`,
`outputs()`, `set_output(n, s)`, `set_outputs(...)`, `n_inputs`, `n_outputs`,
`input_variable(n)` / `output_variable(n)` for `Events`; `GenericIO` also
`analog_input(n)`, `analog_output(n)`, `set_analog_output(n, v)`,
`analog_input_variable(n)`.

| Class | Device | Channels | Extra API |
|---|---|---|---|
| `ECT60` | Leadshine stepper, `Servo` subclass, no torque modes | IN/OUT 1..16 | `digital_inputs()` (bit0 −limit, bit1 +limit, bit2 home), `negative_limit_switch()`, `positive_limit_switch()`, `home_switch()`, `input(n)`, `output(n)`, `set_output(n, s)` |
| `EL2809` | Beckhoff 16 DO | 1..16 | digital outputs |
| `EL1809` | Beckhoff 16 DI | 1..16 | digital inputs |
| `EC3A_IO1632` | AMSA 16 DI + 16 DO | 0..15 | digital inputs and outputs |
| `AX516AM` | Delta 16 DI | 0..15 | digital inputs |
| `AX516AN` | Delta 16 DO | 0..15 | digital outputs |

For any other slave use `Device` + entry labels, or subclass `Device` /
`Servo`.

## Whole-bus read-only view

```python
mon = BusMonitor()                         # never writes; re-attaches after engine restarts
lay = mon.layout()                         # None if no engine
snap = mon.snapshot()                      # snap["engine"]: running|starting|stalled|stopped
for sl, vals in zip(lay["slaves"], snap["slaves"]):
    print(sl["label"], vals["status"], dict(zip([e["label"] for e in sl["inputs"]], vals["inputs"])))
```

## Reacting to changes: `Events`

```python
from odycat import Events
from odycat import trace
ev = Events()                                   # needs history_s > 0 (default 10 s)

@ev.rising(button_in.l0)                   # the variable: no ()
def pressed(e):                                 # e.value, e.old, e.cycle
    fan_out.set_l0(True)

@ev.when(drv.position_actual_value, lambda v: v > 1000, hold=5)   # also falling, change(deadband=)
def far(e): ...

@ev.lost(fan_out)                               # also back(dev), engine(trace.WKC_WRONG), overrun()
def lost(e): ...

while True:                                     # your loop...
    ev.sleep(0.1)                               # ...waits here: handlers run inside the wait
# (ev.wait_cycles(n) for bus.wait_cycles(n); ev.run() if handlers are all the script does)
```

No change is missed (every cycle is in the trace); no edge for the initial
values; watches pause while a slave is out of OP and restart from its current
values. `on_error="raise"|"log"|"stop"`. Handlers run only inside
`ev.sleep()` / `ev.wait_cycles()` / `ev.poll()` / `ev.run()`, in the calling
thread: never call `time.sleep()` in a loop that relies on events, and never
start a thread for them. Not for reactions within 1-2 cycles.

## Config skeleton (engine side)

Devices with a class (all in "Vendor classes" except `Servo`) only need
`"device"` (`"vendor.Model"`), an address and a label; `odycat-config expand
config.json -o machine.json` fills in identity and PDOs (DC only if the entry
has `"dc_sync"`), and the engine runs `machine.json`:

```json
{ "device": "leadshine.ECT60", "alias": 60, "label": "axis_x",
  "sdos": [ { "index": "0x6083", "subindex": 0, "type": "u32", "value": 100000 } ] }
```

Any other slave is written in full:

```json
{
  "bus": "bus0", "cycle_us": 1000,
  "slaves": [
    { "alias": 60, "position": 0, "label": "axis_x",
      "vendor_id": "0x00000a88", "product_code": "0x0a880002",
      "dc_sync": { "assign_activate": "0x0300" },
      "sdos": [ { "index": "0x6083", "subindex": 0, "type": "u32", "value": 100000 } ],
      "txpdo": { "index": "0x1A00", "entries": [
        { "index": "0x6041", "subindex": 0, "type": "u16", "label": "statusword" },
        { "index": "0x6061", "subindex": 0, "type": "i8", "label": "mode_display" },
        { "index": "0x6064", "subindex": 0, "type": "i32", "label": "actual_position" } ] },
      "rxpdo": { "index": "0x1600", "entries": [
        { "index": "0x6040", "subindex": 0, "type": "u16", "label": "controlword" },
        { "index": "0x6060", "subindex": 0, "type": "i8", "label": "mode" },
        { "index": "0x607A", "subindex": 0, "type": "i32", "label": "target_position" } ] } }
  ]
}
```

Entry types: `bool u8 i8 u16 i16 u32 i32 u64 i64 f32 f64` (`"type"`, also for
SDOs), or `"bits": N` for bit fields; `"index": "0x0000"` = gap. `txpdo`/`rxpdo` are one PDO object or a
list of them (several PDOs per direction). All entries of a direction must total whole bytes.
Limits: 512 slaves, 32 PDOs and 256 entries per direction, 32 SDOs per slave,
`cycle_us` one of 125, 250, 500, 1000, 2000, 4000, 8000, 10000. SDO values
must fit their type (no truncation); hex strings (`"0x00FF"`) are accepted.

## Script template

```python
import sys, time
from odycat import Bus
from odycat.devices.cia402 import Servo
from odycat.devices.amsamotion import EC3A_IO1632

def main() -> int:
    bus = Bus()
    if not (bus.is_op and bus.wkc_ok):
        print(f"bus not ready: op={bus.is_op} wkc={bus.work_counter}/{bus.expected_wkc}")
        return 1
    drv = Servo(label="axis_x")
    io = EC3A_IO1632(label="io_module")
    try:
        drv.set_mode("PP")
        drv.enable()
        drv.move_to(10000)
        io.set_output(0, True)
    finally:
        io.set_output(0, False)
        drv.disable()
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

## Common mistakes

- Writing to a `txpdo` entry (it is an input) → `ValueError`.
- Calling `Servo.set_target_torque` on a drive without `0x6071` mapped →
  `ObjectNotMappedError`; fix the config, or the drive has no torque mode.
- Expecting `EL2809.set_output(0, ...)` to work: Beckhoff classes are 1-based.
- `Device(position=1)` for a config entry written with an alias → `KeyError`.
- Checking `last_cycle_ns`/`cycle_count` by hand to detect a dead engine:
  `bus.is_op` turns `False` when the engine stops or hangs, and device access
  raises `EngineStoppedError`.
- Forgetting `finally: drv.disable()` / outputs off.
- Trying to set parameters with SDO from Python: not possible; use the config
  `sdos` list or map the object.
