# Installer

`install.sh` sets up a machine to run OdyCAT: the IgH EtherCAT Master and
the OdyCAT engine. The Python package goes into your own environment
afterwards; the installer ends by printing the command.

**Supported:** Debian 13; Ubuntu 24.04 and 26.04 LTS; Raspberry Pi OS and
Armbian based on them; x86-64 and aarch64.

```sh
sudo ./install.sh                 # asks which card is the bus
sudo ./install.sh --nic enp3s0    # no question
```

| Option | |
|---|---|
| `--nic IFACE` | The network card for the bus. Without it, the installer lists the wired cards that do not carry the default route and asks |
| `--build-engine` | Build the engine on this machine instead of downloading the release binary |
| `--igh-only` | The IgH master and the bus card only, no engine |
| `--keep-igh` | Use an IgH master installed before, not by this installer, as it is |
| `--rt` | Also install the distribution's real-time kernel (Debian; on Ubuntu it comes with Ubuntu Pro) |
| `--yes` | Ask nothing; needs `--nic` when several cards qualify |
| `--dry-run` | Print what would be done and change nothing |
| `--unsupported` | Run on a system not listed above |

Running it again repairs or upgrades: each step skips what is already
done. The log is in `/var/log/odycat-install.log`.

## What it changes

| Step | Changes |
|---|---|
| `10-packages.sh` | Installs build tools, DKMS, the kernel headers (of the running kernel, and the headers metapackage of each kernel metapackage, so later kernels get theirs), libcjson, python3-venv |
| `20-igh.sh` | IgH at the version and commit pinned in `versions.env`: source in `/usr/src/ethercat-<version>`; kernel modules `ec_master` and `ec_generic` through DKMS; userland (`libethercat`, `ethercat`, `ethercatctl`, `/etc/ethercat.conf`) in `/usr/local`; `/etc/systemd/system/ethercat.service`. Refuses an IgH installed by other means (see `--keep-igh`) |
| `30-nic.sh` | `MASTER0_DEVICE` (the bus card's MAC address) and `DEVICE_MODULES="generic"` in `/etc/ethercat.conf`; the card left unmanaged by NetworkManager and systemd-networkd; a drop-in that sets the card up before the master starts; enables and starts `ethercat.service` |
| `40-odycat.sh` | `/usr/local/bin/odycat` (the release binary, checked against the release's `SHA256SUMS`, or built with `--build-engine`); the `odycat` user and group, the udev rule for `/dev/EtherCAT*`, the `odycat@` service, the polkit rule, `/etc/odycat`; adds you to the `odycat` group |
| `50-check.sh` | Checks the modules, the master and the engine; prints the next steps |

Only IgH's **generic** Ethernet driver is built: it works with every card.
IgH's native drivers (lower latency, specific cards and kernels) are set up
by hand; see [docs/realtime.md](../docs/realtime.md).

Not touched: the network configuration of other cards, the firewall, the
boot loader and CPU isolation (real-time tuning: [docs/realtime.md](../docs/realtime.md)).
If the bus card is listed in `/etc/network/interfaces`, the installer warns:
remove it there.

## Uninstalling

```sh
sudo systemctl disable --now ethercat
sudo dkms remove ethercat/<version> --all && sudo rm -rf /usr/src/ethercat-<version>
sudo make -C /path/to/odycat/engine uninstall uninstall-service   # or remove /usr/local/bin/odycat and the files above
```

The IgH userland in `/usr/local` is removed with `make uninstall` in a
configured IgH source tree of the same version.
