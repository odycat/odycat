# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/__init__.py
"""
OdyCAT — EtherCAT framework for Linux.

Bridges a real-time C-based IgH EtherCAT master engine and a high-level
Python library using a lock-free, zero-wait shared memory architecture.

The core is imported from here; the rest from its module::

    from odycat import Bus, Device, Events
    from odycat.events import MultiPoll
    from odycat.device import DeviceLostError, EngineStoppedError
    from odycat.devices.leadshine import ECT60     # devices, by vendor

https://odycat.org · developed by Ocean Octane, distributed and supported by
Yoctovision S.L.U. OdyCAT is a registered trademark of Ocean Octane S.L.U.,
used under licence by Yoctovision. Licensed under the GPL v3, or under a
commercial licence from Yoctovision S.L.U. (see LICENSING.md).
"""

from ._version import __version__

__author__ = "Javier Martínez García"
__copyright__ = "Copyright (c) 2026 Ocean Octane S.L.U."
__license__ = "GPL-3.0-only OR LicenseRef-OdyCAT-Commercial"

from .bus import Bus
from .device import Device
from .events import Events

__all__ = ["Bus", "Device", "Events"]
