# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/cia402/__init__.py
"""CiA 402 drive profile: any servo, stepper or VFD that follows it. Vendor
drives build on Servo (e.g. leadshine.ECT60)."""

from .servo import Servo

__all__ = ['Servo']
