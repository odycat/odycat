# Configuration reference (`config.json`)

The config is the single source of truth for the engine and for Python.
The engine copies it into shared memory at startup, and the Python classes
read their layout from that copy, so a script always matches the config the
running engine was started with. Changing the config means restarting the
engine.

Examples in the repo: [`examples/config.json`](../examples/config.json) (EL2809 / EL1809 by
device profile, servo in full), [`examples/demo_box.json`](../examples/demo_box.json)
(I/O module, stepper drive, Delta AX-5 modules and a CATIO-A board written in
full, all by alias).

## Top level

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `bus` | string | yes | | The bus name: names its shm regions (`/run/odycat/<bus>_shared`, `_config`, `_trace`), and scripts attach to it by this name (`Bus("bus0")`, `bus=` in Python). Letters, digits, `_`, `.`, `-`. Does **not** choose the network card: that is the IgH master's `MASTERn_DEVICE` (`"master"` picks the master) |
| `master` | number | no | 0 | IgH master index (one per network card). Several buses = several masters, each with its own engine and config |
| `cycle_us` | number | yes | | Cycle period in µs: one of 125, 250, 500, 1000, 2000, 4000, 8000, 10000. Any other value is an error |
| `slaves` | array | yes | | Slave entries, see below. Max 512 |
| `shm_group` | string | no | `"odycat"` | Group owning the shm regions (data `0660`, config and trace `0640`). Must exist |
| `history_s` | number | no | 10 | Seconds of cycle history kept in the trace region (0..60, 0 = no trace). Used by `Trace` and `Events`; about `history_s / cycle_us` × (slave blocks + 64 bytes) of locked RAM |
| `require_rt` | bool | no | `false` | Exit at startup if memory cannot be locked or `SCHED_FIFO` cannot be set, instead of warning and running without |

## Slave entry

### Device profiles (`device`)

For hardware OdyCAT has a class for, name the class and give only the
address and the label:

```json
{ "device": "leadshine.ECT60", "alias": 60, "label": "axis_x" }
```

The name is always `"vendor.Model"`, the vendor package and the class (as in
`from odycat.devices.leadshine import ECT60`); an unqualified name is refused
with a suggestion. `odycat-config expand config.json -o machine.json` fills
in the rest from the class `PROFILE` (identity, PDOs, required SDOs), as your
user; the engine takes the result, so the engine and every Python script see
a complete entry. The model ignores
case and `-`/`_` (`"amsamotion.EC3A-IO1632"` = `"amsamotion.EC3A_IO1632"`);
the profiles available are the classes in [devices.md](devices.md) (the
standard-profile classes such as `cia402.Servo` have none).

Any field of the table below can be added to the entry and replaces the
profile's, except:

- `sdos`: written after the profile's SDOs (e.g. profile accelerations);
- `dc_sync`: a profile never turns DC on; the entry does (see below), and
  the class supplies its DC settings (`assign_activate`);
- a field set to `null` removes it from the profile.

Replacing `rxpdo`/`txpdo` is possible, but the class then has to accept the
new mapping (classes with a fixed layout do not).

### Fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `device` | string | no | Device profile to fill the entry from (see above) |
| `position` | number | yes¹ | Ring position, or offset from the aliased slave (see Addressing) |
| `alias` | number or string | no | Station alias 1..65535 (`"0x3C"` also accepted); 0/absent = absolute addressing |
| `label` | string | yes | Unique name used by Python (`Device(label=...)`) and in error messages (the engine prints at most 63 chars) |
| `name` | string | no | Free text (model name), shown by `BusMonitor` |
| `vendor_id` | string | yes | Hex string, e.g. `"0x00000002"`. Checked against the bus; mismatch is fatal |
| `product_code` | string | yes | Hex string. Checked against the bus |
| `rxpdo` | object or array | yes | **Outputs** (slave RxPDOs, master → slave): one PDO, or a list of PDOs. Use `{"entries": []}` when there are none |
| `txpdo` | object or array | yes | **Inputs** (slave TxPDOs, slave → master): one PDO, or a list of PDOs. Use `{"entries": []}` when there are none |
| `sdos` | array | no | Startup SDO writes (max 32) |
| `dc_sync` | object | no | Enables distributed clocks for this slave |
| `sync_group` | string | no | Slaves with the same name send the outputs of one `transmit_outputs()` in the same cycle (see below). Only on slaves with outputs; at most 32 slaves per group |

¹ With an alias, `position` may be omitted and defaults to 0.

### Addressing

Same semantics as IgH and `ethercat slaves`:

- No alias (or 0): `position` is the absolute ring position (0 = first slave
  after the master).
- With `alias`: `position` is the offset from the slave that carries that
  station alias. `"alias": 60, "position": 1` is the slave right after the
  one with alias 60. Inserting slaves *before* the aliased slave does not
  change the address.

Aliases must be unique on the bus, and two config entries cannot share an
address. Aliases are written into the slave's SII (e.g. `ethercat alias -p 0 60`,
then power-cycle) or set by DIP switches on some devices.

### PDOs (`rxpdo`, `txpdo`)

PDO names follow EtherCAT, seen from the slave, as in the ESI file and in
`ethercat pdos`:

| Key | Slave PDOs | Direction | Holds |
|---|---|---|---|
| `rxpdo` | RxPDOs `0x1600`–`0x17FF` (SM2) | master → slave | **Outputs**: controlword, targets, digital outputs |
| `txpdo` | TxPDOs `0x1A00`–`0x1BFF` (SM3) | slave → master | **Inputs**: statusword, actual values, digital inputs |

A PDO index outside its range (e.g. `0x1A00` under `rxpdo`) stops the engine
at startup.

One PDO per direction:

```json
"txpdo": {
  "index": "0x1A00",
  "entries": [
    { "index": "0x6041", "subindex": 0, "type": "u16", "label": "statusword" },
    { "index": "0x6064", "subindex": 0, "type": "i32", "label": "actual_position" }
  ]
}
```

Several PDOs per direction, as a list (e.g. an analog module with one PDO per
channel plus a status PDO, or a multi-axis drive at `0x1A00`/`0x1A10`/`0x1A20`):

```json
"txpdo": [
  { "index": "0x1A00", "entries": [
    { "index": "0x6000", "subindex": 17, "type": "i16", "label": "ai0" } ] },
  { "index": "0x1A01", "entries": [
    { "index": "0x6010", "subindex": 17, "type": "i16", "label": "ai1" } ] },
  { "index": "0x1A10", "entries": [
    { "index": "0x6100", "subindex": 1, "type": "u16", "label": "status" } ] }
]
```

PDO fields:

| Field | Meaning |
|---|---|
| `index` | Slave PDO index (hex string), required when the PDO has entries. Inputs use the slave's TxPDOs (`0x1A00`–`0x1BFF`, under `txpdo`), outputs its RxPDOs (`0x1600`–`0x17FF`, under `rxpdo`) |
| `entries` | Mapped objects, in the order the slave sends them |

OdyCAT assigns exactly the PDOs that have entries to the sync managers (SM2
outputs, SM3 inputs), in config order, and writes their mapping. PDOs without
entries are not assigned. The slave must accept that assignment and
mapping: fixed-mapping slaves need the PDOs and entries exactly as their ESI /
`ethercat pdos` shows; slaves with configurable mapping accept any objects
that are PDO-mappable, in as many PDOs as the slave offers (see
`ethercat sdos -p <pos>` for its `0x16xx` / `0x1Axx` objects).

In shared memory and in Python, the PDOs of a direction follow each other
without a gap: splitting the same entries over several PDOs gives the same
layout as one PDO holding them all.

Entry fields:

| Field | Meaning |
|---|---|
| `index` | Object index, hex string. `"0x0000"` = gap (padding) |
| `subindex` | Number |
| `type` | The value's type, see below |
| `bits` | Instead of `type`: the bit length of a bit field (1-bit channels, a 4-bit value) or of a gap. A value of 8, 16, 32 or 64 bits is given by its `type` |
| `label` | Name for `read_entry`/`write_entry` and `BusMonitor`. Default `"0xINDEX:SUB"`. Keep labels unique within a slave |

Types, the same for PDO entries and SDOs:

| `type` | Bits | ETG / IEC 61131 name | | `type` | Bits | ETG / IEC 61131 name |
|---|---|---|---|---|---|---|
| `i8` | 8 | SINT | | `u8` | 8 | USINT, BYTE |
| `i16` | 16 | INT | | `u16` | 16 | UINT, WORD |
| `i32` | 32 | DINT | | `u32` | 32 | UDINT, DWORD |
| `i64` | 64 | LINT | | `u64` | 64 | ULINT, LWORD |
| `f32` | 32 | REAL | | `f64` | 64 | LREAL |
| `bool` | 8 | BOOL (a whole byte) | | | | |

A 1-bit BOOL is `"bits": 1` and reads as `True`/`False`. CiA 402 types:
controlword/statusword `u16`, modes `i8`, positions/velocities `i32`, torques
`i16`, digital inputs `u32`, error code `u16`.

Rules:

- The entries of a direction (all its PDOs together) must add up to whole
  bytes. If not, add a gap entry: `{"index": "0x0000", "subindex": 0, "bits": 4}`.
  A single PDO need not: 16 PDOs of one 1-bit channel each are 2 bytes.
- Entries with `bits` are unsigned bit fields in Python (1 bit → `bool`).
- Gaps are part of the PDO on the wire. A byte made only of gaps is not
  copied to shared memory and is invisible in Python.
- The order of entries is the order on the wire and in shared memory.
  Device classes based on `entries` (like `Servo`) find objects by index, so
  for them the order is free; classes with a fixed `struct` format need the
  exact order documented in the class.

1-bit channels split over two PDOs (Delta AX-516AM):

```json
"txpdo": [
  { "index": "0x1A00", "entries": [
    { "index": "0x6000", "subindex": 1, "bits": 1, "label": "ch0" },
    ...
    { "index": "0x6000", "subindex": 8, "bits": 1, "label": "ch7" } ] },
  { "index": "0x1A01", "entries": [
    { "index": "0x6001", "subindex": 1, "bits": 1, "label": "ch8" },
    ... ] }
],
"rxpdo": { "entries": [] }
```

### Startup SDOs (`sdos`)

```json
"sdos": [
  { "index": "0x6060", "subindex": 0, "type": "i8",  "value": 8 },
  { "index": "0x6083", "subindex": 0, "type": "u32", "value": 500000, "label": "accel" }
]
```

| Field | Meaning |
|---|---|
| `index` | Object index, 0x0001..0xFFFF: hex string (`"0x6083"`) or number |
| `subindex` | 0..255: number or hex string |
| `type` | `bool`, `u8`, `i8`, `u16`, `i16`, `u32`, `i32`, `f32` (the types above, up to 4 bytes) |
| `value` | Integer number, or a string in decimal (`"-12"`) or hex (`"0x00FF"`). Must fit the type: `u16` 0..65535, `i8` -128..127, ... A hex string is the raw bit pattern (0..max of the size, so `"0xFFFF"` is -1 as `i16`). `bool` also takes `true`/`false`. `f32` takes a number or a hex bit pattern (`"0x3FC00000"` = 1.5) |
| `label` | Optional, shown in error messages |

SDOs are queued in IgH and downloaded by the master each time the slave goes
from PREOP to SAFEOP (also after the slave is power-cycled while the engine
runs). Only 1, 2 and 4-byte values are supported: `u64`, `i64` and `f64`
are rejected when the config is loaded. Any value that does not fit its
type, a fraction for an integer type, or text that is not a number stops
the engine with an error naming the slave and the SDO: nothing is rounded
or truncated. The startup log prints every value as it will be written
(`0x6083:00 = 20000 (0x00004E20)`). A rejected SDO (abort code) makes the
slave fail to reach SAFEOP.

Use SDOs for parameters that are set once (max torque, profile
accelerations, input polarity, ...). There is no runtime SDO access; values
that must change while running need to be mapped in a PDO.

### Distributed clocks (`dc_sync`)

**Every slave runs in free-run unless its entry has `dc_sync`.** DC is a
deliberate choice: a slave with DC is synchronised to Sync0, which is what
cyclic synchronous modes (CSP/CSV/CST) and coordinated axes need, and it
makes the bus timing your responsibility: the engine must run with
real-time scheduling (`"require_rt": true`), or a late cycle drops the
slave out of OP. Profile modes (PP/PV/PT/HM) and I/O do not need DC.

The short form is the Sync0 period in ns (`0` = the bus cycle); the device
class supplies the rest:

```json
{ "device": "leadshine.ECT60", "alias": 60, "label": "axis_x", "dc_sync": 1000000 }
```

The full form gives every setting (for slaves without a class, or to
override the class's):

```json
"dc_sync": {
  "assign_activate": "0x0300",
  "sync0_cycle_ns": 0,
  "sync0_shift_ns": 0,
  "sync1_cycle_ns": 0,
  "sync1_shift_ns": 0
}
```

`null` or no `dc_sync` means free-run.

| Field | Default | Meaning |
|---|---|---|
| `assign_activate` | `"0x0300"` | ESI `AssignActivate` word (`0x0300` = Sync0) |
| `sync0_cycle_ns` | 0 → `cycle_us × 1000` | Sync0 period; a whole multiple of the bus cycle |
| `sync0_shift_ns` | 0 | Sync0 shift |
| `sync1_cycle_ns`, `sync1_shift_ns` | 0 | Sync1 |

`Servo.set_mode("CSP")` (also CSV, CST) on a slave without `dc_sync` stops
with a message saying what to add. The engine synchronises the reference
clock every cycle for all configs.

### Sync modes and a master without real-time scheduling

Slaves **without** `dc_sync` are switched to free-run at startup
(`0x1C32:01` and `0x1C33:01` = 0) when they list free-run as supported
(`0x1C32:04` bit 0); the engine prints which ones. In free-run a slave does
not depend on when frames arrive. In SM-synchronous mode (`0x1C32:01` = 1,
the default of many I/O modules) a late cycle, followed by frames sent back
to back to catch up, counts as a sync error; past the slave's limit
(`0x10F1:02`) many modules latch an error (`0x1C32:20`) and stop driving
their outputs while staying in OP. Slaves without CoE keep their mode. To
keep a slave SM-synchronous, list `0x1C32:01` (and `0x1C33:01`) in its
`sdos`: explicit SDOs take precedence.

Slaves **with** `dc_sync` are synchronised to Sync0. A Sync0 period that
gets no new frame is a sync error, and the drive drops out of OP (e.g. error
`0x7500`). When that happens and the engine runs without real-time
scheduling, the engine prints a warning with the longest recent cycle, and
the `DeviceLostError` in Python says the same. If you must run without RT,
set the Sync0 period to several bus cycles so a late cycle cannot leave a
period empty. A longer Sync0 means the drive takes a new setpoint less
often.

The Sync0 period must be a whole multiple of the bus cycle (`cycle_us ×
1000`); the engine refuses other values. Free-run slaves are still protected
against bus loss by their sync manager watchdog (100 ms by default): if no
output data arrives for that long, the slave goes to SAFEOP and its outputs
to their safe state.

### Sync groups (`sync_group`)

A write to a slave's outputs reaches the bus whole, never half done, and
`Device.transmit_outputs()` publishes several devices at once. Whether those
devices go out in the **same cycle** depends on their sync group:

- Slaves with the same `"sync_group"` name are published with one store: the
  outputs staged for them in one `transmit_outputs()` always go out
  together. Use it for axes that move together (a gantry, interpolated
  axes), or a drive and the I/O that must switch with it.
- A slave without `"sync_group"` is a group of its own. Its writers never
  wait for anyone else's.

The price of a group is that scripts writing to its slaves wait for each
other (microseconds), so do not put unrelated slaves in one.

```json
{ "device": "leadshine.ECT60", "alias": 60, "label": "axis_x", "sync_group": "gantry" },
{ "device": "leadshine.ECT60", "alias": 61, "label": "axis_y", "sync_group": "gantry" }
```

### Outputs after a slave drops out

When a slave leaves OP (cable, power, sync error), it switches its own
outputs to their safe state, and IgH reconfigures it and brings it back on
its own. The engine then sends zeros to it until a script writes its
outputs again, so the slave returns with all outputs off instead of
resuming the old commands. A
CiA 402 drive comes back with controlword 0 and stays disabled until the
script calls `enable()` again (which first sets target = actual), instead of
re-enabling itself in the middle of whatever the script was doing. An I/O
module keeps its outputs off until the script writes them again: a script
that writes an output once and never again finds it off after a drop. A
script can notice the drop through `is_operation_enabled` or the slave
status table (`BusMonitor`), and the next read or write on that device
raises `DeviceLostError` (see the Python API).

## Limits

| Limit | Value |
|---|---|
| Slaves per config | 512 |
| PDOs per direction | 32 |
| Entries per direction (all its PDOs together) | 256 |
| SDOs per slave | 32 |
| `cycle_us` | 125, 250, 500, 1000, 2000, 4000, 8000, 10000 |
| Slaves per sync group | 32 |

## Complete example (servo + I/O)

```json
{
  "bus": "bus0",
  "cycle_us": 1000,
  "slaves": [
    {
      "alias": 60, "position": 0,
      "name": "ECT60", "label": "stepper_x",
      "vendor_id": "0x00000a88", "product_code": "0x0a880002",
      "sdos": [],
      "dc_sync": { "assign_activate": "0x0300" },
      "txpdo": { "index": "0x1A00", "entries": [
        { "index": "0x6041", "subindex": 0, "type": "u16", "label": "statusword" },
        { "index": "0x6061", "subindex": 0, "type": "i8", "label": "mode_display" },
        { "index": "0x6064", "subindex": 0, "type": "i32", "label": "position_actual_value" },
        { "index": "0x606C", "subindex": 0, "type": "i32", "label": "velocity_actual_value" },
        { "index": "0x60FD", "subindex": 0, "type": "u32", "label": "digital_inputs" },
        { "index": "0x603F", "subindex": 0, "type": "u16", "label": "error_code" } ] },
      "rxpdo": { "index": "0x1600", "entries": [
        { "index": "0x6040", "subindex": 0, "type": "u16", "label": "controlword" },
        { "index": "0x6060", "subindex": 0, "type": "i8", "label": "modes_of_operation" },
        { "index": "0x607A", "subindex": 0, "type": "i32", "label": "target_position" },
        { "index": "0x60FF", "subindex": 0, "type": "i32", "label": "target_velocity" },
        { "index": "0x60FE", "subindex": 1, "type": "u32", "label": "digital_outputs" } ] }
    },
    {
      "alias": 60, "position": 1,
      "name": "EC3A-IO1632", "label": "io_module",
      "vendor_id": "0x00000b95", "product_code": "0x00001101",
      "txpdo": { "index": "0x1A00", "entries": [
        { "index": "0x6000", "subindex": 1, "type": "u16", "label": "inputs" } ] },
      "rxpdo": { "index": "0x1600", "entries": [
        { "index": "0x7000", "subindex": 1, "type": "u16", "label": "outputs" } ] }
    }
  ]
}
```

## Deriving a config from the hardware

`odycat-config scan` does this for you, as a normal user (no sudo: members of
the `odycat` group can read the bus):

```sh
odycat-config scan                     # config on stdout, a report on stderr
odycat-config scan -o machine.json     # write it (--force to overwrite)
odycat-config scan --short             # {"device": ...} for slaves with a device class
odycat-config scan --no-profiles       # ignore the device classes
odycat-config scan --positions         # ring positions instead of alias:offset
odycat-config scan --update machine.json   # machine.json, up to date with the bus
```

The config is written out in full, as the engine binary needs it:
`sudo odycat machine.json` works, and Python never runs as root.

- A slave whose vendor id and product code match a device class is written
  out from that class: identity, PDOs with the class's entry names (the ones
  its methods and the examples use), DC settings and required SDOs. With
  `--short` it is only `{"device": ..., "alias": ..., "position": ...,
  "label": ...}`, which `odycat-config expand` writes out before the engine
  can take it. `--no-profiles` ignores the classes: such
  slaves are then read from the bus like any other, with the entry names the
  slave reports.
- Any other slave is written in full with the PDOs it has assigned now to SM2
  (outputs) and SM3 (inputs). Entry types and names come from its CoE object
  dictionary, then from the standard CiA 402 drive objects (`statusword`,
  `position_actual_value`, ...); an entry with neither gets the unsigned type of
  its size and the report says so. PDOs in other sync managers are left out,
  with a note.
- `bus` is `bus<master>`, e.g. `bus0` (`--bus NAME` for another;
  `--master N` scans another IgH master and writes `"master": N`);
  `cycle_us` is 1000 (`--cycle-us` for another).
- Labels are generated: rename them before writing scripts against them.
- `dc_sync` is written only for a slave that cannot run without it: a CoE
  slave with DC whose 0x1C32:04 (synchronization types supported) has no
  free-run gets `"dc_sync": 0` (Sync0 = the bus cycle), and the report says
  so. For other drives with DC the report points out that CSP/CSV/CST
  usually need it.
- A drive whose PDO map has neither 0x6060 nor 0x6061 (modes of operation)
  usually has a fixed single-mode PDO set, for example CSP only; the report
  says so, since `Servo.set_mode()` needs 0x6060 in the map or in `sdos`.

### Updating a config after the bus changed

When slaves are added, swapped or removed, `--update` brings an existing
config up to date without losing the work in it:

```sh
odycat-config scan --update machine.json > machine.new.json
diff machine.json machine.new.json
```

- A slave still at the same address (alias:offset, or ring position for
  alias 0, as the config writes it) with the same identity (vendor id and
  product code, also through `"device"`) **keeps its entry as written**:
  label, SDOs, PDO edits, `dc_sync`.
- A new slave is added with a generated label that does not clash with
  yours; a different device at a known address replaces that entry; an entry
  whose slave is no longer on the bus is removed. The report flags each one
  (`ADDED`, `REPLACED`, `REMOVED`) and ends with the counts.
- Top-level settings (`bus`, `cycle_us`, `history_s`, ...) are kept, and
  the bus is read from the config's `"master"`; `--bus`, `--cycle-us` and
  `--master` override them.
- The input file is never changed: the result goes to stdout or `-o FILE`
  (`-o machine.json --force` replaces it). It is written in the scan's
  layout, so a hand-formatted file shows layout changes in a diff too.

By hand:

1. `ethercat slaves -v` → vendor id, product code, alias, ring position.
2. `ethercat pdos -p <pos>` → current PDO assignment and entries with their
   bit lengths. Copy them, choosing the `type` from the data type in the
   object dictionary (`ethercat sdos -p <pos>`) or the vendor's documentation.
3. For CiA 402 drives with configurable mapping, map the objects your code
   needs (see [devices.md](devices.md#servo-cia-402)).
4. Start the engine. Its bus scan shows each slave as `OK`, `MISMATCH` or
   `not in config`, and the per-slave line shows the input (tx) and output (rx) byte sizes.
