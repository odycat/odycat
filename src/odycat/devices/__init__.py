# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/__init__.py
"""
Device classes, by vendor::

    from odycat.devices.leadshine import ECT60
    from odycat.devices.beckhoff import EL1809, EL2809
    from odycat.devices.cia402 import Servo        # standard profiles

In a config a device class is named "vendor.Model" ("leadshine.ECT60").
Vendors are the subpackages of odycat.devices. A vendor package exports its
device classes from its __init__; those with a PROFILE can be named in a
config.

Every device class states its support tier in SUPPORT (see TIERS):

- "tested": tested on the real device before each release. TESTED_REVISIONS
  lists the device revisions tested (classes with a PROFILE); TESTED_ON
  lists the device classes whose devices a generic class was tested on.
- "vendor": maintained by its manufacturer, named in MAINTAINER, which tests
  it on the real device before each release; TESTED_REVISIONS lists the
  device revisions it tested.
- "community": maintained by a contributor, tested by unit tests only.
- "unmaintained": no maintainer; deprecated for one release if it breaks,
  then removed.
"""

import functools
import importlib
import pkgutil
from typing import Optional

TIERS = ("tested", "vendor", "community", "unmaintained")


def vendors() -> dict:
    """{vendor: module} of the bundled vendors."""
    return {m.name: f"{__name__}.{m.name}"
            for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name)
            if m.ispkg}


@functools.lru_cache(maxsize=None)
def device_classes() -> dict:
    """{"vendor.Model": class} of every device class with a PROFILE."""
    from odycat.device import Device
    out = {}
    for vendor, module in vendors().items():
        mod = importlib.import_module(module)
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, Device) and "PROFILE" in vars(obj):
                out[f"{vendor}.{obj.__name__}"] = obj
    return out


def support(cls) -> Optional[str]:
    """The support tier a class declares itself (not inherited: a subclass
    written outside OdyCAT has none), or None."""
    return vars(cls).get("SUPPORT")


def maintainer(cls) -> Optional[str]:
    """The manufacturer that maintains a "vendor" class, or None."""
    return vars(cls).get("MAINTAINER")


def support_short(cls) -> str:
    """The tier with the manufacturer of a "vendor" class: "tested",
    "vendor (Acme)", "none"..."""
    tier = support(cls) or "none"
    return f"vendor ({maintainer(cls)})" if tier == "vendor" else tier


def support_text(cls) -> str:
    """The tier as the catalogue and scan show it: "tested (revision
    0x00000202)", "vendor (Acme; revision 0x00000010)", "community", ..."""
    tier = support(cls) or "none"
    if tier not in ("tested", "vendor"):
        return tier
    details = [maintainer(cls)] if tier == "vendor" else []
    revisions = vars(cls).get("TESTED_REVISIONS") or ()
    if revisions:
        word = "revision" if len(revisions) == 1 else "revisions"
        details.append(f"{word} {', '.join(f'0x{r:08X}' for r in revisions)}")
    else:
        on = vars(cls).get("TESTED_ON") or ()
        if on:
            details.append(f"on {', '.join(on)}")
    return f"{tier} ({'; '.join(details)})" if details else tier
