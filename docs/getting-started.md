# Getting started

## Quick install

On Debian 13, Ubuntu 24.04 or 26.04 LTS, Raspberry Pi OS or Armbian (x86-64
or aarch64), the installer sets up the IgH master and the engine. Download
`odycat-install-X.Y.Z.tar.gz` and `SHA256SUMS` from the release, then:

```sh
sha256sum -c --ignore-missing SHA256SUMS
tar -xzf odycat-install-X.Y.Z.tar.gz
sudo odycat-install-X.Y.Z/install.sh
```

It asks which network card is the bus, builds the IgH kernel modules with
DKMS (a kernel upgrade rebuilds them), installs the engine and the
permissions, checks that the master runs, and ends by printing the command
that installs the Python package of the same release in a virtual
environment. Its options (`--help`) and what it changes are in
[install/README.md](../install/README.md). Then continue at
[3. Write a config](#3-write-a-config).

The rest of this page is the manual way, and explains what the installer
does.

## 1. Requirements

| What | Notes |
|---|---|
| Linux: Debian 13, Ubuntu 24.04 or 26.04 LTS, Raspberry Pi OS or Armbian ([compatibility.md](compatibility.md)); x86_64 or aarch64 | A PREEMPT_RT kernel is recommended for low jitter, not required; see [realtime.md](realtime.md). ARM boards: see below |
| IgH EtherCAT Master ≥ 1.6 | Kernel module `ec_master` + userland `libethercat` and `ecrt.h` (tested with 1.6.13, installed under `/usr/local`) |
| `libcjson-dev`, `gcc`, `make` | To build the engine |
| Python ≥ 3.11 | No runtime dependencies. A small C helper (`odycat._atomic`) is built when the package is installed; on ARM it is required to write outputs, so install `build-essential` and `python3-dev` first |

### IgH master device

The engine uses **IgH master 0** unless the config sets `"master"`. Which
network card a master uses is decided by IgH, not by OdyCAT: set
`MASTER0_DEVICE` (MAC address) and `DEVICE_MODULES` in `/etc/ethercat.conf`
(or `/etc/sysconfig/ethercat`) and start the master (`sudo systemctl start
ethercat`). Check with:

```sh
ethercat master      # link up, slaves found
ethercat slaves      # positions, aliases, states
```

The `bus` field in the OdyCAT config **names** the bus: its shared memory
regions (`/run/odycat/<bus>_shared`, `_config`, `_trace`) and the name
scripts attach to. It does not choose the network card: IgH does, and the
config's `"master"` picks the master.

### ARM boards (Raspberry Pi, Orange Pi, ...)

A 64-bit OS (aarch64). Things that differ from a PC:

- **The bus NIC.** IgH usually has no native driver for a board's Ethernet:
  use `DEVICE_MODULES="generic"`. Stop the network manager from configuring
  that NIC (it would send DHCP requests on the bus), for NetworkManager:

  ```ini
  # /etc/NetworkManager/conf.d/90-ethercat-unmanaged.conf
  [keyfile]
  unmanaged-devices=interface-name:end0
  ```

  An unmanaged NIC stays down at boot, and the generic driver needs it up.
  Wait for the link too: the master reads every slave's identity (SII) as
  soon as it starts, and a read that goes unanswered leaves that slave
  unidentified (`ethercat slaves` shows `0x00000000:0x00000000`, the kernel
  log "SII read datagram failed") until `ethercat rescan`:

  ```ini
  # /etc/systemd/system/ethercat.service.d/link-up.conf
  [Service]
  ExecStartPre=/sbin/ip link set end0 up
  ExecStartPre=/bin/sh -c "for i in $(seq 100); do [ \"$(cat /sys/class/net/end0/carrier)\" = 1 ] && exec sleep 1; sleep 0.1; done"
  ```
- **Building IgH.** It needs the headers of the running kernel (Armbian:
  `linux-headers-current-<family>`). With a kernel newer than the IgH
  release, the generic driver may not compile; check the IgH repository for
  a fix before patching.
- **Real-time scheduling.** If the engine warns that `SCHED_FIFO` was refused
  although it runs as root, see "Run the engine with real-time scheduling"
  in [realtime.md](realtime.md). With few cores, giving the engine a core of
  its own matters more than on a PC.
- **The Python package** builds `odycat._atomic` when installed; without a C
  compiler it installs without it and refuses to write outputs, with a
  message saying so.

### Several buses

Each network card is its own IgH master (`MASTER1_DEVICE`, ... in
`/etc/ethercat.conf`) and gets its own engine, with its own config, cycle
time and shared memory. Give each config its master and a bus name
(`"master": 1, "bus": "io"`; `odycat-config scan --master 1` writes both,
with `bus1` unless `--bus` says otherwise), start one engine per config, and
pass `bus=` in Python (see the Python API, "Bus auto-detection").

### Permissions

The engine keeps its shared memory as files on tmpfs in its run directory,
`/run/odycat` (mode `0750`: only the engine creates files there). The regions
belong to a group (default `odycat`; the data region, where scripts write
outputs, is mode `0660`, the others `0640`). Every user that runs a
Python script, or the engine, must be in it:

```sh
sudo groupadd odycat
sudo usermod -aG odycat $USER     # then log out and in again
```

The engine also needs access to `/dev/EtherCAT0` and, for real-time
operation, `CAP_SYS_NICE` (scheduling) and `CAP_IPC_LOCK` or a large enough
memlock limit (to lock its memory). Running the engine with `sudo` covers both; the
shm is still given to the `odycat` group, so Python scripts can run as a
normal user. To run the engine without root, the user needs read/write access
to `/dev/EtherCAT0`, for example with this udev rule:

```sh
sudo cp packaging/udev/99-ethercat.rules /etc/udev/rules.d/   # GROUP="odycat", MODE="0660"
sudo udevadm control --reload && sudo udevadm trigger
```

## 2. Build and install

OdyCAT has two parts, installed separately. Install both from the same
version: they share the shared-memory layout.

**The engine** (C, started with `sudo`). Build it as your user, then install
it with `sudo`: it goes to `/usr/local/bin`, owned by root, so that only root
can replace the program `sudo` runs (`make -C engine deps` names any missing
build dependency):

```sh
make -C engine
sudo make -C engine install     # /usr/local/bin/odycat (PREFIX=... for another place)
```

**The Python package**, into your project's virtualenv:

```sh
pip install odycat
```

From this checkout instead, `make install` builds the wheel and reinstalls it
into the active virtualenv (or `.venv`, or `python3`). `make` lists the other
tasks.

For development, use the repo directly: put `src/` on `PYTHONPATH`,
build with `make engine` and start
`engine/odycat`.

## 3. Write a config

Let OdyCAT write a first one from the slaves on the bus (no sudo needed), then
rename the labels:

```sh
odycat-config scan -o machine.json
```

It writes slaves with a device class out from the class (same entry names as
the class and the examples) and the others from what they report, all in
full, so the engine binary takes it as it is (see
[configuration.md](configuration.md#deriving-a-config-from-the-hardware)).
To look at the slaves yourself:

```sh
ethercat slaves -v      # vendor id, product code, alias
ethercat pdos -p 0      # PDO mapping of slave 0
```

For a device OdyCAT has a class for (see [devices.md](devices.md)), name it
with `device` and give its address and a label; the class fills in the
identity, PDO mapping and DC settings. Minimal config with one Beckhoff EL2809
16-channel output terminal:

```json
{
  "bus": "bus0",
  "cycle_us": 1000,
  "slaves": [
    { "device": "beckhoff.EL2809", "position": 0, "label": "outputs" }
  ]
}
```

Any other slave is described in full: identity and the PDOs to map, with
`rxpdo` for its outputs (RxPDOs `0x16xx`) and `txpdo` for its inputs (TxPDOs
`0x1Axx`), as `ethercat pdos` lists them:

```json
{
  "position": 1,
  "label": "my_io",
  "vendor_id": "0x00000b95",
  "product_code": "0x00001101",
  "rxpdo": { "index": "0x1600", "entries": [
    { "index": "0x7000", "subindex": 1, "type": "u16", "label": "do" } ] },
  "txpdo": { "index": "0x1A00", "entries": [
    { "index": "0x6000", "subindex": 1, "type": "u16", "label": "di" } ] }
}
```

See [configuration.md](configuration.md) for every field.

## 4. Start the engine

```sh
sudo odycat machine.json
```

`odycat` is the compiled engine, so `sudo` runs no Python. It takes
configs written in full, as `odycat-config scan` writes them. If you wrote a
config that names device classes (`"device": "leadshine.ECT60"`), expand it
as your user before starting the engine:

```sh
odycat-config expand config.json -o machine.json
```

`sudo` finds it in `/usr/local/bin` (step 2). In a repository checkout it
can also run from `engine/odycat`.

It prints what needs attention, and little else when all is well:

```
OdyCAT: engine 1.0.0 (git ...; security fixes until ...)
OdyCAT: loaded config — bus 'machine', 6 slave(s), cycle 1000 µs
OdyCAT: bus scan — 6 slave(s) found, 6 configured
OdyCAT: bus in OP.
OdyCAT: entering RT loop (cycle = 1000 µs).
```

A slave missing or out of OP gets its own line in the scan and after `bus in
OP`, and every `WARNING:` is printed. A device of another identity at a
configured address stops the engine before it configures anything. `odycat -v
machine.json` also prints the whole bus scan, the startup SDOs, DC and
process-data layout of every slave, each state reached and the
shared-memory paths: use it while commissioning, or when something is
wrong.

Any startup problem ends the engine with `ERROR: ...` and exit code 1 (see
[troubleshooting.md](troubleshooting.md)). Stop it with Ctrl-C or `SIGTERM`;
closing its terminal (`SIGHUP`) stops it the same way: the engine deactivates
and releases the master, then removes its shared-memory regions and lock.

Only one engine can run per bus name (lock file `/run/odycat/<bus>.lock`).

### Three ways to start it

| | How | Run directory |
|---|---|---|
| **By hand** (test benches, systems without systemd) | `sudo odycat machine.json` | `/run/odycat`, created by the engine (`0750`, `root:odycat`) if missing |
| **As a service** (machines) | `sudo make -C engine install-service` once, then a config in `/etc/odycat/<name>.json` (`root:odycat`, `0640`) and `systemctl enable --now odycat@<name>` | `/run/odycat`, created by systemd (`0750`, owned by the `odycat` system user) and removed when the service stops |
| **Development, without sudo** | `ODYCAT_RUN_DIR=$XDG_RUNTIME_DIR/odycat engine/odycat machine.json` | Your private runtime directory: only your own scripts can attach. Set the same `ODYCAT_RUN_DIR` for them |

The service runs as the system user `odycat`, with only the two capabilities
real time needs (`CAP_SYS_NICE`, `CAP_IPC_LOCK`), in a sandbox: no root at
any point. Members of the `odycat` group can start and stop it without sudo
(`systemctl start odycat@<name>`, polkit rule). The CPU of its real-time
loop goes in a drop-in (`CPUAffinity=`, see [realtime.md](realtime.md)).

Python finds the regions in `$ODYCAT_RUN_DIR`, else `/run/odycat`, and
refuses a region that the engine did not create (see
[troubleshooting.md](troubleshooting.md)).

### Slaves out of OP

If a configured slave is missing or does not reach OP, the engine says
which one and why (its state and AL status code) and runs the bus without
it. Scripts get `DeviceLostError` on that slave, so a script decides
whether the machine can run without it. The slave joins once it has stayed
in OP for 500 ms: a slave that dropped out on its own is retried by IgH
straight away, and the engine asks IgH again for every other one (after
1 s, then 2, 4, ... up to once a minute), so a slave that refused a state
change is tried again too. `sudo odycat --strict machine.json` stops the
engine instead, which is what you want while commissioning. A different
device at a configured address always stops it.

A slave that stays out of OP is a fault to fix, not a state to run in:
the start-up report and `DeviceLostError` give the reason, and
`BusMonitor` shows every slave's state and AL status code for an HMI or a
health check.

### The kernel log

IgH reports every attempt to bring a slave to OP in the kernel log. A
slave that keeps dropping out of OP (it accepts OP and refuses it a moment
later, like a drive that needs `dc_sync` and has none) is retried several
times a second, and each attempt writes about 15 lines: some 25 lines a
second, for as long as the fault lasts. That is IgH working as intended,
but plan for it:

- The kernel log is a ring of fixed size, so a flapping slave pushes older
  messages out within minutes (the engine's own start-up, a network
  driver's errors). `journalctl -k` keeps what journald stored; set its
  limits (`SystemMaxUse=` in `/etc/systemd/journald.conf`) to what the disk
  can hold.
- rsyslog, where installed, copies kernel messages to `/var/log/kern.log`
  and `/var/log/syslog`: about 20 MB an hour while a slave flaps. On boards
  that keep `/var/log` in RAM (Armbian's zram log, about 50 MB) that fills
  it in two or three hours, and then other services cannot log. Rotate
  those files by size, or keep kernel messages out of rsyslog if journald
  has them.
- On flash storage, persistent logs of that rate wear it: keep the journal
  on a RAM disk or with tight limits on SD cards and eMMC.
- Kernel messages at error level go to the console only if the kernel's
  console log level lets them (`loglevel=` on the kernel command line). On a
  serial console that printing is slow and can delay the engine's cycles:
  keep the console level low (`loglevel=3` or less) on machines that run a
  bus.

## 5. First script

```python
from odycat import Bus
from odycat.devices.beckhoff import EL2809

bus = Bus()                       # auto-detects the running engine
print(bus.is_op, bus.work_counter, bus.expected_wkc)

out = EL2809(label="outputs")     # or EL2809(position=0)
out.set_output_1(True)            # channel 1 on (or out.set_output(1, True))
print(out.output_1(), f"{out.outputs():016b}")
```

Everything (bus, shm offset, PDO layout) is discovered from the running
engine. If no engine runs, constructors raise `RuntimeError`. For a slave
without a class, `Device(label="my_io")` reads and writes its entries by
label: `read_entry("di")`, `write_entry("do", 0x0001)`.

Next: [python-api.md](python-api.md) and [devices.md](devices.md).

## Tests

Unit tests need no hardware, no IgH and no root:

```sh
./ci/test.sh            # pytest if installed, else stdlib unittest
```

See [testing.md](testing.md) for what the tests cover and what CI runs.

## Safety notes

- **Outputs a script wrote go back to 0 when it ends** (normal end, uncaught
  exception, Ctrl+C, SIGTERM, SIGHUP), unless it called
  `keep_outputs_on_exit()`; a drive gets controlword 0 and disables. See
  [python-api.md](python-api.md#outputs-when-the-script-ends).
- **Outputs keep their last value when the script cannot run that cleanup:**
  `kill -9`, an out-of-memory kill, a crash, `os._exit()`, or a script that
  hangs. The engine keeps cycling with those values, so the slave's bus-loss
  watchdog will not detect a stalled script. A `try/finally` or
  `servo.disable()` cannot handle a kill or hang. Assess each machine's
  failure modes and provide independent protective measures where needed;
  zero output or a disabled drive is not necessarily a safe state.
- Stopping the engine takes the slaves to PREOP; the resulting physical
  behaviour depends on each device's configuration and its manual. Verify it
  on your machine rather than assuming all outputs switch off safely.
- OdyCAT is not a safety function: see [intended-use.md](intended-use.md).
- Several scripts may attach to the same engine. Nothing stops two of them
  from writing the same output; the last write wins.
