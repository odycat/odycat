# Device classes

All classes derive from `Device` (see [python-api.md](python-api.md)) and take
the same addressing arguments: `position=`, `alias=`, `label=`, `bus=`.
They read their layout from the running engine's config, so the config entry
for the slave must match what the class expects (described per class below).

Device classes are organised by vendor, and standard profiles have their
own package:

```python
from odycat.devices.leadshine import ECT60
from odycat.devices.cia402 import Servo
```

| Class | Package (`odycat.devices.…`) | Config name | Slave | Vendor / product | Support |
|---|---|---|---|---|---|
| `Servo` | `cia402` | — | Any CiA 402 drive (servo, stepper, VFD) | any | tested |
| `DigitalIO` | `mdp` | — | Any digital I/O following the Modular Device Profile (ETG.5001) | any | tested |
| `GenericIO` | `cia401` | — | Any I/O following CiA 401 (digital and analog) | any | community |
| `ECT60` | `leadshine` | `leadshine.ECT60` | Leadshine ECT60 closed-loop stepper | `0x00000A88` / `0x0A880002` | tested |
| `EL2809` | `beckhoff` | `beckhoff.EL2809` | Beckhoff EL2809, 16 DO | `0x00000002` / `0x0AF93052` | community |
| `EL1809` | `beckhoff` | `beckhoff.EL1809` | Beckhoff EL1809, 16 DI | `0x00000002` / `0x07113052` | community |
| `EC3A_IO1632` | `amsamotion` | `amsamotion.EC3A_IO1632` | AMSA Motion EC3A-IO1632, 16 DI + 16 DO | `0x00000B95` / `0x00001101` | tested |
| `AX516AM` | `delta` | `delta.AX516AM` | Delta AX-516AM20-0A, 16 DI | `0x000001DD` / `0x10512081` | tested |
| `AX516AN` | `delta` | `delta.AX516AN` | Delta AX-516AN02-0A, 16 DO | `0x000001DD` / `0x10512181` | tested |

**Support** is the class's tier: **tested** classes are tested on the real
device before each release (the [device catalogue](generated/device-catalogue.md)
lists the device revisions tested); **community** classes are maintained by a
contributor and tested by unit tests only; **unmaintained** classes have no
maintainer. See [Devices in CONTRIBUTING](../CONTRIBUTING.md#devices).
"Tested" does not test your machine or your application: test them on your
own hardware (see [intended use](intended-use.md)). `odycat-config scan` shows
the tier of each slave's class, and says when a slave's revision is not one
that was tested.

Every vendor class carries a device profile: in the config,
`{"device": "leadshine.ECT60", "alias": 60, "label": "axis_x"}` is enough
(see [configuration.md](configuration.md#device-profiles-device)). The
mappings described below are what the profiles fill in.

For any other slave, use `Device` with entry labels, or write a class (see the
end of this page).

## I/O names

A device class names each I/O point **as it is printed on the device**, in
lowercase, so the name in the script is the one on the terminal you wire.
Where the device prints only a number, the name is `input_<n>` /
`output_<n>`, as the vendor's own documentation calls them.

| Class | Printed | Names |
|---|---|---|
| `EC3A_IO1632` | IN00…IN15, OUT00…OUT15 | `in00`…`in15`, `out00`…`out15` |
| `AX516AM`, `AX516AN` | L0…L7, R0…R7 (two columns) | `l0`…`l7`, `r0`…`r7` |
| `EL1809`, `EL2809` | 1…16 | `input_1`…`input_16`, `output_1`…`output_16` |
| `ECT60` (I/O connector) | IN3…IN6, OUT1, OUT2 | `in3`…`in6`, `out1`, `out2` |

`input(name)`, `output(name)` and `set_output(name, state)` take the printed
name (`io.input("IN03")`, `do.set_output("R0", True)`), or the number where
the printed name is a number with a prefix (`io.input(3)`). An unknown name
raises `ValueError`. The generic profile classes (`DigitalIO`, `GenericIO`)
have no device of their own to read names from: they number channels from
0.

## Reading, writing and variables

Reading is named after the thing, writing is an action and keeps its verb,
as in Qt (`text()` / `setText()`):

```python
io.in03()                     # input IN03 now (or io.input("IN03"))
io.out03()                    # output OUT03 as written
io.set_out03(True)            # (or io.set_output("OUT03", True))
io.inputs()                   # all inputs as a word
drv.position_actual_value()   # a drive's actual position now
drv.set_target_velocity(1000)
```

Without `()`, the name is the **variable itself**: that is what
[`Events`](python-api.md#events) watches.

```python
@ev.rising(io.in03)                                    # the input, not its value
def pressed(e): ...

@ev.when(drv.position_actual_value, lambda v: v > 1000)
def far(e): ...
```

A variable is not a value: `if io.in03:` or `io.in03 == True` raise
`TypeError` ("call it, in03(), to test it") instead of being always true or
always false. Every per-point method (`in00` ... `in15`, `out00`,
`set_out00` ...) is written out in the class, so editors complete the
names. The variables are declared with `@signal(index, subindex, bit=)`
(`odycat.signal`) and found by object index, whatever the entries are called
in the config.

---

## `Servo` (CiA 402)

`odycat.devices.cia402`. Works with any CiA 402 drive (servo, stepper, VFD)
and any PDO
mapping: objects are found **by index** in the slave's config entries, not by
position. Supports PP, PV, PT, HM, CSP, CSV, CST (the drive decides which it
actually implements).

```python
from odycat.devices.cia402 import Servo

drv = Servo(label="axis_x")          # axis=1 for the 2nd axis of a multi-axis drive
drv.set_mode("CSV")                  # writes 0x6060, waits for 0x6061
drv.enable()                         # fault reset → shutdown → switch on → enable
drv.set_target_velocity(1000)
...
drv.set_target_velocity(0)
drv.disable()
```

### Required mapping

Only mapped objects can be used; there is no runtime SDO. Using an unmapped
object raises `ObjectNotMappedError` (a `KeyError`) naming the object and the
config block to add it to.

| Feature | Inputs (`txpdo`) | Outputs (`rxpdo`) |
|---|---|---|
| Always | `0x6041` statusword `u16` | `0x6040` controlword `u16` |
| `set_mode` | `0x6061` mode display `b` | `0x6060` mode `b` |
| CSP / PP | `0x6064` position actual `i32` | `0x607A` target position `i32` |
| CSV / PV | `0x606C` velocity actual `i32` | `0x60FF` target velocity `i32` |
| CST / PT | `0x6077` torque actual `i16` | `0x6071` target torque `i16` |
| Fault codes | `0x603F` error code `u16` | |

Instead of `set_mode`, the mode can be fixed with a startup SDO
(`{"index": "0x6060", "subindex": 0, "type": "i8", "value": 8}`). Parameters
that do not change at runtime (profile velocity/acceleration, max torque,
torque slope, quick-stop deceleration, ...) can be given as startup SDOs
instead of being mapped.

### API

State machine:

| Member | Description |
|---|---|
| `enable(timeout_s=2.0)` | Clears faults, sets mapped targets to "no motion" (target position = actual position, target velocity/torque = 0), then 0x06 → 0x07 → 0x0F, waiting for each statusword bit. `RuntimeError` on timeout |
| `disable()` | Controlword 0x0006 (power stage off) |
| `quick_stop()` | Controlword 0x0002 (decelerates with 0x6085, then disables) |
| `reset_fault()` | Rising edge on controlword bit 7 |
| `halt(state=True)`, `resume()` | Halt bit (PP/PV/PT/HM) |
| `set_mode(mode, timeout_s=1.0)` | `"PP"`, `"PV"`, `"PT"`/`"TQ"`, `"HM"`, `"IP"`, `"VL"`, `"CSP"`, `"CSV"`, `"CST"` or an int |
| `mode_display()` | 0x6061 |

A drive that drops out of OP comes back disabled: the engine zeroes its
outputs (controlword 0) when it leaves OP, and the next call on the `Servo`
raises `DeviceLostError`. Catch it and call `enable()` again to resume (see
"Outputs after a slave drops out" in the configuration reference).

All of these are variables: `drv.is_fault()` is the value, `drv.is_fault`
can be watched with `Events`.

Status (from 0x6041): `statusword()`, `status_bit(bit)`,
`is_ready_to_switch_on()`, `is_switched_on()`, `is_operation_enabled()`,
`is_fault()`, `is_warning()`, `is_target_reached()`,
`is_internal_limit_active()`, `is_following_error()`, `error_code()` (0x603F).
Controlword: `controlword()` (as written), `set_controlword(value)`,
`set_controlword_bit(bit, value)`.

Actual values: `position_actual_value()`, `velocity_actual_value()`,
`torque_actual_value()`, `current_actual_value()`, `position_demand_value()`,
`velocity_demand_value()`, `torque_demand_value()`,
`following_error_actual_value()`, `digital_inputs()` (0x60FD).

Targets as written: `target_position()`, `target_velocity()`,
`target_torque()`.

Targets and limits: `set_target_position`, `set_target_velocity`,
`set_target_torque`, `set_position_offset`, `set_velocity_offset`,
`set_torque_offset`, `set_max_torque`, `set_torque_limits(pos, neg)`,
`set_torque_slope`, `set_torque_profile_type`, `set_max_current`,
`set_max_motor_speed`, `set_profile_velocity`, `set_profile_acceleration`,
`set_profile_deceleration`, `set_quick_stop_deceleration`.

Profile position:

```python
drv.set_mode("PP")
drv.enable()
drv.move_to(10000)                  # absolute, waits for target reached
drv.move_to(-500, relative=True)
drv.move_to(0, wait=False)          # returns after set-point acknowledge
```

`move_to` sets "change set immediately", performs the new-set-point /
set-point-acknowledge handshake and, with `wait=True`, waits for
target-reached (`timeout_s=30.0`). Speed and ramps come from 0x6081 / 0x6083 /
0x6084 (map them or set them as SDOs). `trigger_new_set_point()` does the
handshake alone, for a target already set with `set_target_position()`: it
holds bit 4 low, raises it, waits for set-point acknowledge and drops bit 4
(`timeout_s=2.0`). Each level is held for a number of bus cycles (counted
with `wait_cycles`, so an engine stall cannot shorten it): 2, plus one Sync0
period for a drive with `dc_sync` (12 cycles for a 10 ms Sync0 on a 1 ms
bus). Some drives occasionally show no acknowledge; if the drive
starts moving and its target-reached bit changes, the set-point counts as
accepted. In HM it gives a rising edge on bit 4 to start homing and leaves
the bit high.

Generic object access (index relative to axis 0):

```python
drv.has_object(0x60B1)
drv.read_object(0x6064)
drv.write_object(0x60FE, 0x10000, subindex=1)
drv.stage_object(0x607A, 5000)       # for Device.transmit_outputs()
```

Units are the drive's: positions in user units (counts/steps), velocities in
units/s, torques and currents in 0.1 % of rated torque/current.

Module constants: `OP_MODE`, `CW_*` (controlword bits), `SW_*` (statusword
bits), object indices (`CONTROLWORD`, `TARGET_POSITION`, ...), `AXIS_OFFSET`
(0x800).

### Multi-axis drives

CiA 402 repeats the profile every 0x800 objects. `Servo(label="drive",
axis=1)` uses 0x6840, 0x6841, 0x687A, ... Two `Servo` objects on the same
slave (axis 0 and 1) work independently.

### Cyclic synchronous modes from Python (CSP / CSV / CST)

They work: set the mode, enable, and write a new target every cycle. The
engine sends the last target written in every cycle, so the drive always
gets one; the script only has to keep it up to date.

```python
from odycat import Bus
from odycat.devices.leadshine import ECT60

bus = Bus()
drv = ECT60(alias=60)
drv.set_mode("CSP")
drv.enable()

start = drv.position_actual_value()
t0 = bus.wait_cycles(0)
while True:
    now = bus.wait_cycles(1)                       # once per bus cycle
    drv.set_target_position(start + path(now - t0))  # path(): cycles -> position
```

What to know:

- **A late iteration repeats a target.** If the script misses a cycle, the
  drive gets the previous target once more: in CSP a one-cycle pause in the
  commanded path (a small velocity blip and following-error spike), in CSV
  one more cycle at the previous velocity. Nothing fails; the motion is less
  smooth. Computing the target from the **cycle count** (`path(now - t0)`
  above), not from a loop counter, means a late iteration jumps straight to
  where the axis should be, so the path keeps its timing.
- **How often it happens** depends on the PC and its load. Measure it on the
  machine with the trace: a cycle whose target equals the previous one's was
  missed by the script.

  ```python
  from odycat.trace import Trace
  t = Trace()
  slots, _ = t.read(after=t.head - 10000)          # the last 10 s
  targets = [t.value(s, drv.target_position) for s in slots]
  repeats = sum(a == b for a, b in zip(targets, targets[1:]))
  ```
- **DC**: add `"dc_sync": 0` (Sync0 = the bus cycle) to the drive's config
  entry, so it applies targets on Sync0 and they take effect evenly even
  when they arrive with some jitter. `set_mode("CSP")` refuses to run
  without it.
- **Real time**: run the engine with real-time scheduling (`"require_rt":
  true`) and, for the fewest misses, the script too, below the engine's
  priority 80: `chrt -f 50 python3 motion.py`. Without root, allow it for the
  `odycat` group once, in `/etc/security/limits.d/odycat.conf`:
  `@odycat - rtprio 60` and `@odycat - memlock unlimited` (log in again).
- **Several axes in the same cycle**: stage the targets, then send them
  together, so no axis moves a cycle before another:

  ```python
  from odycat import Device
  from odycat.devices.cia402.servo import TARGET_POSITION
  x.stage_object(TARGET_POSITION, px)
  y.stage_object(TARGET_POSITION, py)
  Device.transmit_outputs()
  ```
- **With `Events`**, pace the loop with `ev.wait_cycles(1)` instead: handlers
  then run inside that wait, so keep them short.
- **Limits**: Python cannot guarantee that every cycle gets a new target.
  That is fine for conveyors, pick and place, gantries and synchronised axes
  at moderate speed; high-precision contouring belongs in a motion controller
  or in the drive's own interpolation. Profile modes (PP/PV/PT/HM), where the
  drive generates the trajectory, need neither DC nor a cycle-paced loop.

---

## `DigitalIO` (Modular Device Profile, ETG.5001)

`odycat.devices.mdp`. Any digital I/O slave whose config maps inputs at
`0x6000`–`0x6FFF` and outputs at `0x7000`–`0x7FFF`, the layout most EtherCAT
I/O uses. Generic: no device profile, so the config describes the slave in
full (`odycat-config scan` writes it); objects are found by index.

Channel n (0-based) is the n-th digital bit in object order, so one object
per channel (Beckhoff `0x6000:01`, `0x6010:01`, ...), 1-bit subindices
(Delta `0x6000:01..08`) and a 16-bit word (EC3A `0x6000:01`) all number the
same way. Signed and float entries (analog values) are not channels.

```python
from odycat.devices.mdp import DigitalIO

io = DigitalIO(alias=40)
io.n_inputs, io.n_outputs
io.input(3); io.inputs()                    # value now; all as a word
io.set_output(0, True); io.output(0); io.set_outputs(0x00FF)
ev.rising(io.input_variable(3))             # the variable, for Events
```

There are no `input_3()` methods: the number of channels depends on the slave.

## `GenericIO` (CiA 401)

`odycat.devices.cia401`. I/O following the CiA 401 profile, found on
CANopen-derived devices (most EtherCAT I/O uses the Modular Device Profile
above). Digital channels (0-based) come from the first input family mapped
among `0x6000` (8-bit groups), `0x6100` (16-bit), `0x6120` (32-bit), and the
first output family among `0x6200`, `0x6300`, `0x6320`: the families are the
same channels at different widths, so only one is used. Analog channels use
the profile's subindices (1-based): inputs `0x6401` / `0x6402`, outputs
`0x6411` / `0x6412`, raw values.

```python
from odycat.devices.cia401 import GenericIO

io = GenericIO(label="remote_io")
io.input(3); io.set_output(0, True); io.inputs()
io.analog_input(1); io.set_analog_output(2, 1200); io.analog_output(2)
ev.change(io.analog_input_variable(1), deadband=50)
```

## `ECT60` (Leadshine closed-loop stepper)

Subclass of `Servo`. Supports PP, PV, PT, HM, CSP, CSV; no torque objects.
Runs in free-run unless its config entry has `dc_sync`; CSP/CSV need it
(`"dc_sync": 1000000`, the class adds `assign_activate` `0x0300`).
Profile mapping: statusword, mode display, position, velocity, `0x60FD`
inputs, `0x603F` in `txpdo` 0x1A00; controlword, mode, target position,
target velocity, `0x60FE:01` outputs in `rxpdo` 0x1600. Profile velocity and
accelerations are application settings: add them as `sdos`.

Extra API:

| Member | Description |
|---|---|
| `digital_inputs()` | Raw 0x60FD: bit 0 negative limit, bit 1 positive limit, bit 2 home, bit 15+n `IN<n>` |
| `negative_limit_switch()`, `positive_limit_switch()`, `home_switch()` | 0x60FD bits 0, 1, 2 (variables) |
| `in3()` … `in6()`, `input("IN3")` | The connector's inputs IN3…IN6 (0x60FD bit 15+n) |
| `out1()`, `out2()`, `output("OUT1")` | The connector's outputs, as written |
| `set_out1(state)`, `set_output("OUT1", state)` | Via 0x60FE:01 bit 15+n. Only effective if enabled in 0x60FE:02 and the output function (0x2005) |

```python
drv = ECT60(alias=60)
drv.set_mode("PP"); drv.enable(); drv.move_to(10000); drv.disable()
```

Motion parameters are set with startup SDOs in the slave's config entry
(units are steps: 0x2001 motor resolution is 10000 steps/rev by default, so
a velocity of 10000 is 1 rev/s):

```json
"sdos": [
  { "index": "0x6081", "subindex": 0, "type": "u32", "value": 50000,  "label": "profile_velocity" },
  { "index": "0x6083", "subindex": 0, "type": "u32", "value": 200000, "label": "profile_acceleration" },
  { "index": "0x6084", "subindex": 0, "type": "u32", "value": 200000, "label": "profile_deceleration" },
  { "index": "0x6085", "subindex": 0, "type": "u32", "value": 500000, "label": "quick_stop_deceleration" }
]
```

0x6081/0x6083/0x6084 apply to PP and PV (in PP, 0x6081 is the move speed).
Homing uses 0x6099:01/02 (fast/slow speed) and 0x609A (acceleration). In
CSP/CSV the drive follows the targets the application sends, so the
application sets speed and ramps. The ECT60 has no 0x607F/0x6080 speed-limit
objects. Drive defaults: 0x6081 = 10000, 0x6083 = 0x6084 = 100000,
0x6085 = 500000.

## `EL2809` / `EL1809` (Beckhoff 16 DO / 16 DI)

Profile: the terminal's 16 one-bit PDOs as in its ESI (EL2809 RxPDOs
`0x1600`..`0x160F` → `0x7000:01`, `0x7010:01`, ...; EL1809 TxPDOs
`0x1A00`..`0x1A0F` → `0x6000:01`, `0x6010:01`, ...), packed into one 16-bit
word. Channels 1..16.

```python
out = EL2809(position=0)
out.set_output_1(True)              # or out.set_output(1, True)
out.set_outputs(0xFFFF)             # word (bit n-1 = channel n) or 16 bools
out.output_1(); out.outputs()

inp = EL1809(position=1)
inp.input_3(); inp.input(3); inp.inputs()
```

## `EC3A_IO1632` (AMSA Motion 16 DI + 16 DO)

Config: `txpdo` `0x6000:01` `u16`, `rxpdo` `0x7000:01` `u16`; bit n of
each word is IN<n> / OUT<n>.

```python
io = EC3A_IO1632(alias=40)
io.in03(); io.input("IN03"); io.input(3); io.inputs()   # inputs() is a variable too
io.set_out00(True); io.set_output("OUT00", True); io.set_outputs(0x00FF)
io.out00(); io.outputs()
```

## `AX516AM` / `AX516AN` (Delta AX-5 remote I/O)

Each module behind the AX-500CEC00 coupler is its own slave with 16 one-bit
entries split over two PDOs (see the AX-516AM example in
[configuration.md](configuration.md#pdos-rxpdo-txpdo)
and [`demo_box.json`](../examples/demo_box.json)). The coupler itself
is configured with empty PDO blocks. The terminals come in two columns, L0…L7
and R0…R7; in the process data they alternate: bit 2n = L<n>, bit 2n+1 =
R<n>.

```python
di = AX516AM(alias=2)
di.l0(); di.input("L0"); di.inputs()       # inputs(): 16-bit word

do = AX516AN(alias=3)
do.set_r0(True); do.set_output("R0", True)
do.set_outputs(0x00FF)                      # word or list of 16 bools, bit order
do.r0(); do.outputs()
```

The constructor raises `RuntimeError` if the config does not map exactly 2
bytes. The modules need their SII completed once before IgH can exchange
their process data: `odycat-config prepare --position <ring position>` (see
[Preparing a device](tools.md#preparing-a-device-odycat-config-prepare)).

## Writing a new device class

This is how to write a class for your own machine. To have a device
supported in OdyCAT itself (tested, in the catalogue, maintained), see
"Devices" in [CONTRIBUTING.md](../CONTRIBUTING.md).

Declare the device's variables with `@signal`: they are found by object
index (any entry order, any labels), editors complete them, `Events` can
watch them, and a unit test checks every declared object is in the class's
`PROFILE`. Write each per-channel method out (a generator script is fine, the
result goes in the source).

```python
# odycat/devices/<vendor>/<model>.py    (lowercase file, class named as the product)
"""<Vendor> <MODEL> — what it is.

Identity: vendor 0x..., product 0x...

Profile mapping:
  rxpdo 0x1600: 0x7000:01 digital outputs u16 ("H")
  txpdo 0x1A00: 0x6000:01 digital inputs u16 ("H")
"""

from __future__ import annotations

from typing import Optional

from odycat.device import Device
from odycat.signal import signal


class MODEL(Device):
    """<Vendor> <MODEL> — 16 DI / 16 DO."""

    # Filled in for {"device": "<vendor>.MODEL", ...} config entries.
    PROFILE = {
        "vendor_id": "0x...",
        "product_code": "0x...",
        "rxpdo": {"index": "0x1600", "entries": [
            {"index": "0x7000", "subindex": 1, "type": "u16", "label": "outputs"}]},
        "txpdo": {"index": "0x1A00", "entries": [
            {"index": "0x6000", "subindex": 1, "type": "u16", "label": "inputs"}]},
    }

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None):
        super().__init__(position, alias=alias, label=label, bus=bus)

    @signal(0x6000, 1)
    def inputs(self) -> int:
        """All 16 inputs as a word, bit n = channel n."""
        ...

    def input(self, channel: int) -> bool:
        """Input <channel> (0..15)."""
        if not 0 <= channel <= 15:
            raise ValueError(f"MODEL: channel {channel} out of range 0..15")
        return bool((self.inputs() >> channel) & 1)

    def set_output(self, channel: int, state: bool) -> None:
        """Set output <channel> (0..15); no other output changes."""
        if not 0 <= channel <= 15:
            raise ValueError(f"MODEL: channel {channel} out of range 0..15")
        self.write_entry_bit("0x7000:1", channel, state)

    @signal(0x6000, 1, bit=0)
    def input_0(self) -> bool:
        """Input 0."""
        ...

    def set_output_0(self, state: bool) -> None:
        """Set output 0."""
        self.set_output(0, state)

    # ... input_1 .. input_15, output_n, set_output_n written out the same way
```

Alternatives:

- **CiA 402 drive**: subclass `Servo` and add only vendor specifics (see
  `ECT60`).
- **Fixed struct layout** (`txpdo_fmt=`/`rxpdo_fmt=` passed to
  `super().__init__`, then `read_txpdo()`/`set_rxpdo_field()`): fastest and
  simplest for fixed-mapping slaves, but the config must list the entries in
  exactly that order and size. Document the required mapping in the module
  docstring.

Then:

1. Export the class from the vendor package's `__init__.py` (import +
   `__all__`). A new vendor is a new directory `src/odycat/devices/<vendor>/`
   with an `__init__.py`; nothing in the core lists vendors, they are found
   by scanning `odycat.devices`. A class written for your own machine can
   instead live in your own code, used directly by import.
2. Give it a `PROFILE` with the identity and the mapping the class expects
   (the entries of `ethercat pdos -p <pos>`, or of the slave's ESI), and add
   it to `tests/test_profiles.py`. Label each entry with the name of the
   variable that reads it (`inputs`, `position_actual_value`), or with its
   CiA name when the class has none (`modes_of_operation`), so configs,
   `Events` and the trace use the same names as the class. A config entry with
   `"device": "<vendor>.<Class>"` then needs nothing else.
3. Give it a support tier: `SUPPORT = "community"` for a contributed class
   (see [Devices in CONTRIBUTING](../CONTRIBUTING.md#devices)).
4. Add a row to the tables at the top of this page.
5. If the device needs a one-off change before IgH can use it (an incomplete
   SII, for example), give the class a `_prepare` classmethod that
   `odycat-config prepare` runs: see `odycat/_prepare.py` and the Delta
   AX-516 classes.

### Testing a device class without hardware

`odycat.testing` is a fake engine: the engine's shared memory as ordinary
files, so device classes, `Events` and the trace run their real code against
it. Publish inputs, call the class, check what it wrote:

```python
import struct
import unittest

from odycat.testing import device_slave, fake_engine, mini_config
from odycat.devices.amsamotion import EC3A_IO1632


class EC3ATests(unittest.TestCase):
    def test_input_and_output(self):
        cfg = mini_config(device_slave("amsamotion.EC3A_IO1632", "io"))
        with fake_engine(cfg) as eng:
            eng.publish(inputs={"io": struct.pack("<H", 1 << 3)})
            io = EC3A_IO1632(label="io", bus="fake0")
            self.assertTrue(io.in03())
            io.set_out00(True)
            self.assertEqual(eng.read_outputs("io"), struct.pack("<H", 1))
            io.close()
```

`eng.publish()` runs one cycle (inputs, header, trace slot); `eng.status()`
and `eng.engine_event()` fake slaves leaving OP and engine events.
