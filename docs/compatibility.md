# Versions and compatibility

OdyCAT follows [Semantic Versioning](https://semver.org/). A version is
`MAJOR.MINOR.PATCH`:

- **PATCH** releases fix bugs.
- **MINOR** releases add features. Code and configs written for an earlier
  minor release of the same major keep working.
- **MAJOR** releases may remove or change what is covered below. The
  CHANGELOG says what changed and how to update.

## What is covered

- **The Python API**, as documented: the classes and functions of `odycat`,
  `odycat.bus`, `odycat.device`, `odycat.devices` (the device classes),
  `odycat.events`, `odycat.signal`, `odycat.trace` and `odycat.monitor`,
  with their parameters, return values and documented exceptions. This
  includes the keys of `BusMonitor.snapshot()` and `layout()` listed in the
  [Python API](python-api.md).
- **The config file** (`config.json`): its keys and what they mean
  ([configuration.md](configuration.md)).
- **The commands**: `odycat` (the engine) and `odycat-config`, with their
  arguments.
- **The environment variable** `ODYCAT_RUN_DIR`.

## What is not covered

- **Anything whose name starts with `_`**: private modules (`odycat._shm`,
  `odycat._config`, `odycat._scan`...) and private members. The functions
  of `odycat._config` documented in the Python API are covered under the
  names listed there.
- **The engine's shared memory**: the layout of the regions, offsets and
  flag values. Scripts use it through the package only. Keeping it internal
  is what lets later releases improve how the engine and the scripts share
  data without breaking scripts.
- **`odycat.testing`**: the fake engine follows the engine and the test
  tooling, and may change in any release.
- **The text of messages**, the format of the engine's console output and of
  `odycat-config` reports. Parse the documented values, not the text.

## The engine and the package

Install the engine and the Python package **from the same release**. The
package checks the engine's shared memory layout when it attaches and
refuses a layout of another major version, saying which side is newer. The
layout's major version changes only in a major release, so within one
major an engine and a package of different minor releases still attach,
but only the same release is tested together.

## Deprecation

Something covered is not removed without notice:

1. It is marked deprecated in a minor release: it keeps working, raises a
   `DeprecationWarning` when used (Python) or prints a warning (config,
   commands), and the CHANGELOG lists it under *Deprecated* with what to
   use instead.
2. It stays at least until the next major release, where it may be removed.

## Supported versions

Fixes, security fixes included, go into the latest minor release of the
current major version: update within the major to get them, which the rules
above keep free of changes to your code. See [SECURITY.md](../SECURITY.md)
for which releases receive security fixes and how to report a vulnerability.

| Requirement | Supported |
|---|---|
| Python | 3.11 and later |
| Operating systems | Debian 13; Ubuntu 24.04 and 26.04 LTS; Raspberry Pi OS and Armbian based on them |
| Architectures | x86-64 and aarch64 |
| IgH EtherCAT Master | 1.6 (the installer installs 1.6.13) |
| cJSON (engine) | 1.7, as packaged by the distribution, with its security updates (tested: 1.7.18 on Debian 13, 1.7.17 on Ubuntu 24.04) |

The engine links IgH's `libethercat` and cJSON from the system; OdyCAT does
not ship them. The installer takes IgH from its own repository, at the
pinned version, and builds it on the machine. Each release is checked against the known vulnerabilities
of both, and an advisory says when one affects OdyCAT users.
