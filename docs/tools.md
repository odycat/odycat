# Tools

## Config from the bus (`odycat-config`)

`odycat-config scan` prints a config for the slaves IgH sees, using device
classes where they match; `--update machine.json` brings an existing config
up to date with the bus, keeping the entries of slaves that did not change.
`odycat-config expand config.json -o machine.json` writes a config that
names device classes out in full, for the engine. Both run as a normal user. See
[configuration.md](configuration.md#deriving-a-config-from-the-hardware).

## Preparing a device (`odycat-config prepare`)

Some devices need a one-off change before IgH can use them. The device
classes that need one say so, and `odycat-config scan` names the slaves that
are not prepared yet. Today: the Delta AX-516AM and AX-516AN, whose SII (the
description IgH reads from the slave's EEPROM) has no sync manager and PDO
sections, so IgH cannot exchange their process data.

```sh
odycat-config prepare --position 4             # what would change; backup in ./sii_backup
odycat-config prepare --position 4 --write     # program the EEPROM, then read it back
odycat-config prepare --position 4 --restore sii_backup/<file>_orig.bin
```

`--position` is the slave's ring position, as `ethercat slaves` numbers it.
When a slave needs preparing, the command keeps a backup of its SII and the
new image in `--backup-dir` (default `sii_backup/`); the backup is how a
change is undone.
It writes only with `--write` or `--restore`, and only while no application
holds the master: stop the engine first. A slave that is already prepared,
or needs nothing, is left alone. Runs as a normal user in the `odycat` group.

## Building

| Command | Output |
|---|---|
| `make engine` | `engine/odycat` |
| `sudo make -C engine install` | The engine as `/usr/local/bin/odycat`, root-owned (`PREFIX=`, `DESTDIR=` as usual); `uninstall` removes it |
| `make wheel` | `dist/odycat-<version>-cp311-abi3-<platform>.whl`: the Python package with its C helper (no engine); `ODYCAT_PURE=1` builds the pure `py3-none-any` one |
| `make install` | the wheel, reinstalled into the active virtualenv (or `.venv`, or `python3`) |

The engine's flags and dependency checks live in `engine/Makefile`; CI and
`make engine` call it. A missing dependency (gcc, the IgH `ecrt.h` and
`libethercat`, `libcjson-dev`) is named with the package to install;
`IGH=/opt/etherlab make engine` points at another IgH prefix. The engine
needs the IgH userland (`/usr/local/lib`) and the kernel module on the
machine it runs on; the wheel needs only Python. Install the engine and the
package from the same version: they share the shared-memory layout.

## Hardware testing

The unit tests run without hardware ([testing.md](testing.md)). The master
side (PDO registration, DC, SDOs at startup, timing, many scripts sharing a
bus) is checked on hardware benches with real I/O modules and drives:
non-actuating layout checks, tests that switch outputs and move drives,
stability runs of several hours and load tests with hundreds of scripts.
Those scripts depend on the wiring of each bench and are not part of this
repository.
