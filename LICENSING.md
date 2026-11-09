# Licensing

OdyCAT is copyright © 2026 Ocean Octane S.L.U. and is available under two
licences. You choose the one that fits your use.

OdyCAT is published and supported by **Yoctovision S.L.U.**, under licence
from Ocean Octane S.L.U.: Yoctovision publishes the releases, grants the
commercial licences and handles support and security (see
[SECURITY.md](SECURITY.md)). Company details: <https://www.yoctovision.com>.

## 1. GNU GPL v3 (open source)

You may use, study, modify and redistribute OdyCAT for free under the terms
of the GPL v3 only (GPL-3.0-only). The full text is in [LICENSE](LICENSE),
and it is the text that applies. In brief:

- **Using OdyCAT on your own machines** (lab, workshop, factory line, DIY
  projects) without distributing it places no obligations on your
  application code.
- **Distributing OdyCAT or a combined work based on it** brings GPL obligations,
  including making the corresponding source available to recipients under the
  GPL. Whether a particular application forms a combined work depends on how
  it is integrated and distributed; merely installing separate programs on
  the same computer does not automatically make them a single work. In our
  understanding, a program that imports the `odycat` Python package is a
  work based on OdyCAT.

## 2. Commercial licence

For machine builders, OEMs and integrators who need to distribute a combined
proprietary application without relying on the GPL terms for OdyCAT. The
written agreement defines its scope. Commercial licences are granted by
Yoctovision S.L.U.; separate services may include support, custom device
classes, commissioning tools and OdyCAT-specific information to help with
the conformity assessment of your product. A commercial licence does not
certify the complete machine or replace its manufacturer's assessment.

Contact: <licensing@odycat.org> · <https://odycat.org>

## Examples

The scripts and configs in [`examples/`](examples) and the code snippets in
the documentation are under the MIT licence ([text](LICENSES/MIT.txt)): they
are there to be copied into your own code. Each file states its licence in
its `SPDX-License-Identifier` header.

A program built from an example still uses the `odycat` package, which stays
under the GPL v3 or a commercial licence.

## Contributions

Contributions are welcome. So that OdyCAT can keep being offered under both
licences, no contribution is merged without a signed
[Contributor License Agreement](CLA.md). See [CONTRIBUTING.md](CONTRIBUTING.md).

## Third-party software

OdyCAT depends on the software below but does not include or redistribute
it. It is installed separately on the target machine, under its own licence:

| Component | Licence | Used as |
|---|---|---|
| [IgH EtherCAT Master](https://gitlab.com/etherlab.org/ethercat) userland (`libethercat`) | LGPL-2.1 | Shared library the engine links to dynamically |
| IgH EtherCAT Master kernel module (`ec_master`) | GPL-2.0 | Kernel module, reached via `/dev/EtherCAT0` |
| [cJSON](https://github.com/DaveGamble/cJSON) | MIT | Shared library the engine links to dynamically |

## Trademarks

OdyCAT® is a registered trademark of Ocean Octane S.L.U. Do not use it as
the name of your product, do not change the logo, and do not suggest that
Ocean Octane or Yoctovision endorses your product. The logo files are in
[docs/img](docs/img).

EtherCAT® is a registered trademark and patented technology, licensed by
Beckhoff Automation GmbH, Germany.

Every other company, product and protocol name in OdyCAT (in the docs, the
generated device catalogue and the packages under `odycat/devices/`) is the
trademark of its owner and is used only to identify the hardware a device
class drives or has been tested with. It implies no affiliation,
sponsorship or endorsement.
