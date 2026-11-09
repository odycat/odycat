# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""`python3 -m odycat`: says how to start the engine, which is not Python."""

import sys

sys.exit("""The OdyCAT engine is the odycat command (a compiled program, not Python):

    sudo odycat machine.json

A config that names device classes ("device": "leadshine.ECT60") is written
out in full first, as your user:

    odycat-config expand config.json -o machine.json

It is installed apart from this package, root-owned, from the source:

    make -C engine && sudo make -C engine install     # /usr/local/bin/odycat

In a repository checkout it can also run from engine/odycat.""")
