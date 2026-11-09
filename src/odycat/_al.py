# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/_al.py
"""
Internal: EtherCAT application-layer (AL) states and status codes, worded
as the engine prints them (and close to what `dmesg` shows from IgH).
"""

AL_STATE_NAMES = {1: "INIT", 2: "PREOP", 3: "BOOT", 4: "SAFEOP", 8: "OP"}
AL_ERROR = 0x10        # in the AL state: the slave refused a transition

# AL status codes (ETG.1000.6), register 0x0134
AL_STATUS_CODES = {
    0x0001: "Unspecified error", 0x0002: "No memory",
    0x0011: "Invalid requested state change", 0x0012: "Unknown requested state",
    0x0013: "Bootstrap not supported", 0x0014: "No valid firmware",
    0x0015: "Invalid mailbox configuration", 0x0016: "Invalid mailbox configuration",
    0x0017: "Invalid sync manager configuration", 0x0018: "No valid inputs available",
    0x0019: "No valid outputs", 0x001A: "Synchronization error",
    0x001B: "Sync manager watchdog", 0x001C: "Invalid sync manager types",
    0x001D: "Invalid output configuration", 0x001E: "Invalid input configuration",
    0x001F: "Invalid watchdog configuration", 0x0020: "Slave needs cold start",
    0x0021: "Slave needs INIT", 0x0022: "Slave needs PREOP",
    0x0023: "Slave needs SAFEOP", 0x0024: "Invalid input mapping",
    0x0025: "Invalid output mapping", 0x0026: "Inconsistent settings",
    0x0027: "Freerun not supported", 0x0028: "Synchronization not supported",
    0x0029: "Freerun needs 3 buffer mode", 0x002A: "Background watchdog",
    0x002B: "No valid inputs and outputs", 0x002C: "Fatal sync error",
    0x002D: "No sync error", 0x0030: "Invalid DC SYNC configuration",
    0x0031: "Invalid DC latch configuration", 0x0032: "PLL error",
    0x0033: "DC sync IO error", 0x0034: "DC sync timeout error",
    0x0035: "DC invalid sync cycle time", 0x0036: "DC Sync0 cycle time",
    0x0037: "DC Sync1 cycle time", 0x0041: "Mailbox AoE", 0x0042: "Mailbox EoE",
    0x0043: "Mailbox CoE", 0x0044: "Mailbox FoE", 0x0045: "Mailbox SoE",
    0x004F: "Mailbox VoE", 0x0050: "EEPROM no access", 0x0051: "EEPROM error",
    0x0060: "Slave restarted locally",
}


def al_state_name(al_state: int) -> str:
    """"SAFEOP", "SAFEOP + error", "-" before the engine read it."""
    if not al_state:
        return "-"
    name = AL_STATE_NAMES.get(al_state & 0x0F, "?")
    return f"{name} + error" if al_state & AL_ERROR else name


def al_status_text(code: int) -> str:
    """The meaning of an AL status code."""
    if code in AL_STATUS_CODES:
        return AL_STATUS_CODES[code]
    return "vendor specific" if code >= 0x8000 else "unknown code"


def describe(al_state: int, online: bool, al_code: int) -> str:
    """How a slave that is not operational stands: 'offline', 'in SAFEOP +
    error, AL status 0x0027 (Freerun not supported)'. In OP without being
    operational: back in OP for less than the engine's hold time (500 ms)."""
    if not online:
        return "offline"
    if al_state == 8:
        text = "in OP for less than 500 ms (it counts as back once it stays)"
    else:
        text = f"in {al_state_name(al_state)}"
    if al_code:
        text += f", AL status 0x{al_code:04X} ({al_status_text(al_code)})"
    return text
