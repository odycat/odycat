# Contributing to OdyCAT

Thanks for taking an interest. Bug reports, corrections, documentation and
focused code changes are welcome. If you'd like to talk about an application
or show us a machine, [contact Yoctovision](https://www.yoctovision.com)
directly; the issue tracker is for OdyCAT itself.

**Security problems:** do not open an issue; see [SECURITY.md](SECURITY.md).

## Ways to contribute

- **Report a bug:** use the [bug report](.github/ISSUE_TEMPLATE/bug_report.yml)
  and include a small reproduction when you can.
- **Suggest an idea:** use the
  [feature request](.github/ISSUE_TEMPLATE/feature_request.yml) for changes
  to OdyCAT. For a new device, check [Devices](#devices): you can maintain a
  community class or ask Yoctovision about development and testing as a service.
- **Contribute code or documentation:** read [Before a pull request](#before-a-pull-request)
  and [Pull requests we can review](#pull-requests-we-can-review).

Please do not post customer configurations, credentials or other information
you do not have permission to share publicly.

## Before a pull request

- **How decisions are made.** OdyCAT is led by its maintainers, Ocean Octane
  and Yoctovision: they decide what is merged and where the project goes. See
  [GOVERNANCE.md](GOVERNANCE.md).
- **Contributor License Agreement.** OdyCAT is dual licensed (GPL v3 and
  commercial, see [LICENSING.md](LICENSING.md)), so no pull request is merged
  without a signed [CLA](CLA.md). When we decide to consider your pull
  request, we ask you to sign it and email it to <contributions@odycat.org>.
  You keep the copyright of your work; the CLA lets Ocean Octane S.L.U., which
  owns OdyCAT, license your contribution so that it is published under both
  licences. If you contribute on behalf of your employer, ask at the same
  address for the corporate version.
- For anything bigger than a small fix, open an issue first. We can discuss
  the approach before you invest time in a large change.

## Pull requests we can review

Small, focused contributions are easier for us to review:

- **Small and focused.** One change per pull request; anything beyond a
  small fix tied to an issue we have agreed on. Propose larger changes
  (rewrites, mass reformatting, renames across the code) in an issue first;
  we may close unannounced changes we cannot review.
- **At most three open pull requests per person** at a time.
- **Tested.** Unit tests pass (`make test`); for device classes and anything
  that touches the bus, say which hardware and firmware you tested on.
- **AI tools are fine, used with care.** If an AI tool helped you, say so
  in the pull request. You must have read, understood and tested every
  line, be able to explain it in review, and have the right to contribute it
  (the CLA asks you to confirm that).
## Where things are

| Folder | What |
|---|---|
| `engine/` | The real-time C engine and its Makefile |
| `src/odycat/` | The Python package; device classes in `devices/<vendor>/` |
| `tests/` | Unit tests, no hardware (fake engine: `odycat.testing`) |
| `examples/` | Example scripts and configs |
| `docs/` | The documentation; the website is built from it |
| `packaging/` | System files (udev rule, systemd unit, sysusers.d, polkit rule) |
| `install/` | The installer (IgH, bus card, engine) for Debian, Ubuntu, Raspberry Pi OS and Armbian |
| `ci/` | The scripts the CI runs; they work the same locally |

## Devices

OdyCAT is only as useful as the devices it drives, so every device class
says how it is maintained and tested. The initial catalogue reflects the
devices on the maintainers' test bench; a class's tier describes how the
class is maintained and tested, not its manufacturer's involvement. The
device catalogue shows the tier:

| Tier | Maintained by | Tested |
|---|---|---|
| **tested** | The maintainers | On the real device, before each release, with the device revisions listed in the catalogue |
| **community** | A contributor | With unit tests at each release; not on the real device by the maintainers |
| **unmaintained** | Nobody | With unit tests at each release. If it breaks, it is deprecated for one release, then removed |

"Tested" means that the class read and wrote the device's process data as
expected in our tests, on the revisions listed.

### Development and testing as a service

For device-class development or bench testing by Yoctovision S.L.U., write to
<devices@odycat.org>.

### Community device classes

A pull request can add a device class in the **community** tier if:

- you have the device and maintain the class: you check it on the device
  when we ask before a release, and you answer the issues about it;
- it follows "Writing a new device class" in [docs/devices.md](docs/devices.md),
  with unit tests on the fake engine, and says which hardware and firmware
  you tested it on.

A community class whose maintainer does not confirm it for two minor
releases becomes unmaintained.

You can also write a device class for your own machine and keep it in your
own code; the same section of [docs/devices.md](docs/devices.md) explains
how. Fixes to existing device classes (a wrong object index, a missing
entry) are welcome as pull requests; say which hardware and firmware you
tested them on.

## Checks

```sh
make test        # unit tests, no hardware needed
make lint        # syntax, imports, ruff if installed
make docs        # the documentation site, warnings are errors
                 #   (pip install -r docs/requirements.txt)
```

`make` lists every task (engine, wheels, install).

## Style

Match the surrounding code. Python 3.11+, standard library only in `src/odycat/`.
Every source file starts with:

```python
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
```

Files in `examples/` use `# SPDX-License-Identifier: MIT` instead (see
[LICENSING.md](LICENSING.md#examples)).
