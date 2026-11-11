# Changelog

All notable changes to OdyCAT. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-11

### Added

- Device classes for the Beckhoff EL1014 (4 DI) and EL2004 (4 DO), tested
  on the real terminals, and ES1014 / ES2004 for their versions with
  pluggable wiring.

### Fixed

- Slaves without a mailbox (simple digital I/O terminals) can exchange
  process data: their PDOs are in SM0/SM1, and the engine assigned PDOs to
  SM2 and SM3 only, so `odycat-config scan` left those PDOs out. A PDO's
  sync manager is now set with `"sm"` (default 2 for `rxpdo`, 3 for
  `txpdo`), and a direction may span several sync managers (the EL2809 has
  a byte of outputs in SM0 and one in SM1). `odycat-config scan` writes it,
  and the engine stops at startup when the slave has no such sync manager
  for that direction, naming the one where the slave has the PDO.
- The `beckhoff.EL1809` and `beckhoff.EL2809` device classes name the
  terminals' sync managers; without them the terminals stayed in PREOP.
- `odycat-config scan` fills a sync manager that ends inside a byte (four
  1-bit channels) with a gap to the end of it, instead of writing a config
  the engine refuses.
- The engine no longer asks slaves without a mailbox for their free-run
  setting, which printed "Failed to execute SDO upload" for each of them.

## [1.0.1] - 2026-11-10

### Fixed

- When a script ends, a CiA 402 drive's target position and position offset
  (0x607A, 0x60B0) keep their last value instead of going back to 0. They
  went out in the same frame as controlword 0, so a drive still enabled in
  that cycle could take 0 as a new position setpoint (a fault, or a move
  towards position 0). The other outputs still go back to 0 and the drive
  disables.
- The engine's messages reach a file or the systemd journal as they are
  written; redirected, they were held until the engine stopped.
- The engine's warning when a DC slave leaves OP without real-time
  scheduling blames late cycles only when a recent cycle was longer than a
  Sync0 period; a slave that no longer answers is reported as a cable or
  power problem, and one that left OP with cycles on time points to its AL
  status code.

### Changed

- The package summary on PyPI: "Open machine and factory automation with
  Python: EtherCAT on Linux".

## [1.0.0] - 2026-11-09

First public release. OdyCAT drives an EtherCAT bus from Python on Linux:
a real-time engine in C runs the IgH EtherCAT Master, and any number of
Python scripts read inputs and write outputs through shared memory, without
ever making the engine wait.

### Engine

- Runs the bus at a fixed cycle (125 µs to 10 ms) with real-time scheduling,
  on the IgH EtherCAT Master; one engine per master, several buses on one
  machine.
- Configured by one JSON file: slaves by position or station alias, several
  PDOs per direction, bit-level entries, startup SDOs, distributed clocks
  or free-run per slave.
- Checks every slave against the config at startup and reports what differs.
  A configured slave that is missing or does not reach OP is reported with
  its AL status code and the bus runs without it; the engine keeps asking
  for OP and the slave joins once it stays there. `--strict` stops instead. A different device at a configured address
  always stops the engine.
- Prints what needs attention at startup (slaves missing, unknown or out of
  OP, warnings); `-v` adds the whole bus scan, the startup SDOs, DC and
  process-data layout of every slave, each state reached and the paths.
- Watches the working counter and every slave's state, and records the last
  seconds of the bus, every cycle, in a trace that scripts can read.
- Outputs reach the bus whole: a script's write is never sent half done, and
  slaves in one sync group get the outputs of one write in the same cycle.
  A slave that drops out of OP comes back with its outputs off.
- Installs as a systemd service running as its own user with only the
  capabilities real time needs; the shared memory is only open to one group.

### Python package

- `Bus`, `Device` and device classes by vendor (`odycat.devices`), with a
  CiA 402 drive class (`Servo`) and generic I/O classes for the standard
  profiles. A device class names each I/O point as it is printed on
  the device (`io.in03()`, `do.set_r0(True)`), so the name in the script is
  the one on the terminal.
- Every device class states its support tier (`SUPPORT`): `tested` classes
  are tested on the real device before each release, at the revisions they
  list; `community` classes are maintained by a contributor. The device
  catalogue and `odycat-config scan` show the tier, and scan says when a
  slave's revision is not one that was tested.
- `Events`: handlers on input and output changes, conditions, and slaves
  leaving and returning to OP; `Trace` to read the recorded cycles;
  `BusMonitor` for the state of the whole bus.
- Several scripts can share an output module; the outputs a script wrote
  are set back to 0 when it ends.
- `odycat-config scan` writes a config from the slaves on the bus, with
  `dc_sync` for slaves that do not run in free-run;
  `odycat-config expand` writes out entries that name a device class.
- Scripts can run in containers (Docker, Compose), attached to an engine on
  the machine through its run directory (docs/containers.md).
- `odycat-config prepare`: the one-off change some devices need before IgH
  can use them (the SII of the Delta AX-516 modules); shows the change and
  keeps a backup, writes only with `--write` while the engine is stopped.
- `odycat.testing`: a fake engine, to test scripts and device classes without
  hardware.
- Linux on x86-64 and aarch64; Python 3.11 or later, no runtime dependencies.

### Installer

- `install.sh` (a release file, `odycat-install-X.Y.Z.tar.gz`) sets up
  Debian 13, Ubuntu 24.04 and 26.04 LTS, Raspberry Pi OS and Armbian: the
  IgH master at the pinned version (kernel modules through DKMS, so kernel
  upgrades rebuild them), the bus network card, and the engine (the release
  binary, checked against `SHA256SUMS`, or built on the machine). It ends with
  the `pip install` command for the package of the same release.
- Engine binaries for x86-64 and aarch64 in every release.
