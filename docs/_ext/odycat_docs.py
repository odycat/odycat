# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Sphinx extension for the OdyCAT docs.

- Generates generated/device-catalogue.md from the device classes: identity,
  DC, variables (@signal), write methods and PDO mapping of each class, so
  the catalogue is always the code's own description.
- Links from the docs to files outside docs/ (../LICENSING.md, ../examples/...)
  point at the repository browser (conf.py: odycat_repo_url).
"""

import posixpath
from pathlib import Path

from docutils import nodes
from sphinx import addnodes


def _first_paragraph(doc) -> str:
    if not doc:
        return ""
    return " ".join(doc.strip().split("\n\n")[0].split())


def _cell(text) -> str:
    return str(text).replace("|", "\\|")


def _in_definition_order(cls, predicate):
    """(name, value) of cls and its bases below Device, in source order,
    overrides last (Device's own generic methods are in the API reference)."""
    from odycat.device import Device
    seen = {}
    for klass in reversed(cls.__mro__):
        if klass is object or not issubclass(klass, Device) or klass is Device:
            continue
        for name, value in vars(klass).items():
            if predicate(name, value):
                seen.pop(name, None)
                seen[name] = value
    return list(seen.items())


def _entries(profile: dict, key: str) -> list:
    from odycat.testing import direction_entries
    return direction_entries(profile, key)


def _object(entry) -> tuple:
    idx = entry.get("index", 0)
    return (int(idx, 0) if isinstance(idx, str) else int(idx), int(entry.get("subindex", 0)))


def _class_section(key: str, cls) -> list:
    from odycat.devices import support_text
    from odycat.signal import SignalSpec
    vendor, model = key.split(".", 1)
    profile = cls.PROFILE
    inputs = {_object(e) for e in _entries(profile, "txpdo")}
    outputs = {_object(e) for e in _entries(profile, "rxpdo")}
    dc = getattr(cls, "DC_SYNC", None)
    if dc:
        aa = dc.get("assign_activate", 0)
        aa = int(aa, 0) if isinstance(aa, str) else int(aa)
        dc_text = (f"supported (`assign_activate` 0x{aa:04X}); "
                   "turn it on with `\"dc_sync\"` in the config")
    else:
        dc_text = "not used (free-run)"
    out = [f"### {model}", "", _first_paragraph(cls.__doc__), "",
           "| | |", "|---|---|",
           f"| Config name | `{key}` |",
           f"| Import | `from odycat.devices.{vendor} import {model}` |",
           f"| Identity | vendor id `{profile.get('vendor_id')}`, "
           f"product code `{profile.get('product_code')}` |",
           f"| DC | {dc_text} |",
           f"| Support | {support_text(cls)} |", ""]

    signals = _in_definition_order(cls, lambda n, v: isinstance(v, SignalSpec))
    if signals:
        out += ["Variables: `dev.name()` reads the value, `dev.name` is the variable "
                "for `Events` and the trace. \"not mapped\": the class declares it, but "
                "its default mapping does not include the object (add it to the config "
                "entry's PDOs to use it).", "",
                "| Variable | Object | Direction | Description |", "|---|---|---|---|"]
        for name, spec in signals:
            obj = (spec.index, spec.subindex)
            direction = ("input" if obj in inputs else "output" if obj in outputs
                         else "not mapped")
            out.append(f"| `{name}` | {spec.describe()} | {direction} | "
                       f"{_cell(_first_paragraph(spec.__doc__))} |")
        out.append("")

    writers = _in_definition_order(
        cls, lambda n, v: n.startswith("set_") and callable(v))
    if writers:
        out += ["Writing:", "", "| Method | Description |", "|---|---|"]
        for name, func in writers:
            out.append(f"| `{name}()` | {_cell(_first_paragraph(func.__doc__))} |")
        out.append("")

    for key_, title in (("txpdo", "Inputs (TxPDO)"), ("rxpdo", "Outputs (RxPDO)")):
        entries = [e for e in _entries(profile, key_) if _object(e)[0]]
        if not entries:
            continue
        out += [f"{title} mapped by the class:", "",
                "| Object | Entry | Size |", "|---|---|---|"]
        for e in entries:
            idx, sub = _object(e)
            size = e.get("type") or f"{e.get('bits')} bit"
            out.append(f"| 0x{idx:04X}:{sub:02X} | `{e.get('label', '')}` | {size} |")
        out.append("")
    return out


def write_catalogue(app) -> None:
    from odycat.devices import device_classes
    classes = device_classes()
    lines = ["# Device catalogue", "",
             "Every device class in this version of OdyCAT, generated from the classes "
             "themselves. Name a device in a config as `\"vendor.Model\"`; the class "
             "fills in its identity, PDO mapping and DC settings (see "
             "[Device classes](../devices.md)). Standard-profile classes for any "
             "conforming device (`cia402.Servo`, `mdp.DigitalIO`, `cia401.GenericIO`) "
             "are in the [API reference](../api.md). **Support** is the class's tier "
             "(see [Device classes](../devices.md)): a tested class lists the device "
             "revisions it was tested with.", ""]
    for vendor in sorted({k.split(".", 1)[0] for k in classes}):
        lines += [f"## {vendor}", ""]
        for key in sorted(k for k in classes if k.split(".", 1)[0] == vendor):
            lines += _class_section(key, classes[key])
    target = Path(app.srcdir) / "generated" / "device-catalogue.md"
    target.parent.mkdir(exist_ok=True)
    text = "\n".join(lines) + "\n"
    if not target.exists() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")


def _repo_url(app, docname: str, target: str):
    """The repository browser URL of a link to a file outside docs/, or None."""
    if not target or "://" in target or target.startswith("#"):
        return None
    repo = Path(app.srcdir).parent
    path, _, anchor = target.partition("#")
    if path.startswith("/"):            # a .md link, already resolved by MyST
        path = posixpath.relpath(path, str(repo))
    else:
        path = posixpath.normpath(posixpath.join("docs", posixpath.dirname(docname), path))
    if path.startswith("docs/") or path.startswith("..") or path == "docs":
        return None
    if not (repo / path).exists() and (repo / (path + ".md")).is_file():
        path += ".md"
    if not (repo / path).exists():
        return None
    return app.config.odycat_repo_url + path + (f"#{anchor}" if anchor else "")


def link_outside_docs(app, doctree):
    """Links to repository files outside docs/ (../LICENSING.md, ../examples/...)
    open the repository browser, instead of becoming missing documents or
    file downloads."""
    docname = app.env.docname
    for node in list(doctree.findall(addnodes.pending_xref)) + \
            list(doctree.findall(addnodes.download_reference)):
        if node.get("reftype") != "myst":
            continue
        url = _repo_url(app, docname, node.get("reftarget", ""))
        if url:
            node.replace_self(nodes.reference("", "", *node.children,
                                              internal=False, refuri=url))


def setup(app):
    app.add_config_value("odycat_repo_url", "", "env")
    app.connect("builder-inited", write_catalogue)
    app.connect("doctree-read", link_outside_docs, priority=100)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
