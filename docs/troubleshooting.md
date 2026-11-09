# Troubleshooting

## Engine startup errors

The engine prints a bus scan before configuring anything: the slaves that
need attention, and with `-v` (`sudo odycat -v machine.json`) every slave.
Read it first:

```
OdyCAT: bus scan — 6 slave(s) found, 6 configured
  [ 0] #60    0x00000A88:0x0A880002  PREOP  ECT60 ...      OK -> stepper_x (60:0)
  [ 1]        0x00000B95:0x00001101  PREOP  ...            OK -> io_module (60:1)
  [ 2] #1     0x000001DD:0x10518000  PREOP  ...            not in config (stays in PREOP)
```

| Message | Cause / fix |
|---|---|
| `config missing field '<x>'` / `... in slave <i>` | Required field absent or wrong type (`vendor_id`, `product_code` must be strings) |
| `config JSON parse error near: ...` | Invalid JSON (trailing comma, comment, ...) |
| `slave <i> uses "device": <X>, a device class from the Python package` | The engine takes configs written in full: `odycat-config expand config.json -o machine.json` (as your user), then start it with `machine.json` |
| `unknown device '<X>' (known: ...)` | No class with that name: use one of the listed names, or write the slave entry in full |
| `<rxpdo/txpdo> index 0xNNNN is not an RxPDO / a TxPDO` (or `is outside ...`) | `rxpdo` holds the slave's outputs (RxPDOs `0x16xx`), `txpdo` its inputs (TxPDOs `0x1Axx`): move the PDO to the other key |
| `another OdyCAT instance is already running for bus '<x>' (pid N)` | An engine already runs for that bus (as a service too: `systemctl status 'odycat@*'`). Stop it |
| `cannot create run directory '/run/odycat'` | Without sudo the engine cannot create it: start it with `sudo`, as the service, or set `ODYCAT_RUN_DIR=$XDG_RUNTIME_DIR/odycat` (and the same for your scripts) |
| `the engine cannot create files in run directory '/run/odycat'` | The directory belongs to root or to the service user: same as above |
| `run directory '<d>' is writable by its group or by others` / `... belongs to uid N` / `... is not on tmpfs` | The run directory must be private to the engine and in RAM: `chmod 0750` it, or point `ODYCAT_RUN_DIR` at a directory of yours under `/run` |
| `'<file>' already exists and was not left by an OdyCAT engine (owner uid N)` | A file the engine did not create sits where a region goes. Find out where it comes from before removing it |
| `group 'odycat' does not exist` | `sudo groupadd odycat; sudo usermod -aG odycat $USER`, log in again; or set `shm_group` |
| `cannot give '...' to group '...'` | The engine user is not in the group (or did not log in again). Run with sudo or fix the group |
| `could not acquire IgH master N ...` | `ec_master` not loaded (`sudo systemctl start ethercat`), master N not configured (`MASTERn_DEVICE` in `/etc/ethercat.conf`), no access to `/dev/EtherCATn`, or another engine/program owns that master (two configs with the same `"master"`) |
| `'<slave>' is on the engine of bus 'X'; this Trace/Events reads 'Y'` | A device of one bus passed to the `Events`/`Trace` of another. Create one `Events(bus=...)` per bus |
| `N configured slave(s) missing or with wrong identity` | See the scan: `MISMATCH` = different vendor/product at that address (always fatal); `MISSING` = address not on the bus (fatal only with `--strict`). Check wiring order, alias, power |
| `WARNING: N configured slave(s) not on the bus: the engine runs without them` | The bus runs; scripts get `DeviceLostError` (offline) on those slaves until they appear. Check wiring, power, alias. `--strict` refuses to start instead |
| `WARNING: slave A:P (label) did not reach SAFEOP in 15 s` / `left OP after N ms: the engine runs without it`, then `N slave(s) not in OP` with the state and AL status code | The bus runs without that slave and the engine keeps asking for OP; it joins once it stays in OP for 500 ms. Each attempt is logged in `dmesg`, several times a second for a slave that keeps dropping out (see [getting-started.md](getting-started.md#the-kernel-log)). The AL status code says why: "Freerun not supported" (0x0027) = the slave needs `dc_sync`; "Invalid input/output configuration" (0x001E/0x001D) = the PDO mapping does not match the slave; "Synchronization error" / "DC ..." codes = DC settings or late cycles |
| `alias N is set on more than one slave` | Aliases must be unique; reprogram one (`ethercat alias -p <pos> <alias>`) |
| `slaves 'a' and 'b' have the same address` | Duplicate `alias:position` in the config |
| `... entries do not end on a byte boundary (N trailing bits)` | Add a gap entry `{"index": "0x0000", "subindex": 0, "bits": N}` |
| `... 'type' must be one of ...` / `needs a 'type' ... or 'bits'` / `has "bits": 16: give a value of that size its type` | See the type table in [configuration.md](configuration.md) |
| `... has entries but no PDO 'index'` | Give each PDO with entries its `index` (`"0x1A00"`, `"0x1600"`, ...) |
| `too many PDOs in one direction` / `too many PDO entries in one direction` | Limits are 32 PDOs and 256 entries per direction |
| `... entry 0xXXXX:YY registration failed` / `at bit N, expected M` | Entry not in the PDO the slave ended up with; the slave probably ignored the mapping (fixed PDOs). Copy the mapping from `ethercat pdos` |
| `... SDO N: 8-byte SDOs are not supported` | Use 1/2/4-byte types |
| `... SDO N: 'value' X does not fit u16 (0..65535)` (or another type) | The value is out of range for the declared `type`. Fix the value or the type; nothing is truncated |
| `... SDO N: 'value' must be an integer ...` / `'index' must be ...` / `'subindex' must be ...` | Write numbers as JSON numbers, or as strings in decimal or `0x` hex with nothing else in them |
| `cycle_us=N is not supported` | Use one of 125, 250, 500, 1000, 2000, 4000, 8000, 10000 µs |
| `could not lock memory (mlockall)` / `could not set SCHED_FIFO ... require_rt is set` | Fatal only with `require_rt`. Run as root, or give the engine `CAP_IPC_LOCK` (or a large `RLIMIT_MEMLOCK`) and `CAP_SYS_NICE` |
| `ecrt_master_activate failed` | Usually an invalid PDO configuration |
| `could not reach PREOP` (with `--strict`) | Slaves not responding: wiring, power, link |
| `slave A:P (label) is <state>, expected SAFEOP` then `could not reach SAFEOP` (with `--strict`) | PDO mapping rejected, an SDO aborted, or (DC drives) missing/wrong `dc_sync`. The state includes the AL status code; `dmesg` shows IgH's side (e.g. SDO abort code) |
| `... expected OP` / `could not reach OP` (with `--strict`) | Often DC: the drive needs Sync0 (`dc_sync`) or a different `assign_activate`; or a watchdog/SM problem |
| `slave A:P (label) reached OP and left it after N ms` (with `--strict`) | The slave accepted OP, then refused it with an error; the AL status code in the message says why. "Freerun not supported" (0x0027) means the slave needs `dc_sync` |
| `EngineStoppedError: the engine of bus 'X' stopped` / `has not run a cycle for N ms` | The engine stopped, was killed, crashed or hangs. Start it again, then restart the script |
| `... was left by an engine that is no longer running (pid N)` | The engine was killed or crashed and left its region. Start the engine again |
| `WARNING: could not lock memory (mlockall)` | Not fatal (unless `require_rt`). Page faults can delay cycles. Run as root or grant `CAP_IPC_LOCK` / a larger memlock limit |
| `WARNING: could not set SCHED_FIFO priority 80` | Not fatal (unless `require_rt`). Run as root or grant `CAP_SYS_NICE` / an rtprio limit for real-time scheduling. Without it, give DC slaves a longer `sync0_cycle_ns` (see the configuration reference) |
| Outputs of a slave stop changing after some minutes, the slave stays in OP and WKC is fine | The slave latched a sync error after late cycles: `ethercat upload -p N --type bool 0x1c32 0x20` reads 1. Clear it with `ethercat states -p N SAFEOP` then `OP`. The engine puts slaves without `dc_sync` in free-run to prevent this; check the startup log says so for that slave |
| `device names are "vendor.Model" ...; got "ECT60". Did you mean 'leadshine.ECT60'?` | Configs name device classes with their vendor: `"device": "leadshine.ECT60"` |
| `CSP needs DC: add "dc_sync": ...` | Cyclic synchronous modes need DC: add `"dc_sync": 0` (or a Sync0 period in ns) to that slave and run the engine with real-time scheduling |
| `WARNING: slave ... left OP. It uses DC ... without real-time scheduling` (engine), same text in `DeviceLostError` | A late cycle left a Sync0 period without data. Run the engine with `sudo` / `CAP_SYS_NICE`, or remove `dc_sync` if the slave does not need it (profile modes and I/O don't) |
| A DC drive drops to INIT/PREOP every few minutes and comes back (error code `0x7500`) | Sync0 periods without new data because of late cycles. Real-time scheduling, or a longer `dc_sync.sync0_cycle_ns` |
| `dc_sync Sync0 period N ns must be a whole multiple of the bus cycle` | Use `cycle_us × 1000` times 1, 2, 4... |

`dmesg -w` (IgH kernel log) and `ethercat slaves`, `ethercat pdos -p N`,
`ethercat sdos -p N`, `ethercat upload -p N 0x6041 0` help to diagnose slaves.

## Delta AX-516 modules stuck without process data

They reach PREOP but never SAFEOP / show no PDOs: their SII needs completing
once, with `odycat-config prepare --position <ring position>` (see
[Preparing a device](tools.md#preparing-a-device-odycat-config-prepare)).
`AX516AM`/`AX516AN` constructors raise `RuntimeError` if the config does not
map 2 bytes.

## Python errors

| Error | Cause / fix |
|---|---|
| `no running engine detected (no *_config in <run dir>)` | Start the engine; check it reached the RT loop. An engine started with `ODYCAT_RUN_DIR` needs the same `ODYCAT_RUN_DIR` in the script |
| `... refusing to use it` (symbolic link, open to all users, run directory writable by others, belongs to uid N) | The region was not created by the engine, or the run directory is not private. Do not work around it: find out who created the file |
| `multiple engines detected (...)` | Pass `bus=` |
| `no permission to open /run/odycat/...` | Add the user to the shm group and log in again |
| `KeyError: no slave with address A:P ...` | Address must match the config entry as written (alias + position), or use `label=` |
| `KeyError: no entry '<name>' (available: ...)` | Use one of the listed labels or `"0xINDEX:SUB"` |
| `ObjectNotMappedError: object 0xXXXX:YY is not in this slave's PDO map` (also from `Events` when watching it) |
| `TypeError: in03 (slave '...') is the variable itself, not its value` | A device variable without `()` used as a value (`if io.in03:`). Write `io.in03()` | Add it to the config (`txpdo` for inputs, `rxpdo` for outputs) or set it as a startup SDO, restart the engine |
| `ValueError: ... is an input; only outputs can be written` | Inputs come from the slave; they cannot be written |
| `struct.error: unpack requires a buffer of N bytes` | A fixed-format class does not match the config mapping. Fix the config to the documented layout |
| `RuntimeError: OdyCAT: the engine stopped in the middle of copying the inputs` | The engine made no progress for 10 cycles (at least 50 ms) while copying the inputs: it was killed or froze mid-cycle. Restart it. A busy machine or an engine without RT only makes reads wait, never fail |
| `RuntimeError: ... did not reach Operation-Enabled (statusword=0x....)` | Drive not ready: fault, missing power stage supply, STO active, DC not configured. Check `error_code()` |
| `RuntimeError: drive did not accept mode N` | The drive does not support that mode, or 0x6061 is not mapped correctly |

After the engine restarts, existing `Bus` and `Device` objects do not
reconnect. Device access raises `EngineStoppedError`; create new objects.
`BusMonitor` re-attaches by itself.

## Runtime symptoms

| Symptom | Check |
|---|---|
| `bus.wkc_ok` false / `error_flags & 1` | A slave is not exchanging data (lost, not OP). `BusMonitor().snapshot()` shows which |
| `error_flags & 2` | A configured slave is offline or not operational, or fewer slaves respond than configured |
| `cycle_count` not increasing | Engine stopped or stalled; check its terminal |
| Outputs written but nothing happens | Wrong slave, or the wrong I/O point (names follow what is printed on the device, see [devices.md](devices.md#io-names)); output overwritten by another script; drive not enabled |
| Drive enables but does not move in CSP/CSV | Mode not set (check `mode_display()`); DC missing; target not changing; limits active (`digital_inputs()`) |
| Values one cycle late | Expected: inputs are from the last received frame, outputs go out with the next frame |
