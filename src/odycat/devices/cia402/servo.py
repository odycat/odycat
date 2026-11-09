# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/devices/cia402/servo.py
"""
Generic CiA 402 drive — PP, PV, PT, HM, CSP, CSV, CST.

Objects are addressed by their CiA 402 index (0x6064, 0x6071, ...) and
looked up in the slave's PDO map from the engine config, so the class works
with any mapping and any entry order. Only mapped objects are reachable:
OdyCAT has no runtime SDO access, so an object that is not in the PDO map
raises ObjectNotMappedError (a KeyError) naming it. Map it in the config's "txpdo"/"rxpdo", or,
for parameters set once (max torque, torque slope, profile accelerations),
write it as a startup SDO in "sdos".

Direction names follow the config: "txpdo" = from the drive (inputs:
statusword, actual values), "rxpdo" = to the drive (outputs: controlword,
targets, limits).

Multi-axis CiA 402 drives repeat the profile every 0x800 objects (axis 1 at
0x6000, axis 2 at 0x6800, ...); pass axis=1 for the second axis.

Units are the drive's: positions in user units, velocities in user units/s,
torques and currents in 0.1 % of the rated torque/current (0x6076/0x6075).

Reading is named after the object, writing keeps its verb, as in Qt::

    drv = Servo(label="axis_x")
    drv.set_mode("CST")
    drv.enable()
    drv.set_target_torque(150)          # 15 % rated torque
    print(drv.torque_actual_value())    # the value now
    if drv.is_fault(): ...

Without (), drv.torque_actual_value is the variable itself, for odycat.Events
(@ev.change(drv.torque_actual_value)); see odycat.signal.
"""

from __future__ import annotations

import time
from typing import Optional

from odycat._config import read_config, slave_config
from odycat._profiles import dc_sync_settings
from odycat.device import Device, ObjectNotMappedError
from odycat.signal import signal

# CiA 402 operation modes (0x6060 / 0x6061)
OP_MODE = {
    "PP":  1,   # Profile Position
    "VL":  2,   # Velocity
    "PV":  3,   # Profile Velocity
    "TQ":  4,   # Profile Torque
    "PT":  4,   # Profile Torque (alias)
    "HM":  6,   # Homing
    "IP":  7,   # Interpolated Position
    "CSP": 8,   # Cyclic Synchronous Position
    "CSV": 9,   # Cyclic Synchronous Velocity
    "CST": 10,  # Cyclic Synchronous Torque
}

# Controlword bit positions (CiA 402)
CW_SWITCH_ON              = 0
CW_ENABLE_VOLTAGE         = 1
CW_QUICK_STOP             = 2
CW_ENABLE_OPERATION       = 3
CW_NEW_SET_POINT          = 4   # PP: new set-point / HM: start homing
CW_CHANGE_SET_IMMEDIATELY = 5   # PP
CW_RELATIVE               = 6   # PP: target is relative
CW_FAULT_RESET            = 7
CW_HALT                   = 8

# Statusword bit positions (CiA 402)
SW_READY_TO_SWITCH_ON = 0
SW_SWITCHED_ON        = 1
SW_OPERATION_ENABLED  = 2
SW_FAULT              = 3
SW_VOLTAGE_ENABLED    = 4
SW_QUICK_STOP         = 5
SW_SWITCH_ON_DISABLED = 6
SW_WARNING            = 7
SW_TARGET_REACHED     = 10
SW_INTERNAL_LIMIT     = 11
SW_SETPOINT_ACK       = 12  # PP: set-point acknowledge / CSx: target ignored
SW_HOMING_ATTAINED    = 12  # HM
SW_FOLLOWING_ERROR    = 13  # PP/CSP: following error / HM: homing error

# CiA 402 object indices (axis 0)
ERROR_CODE              = 0x603F
CONTROLWORD             = 0x6040
STATUSWORD              = 0x6041
MODES_OF_OPERATION      = 0x6060
MODES_DISPLAY           = 0x6061
POSITION_DEMAND_VALUE   = 0x6062
POSITION_ACTUAL_VALUE   = 0x6064
FOLLOWING_ERROR_WINDOW  = 0x6065
VELOCITY_DEMAND_VALUE   = 0x606B
VELOCITY_ACTUAL_VALUE   = 0x606C
TARGET_TORQUE           = 0x6071
MAX_TORQUE              = 0x6072
MAX_CURRENT             = 0x6073
TORQUE_DEMAND_VALUE     = 0x6074
RATED_CURRENT           = 0x6075
RATED_TORQUE            = 0x6076
TORQUE_ACTUAL_VALUE     = 0x6077
CURRENT_ACTUAL_VALUE    = 0x6078
TARGET_POSITION         = 0x607A
MAX_PROFILE_VELOCITY    = 0x607F
MAX_MOTOR_SPEED         = 0x6080
PROFILE_VELOCITY        = 0x6081
PROFILE_ACCELERATION    = 0x6083
PROFILE_DECELERATION    = 0x6084
QUICK_STOP_DECELERATION = 0x6085
TORQUE_SLOPE            = 0x6087
TORQUE_PROFILE_TYPE     = 0x6088
POSITION_OFFSET         = 0x60B0
VELOCITY_OFFSET         = 0x60B1
TORQUE_OFFSET           = 0x60B2
POSITIVE_TORQUE_LIMIT   = 0x60E0
NEGATIVE_TORQUE_LIMIT   = 0x60E1
FOLLOWING_ERROR_ACTUAL  = 0x60F4
DIGITAL_INPUTS          = 0x60FD
DIGITAL_OUTPUTS         = 0x60FE   # subindex 1: physical outputs
TARGET_VELOCITY         = 0x60FF

AXIS_OFFSET = 0x800

# Cyclic synchronous modes: the drive takes a new target every cycle and
# needs DC to apply them at the right moment.
CYCLIC_MODES = {8: "CSP", 9: "CSV", 10: "CST"}


def check_dc_for_mode(mode: int, dc_on: bool, label: str) -> None:
    """Refuse a cyclic synchronous mode on a slave the config runs without DC,
    saying what to change (the drive would otherwise reject it unexplained)."""
    name = CYCLIC_MODES.get(int(mode))
    if name and not dc_on:
        raise RuntimeError(
            f"{name} needs DC: add \"dc_sync\": <Sync0 period in ns> (0 = the bus "
            f"cycle) to slave '{label}' in the config, and run the engine with "
            f"real-time scheduling. PP, PV and HM work without DC.")


class Servo(Device):
    """Generic CiA 402 drive (one axis).

    Every accessor maps to one CiA 402 object; see the module docstring
    for how objects are found and what happens when one is not mapped.
    """

    SUPPORT = "tested"
    TESTED_ON = ("leadshine.ECT60",)

    def __init__(self, position: Optional[int] = None, *,
                 alias: int = 0, label: Optional[str] = None,
                 bus: Optional[str] = None, axis: int = 0):
        super().__init__(position, alias=alias, label=label,
                         bus=bus)
        self._axis_offset = AXIS_OFFSET * axis
        # Bus cycles for an output written to SHM to reach the drive and be
        # sampled: 2 to be sent for sure, plus one Sync0 period for a DC
        # drive, which only takes new data at Sync0.
        cycle_ns = read_config(bus).get("cycle_us", 1000) * 1000
        dc = slave_config(position, bus, alias=alias, label=label).get("dc_sync")
        self._dc = dc is not None
        sync0_ns = (dc_sync_settings(dc, None).get("sync0_cycle_ns") or cycle_ns) \
            if self._dc else 0
        self._settle_cycles = 2 + -(-sync0_ns // cycle_ns)

    # ----------------------------------------------------------------
    #  Object access by CiA 402 index
    # ----------------------------------------------------------------

    def _object(self, index: int, subindex: int, output: bool) -> dict:
        """Entry of an object (axis-relative index) in the wanted direction."""
        key = (index + self._axis_offset, subindex)
        found = self._object_map().get(key)
        where = "rxpdo" if output else "txpdo"
        if found is None:
            hint = ' or, if it only needs setting once, to "sdos"' if output else ""
            raise ObjectNotMappedError(
                f"{type(self).__name__}: object 0x{key[0]:04X}:{subindex:02X} "
                f"is not in this slave's PDO map. Add it to the config "
                f"\"{where}\"{hint}.")
        entry, is_output = found
        if output and not is_output:
            raise ValueError(
                f"{type(self).__name__}: object 0x{key[0]:04X}:{subindex:02X} "
                f"is mapped as an input; map it in \"rxpdo\" to write it.")
        return entry

    def has_object(self, index: int, subindex: int = 0) -> bool:
        """True if the object (axis-relative index) is in the PDO map."""
        return (index + self._axis_offset, subindex) in self._object_map()

    def read_object(self, index: int, subindex: int = 0):
        """Read a mapped object, input or output."""
        key = (index + self._axis_offset, subindex)
        if key not in self._object_map():
            self._object(index, subindex, False)   # raises with the hint
        return self._read_field(*self._object_map()[key])

    def _read_object(self, index: int, subindex: int = 0):
        """Signals read through here (absolute index): same error as read_object()."""
        if (index, subindex) not in self._object_map():
            self._object(index - self._axis_offset, subindex, False)
        return super()._read_object(index, subindex)

    def write_object(self, index: int, value, subindex: int = 0) -> None:
        """Write a mapped output object (immediate)."""
        self._write_field(self._object(index, subindex, True), value)

    def write_object_bit(self, index: int, bit: int, value: bool,
                         subindex: int = 0) -> None:
        """Set or clear one bit of a mapped output object (immediate); no
        other bit changes, e.g. one digital output in 0x60FE:01 while
        another script drives the others."""
        self._write_field_bit(self._object(index, subindex, True), bit, value)

    def stage_object(self, index: int, value, subindex: int = 0) -> None:
        """Stage a mapped output object for Device.transmit_outputs(),
        e.g. to update several axes in the same cycle."""
        self._stage_field(self._object(index, subindex, True), value)

    # ----------------------------------------------------------------
    #  Statusword / controlword
    # ----------------------------------------------------------------

    @signal(STATUSWORD)
    def statusword(self) -> int:
        """0x6041 statusword."""
        ...

    def status_bit(self, bit: int) -> bool:
        """One bit of the statusword."""
        return bool((self.statusword() >> bit) & 1)

    @signal(STATUSWORD, bit=SW_READY_TO_SWITCH_ON)
    def is_ready_to_switch_on(self) -> bool:
        """Statusword bit 0: ready to switch on."""
        ...

    @signal(STATUSWORD, bit=SW_SWITCHED_ON)
    def is_switched_on(self) -> bool:
        """Statusword bit 1: switched on."""
        ...

    @signal(STATUSWORD, bit=SW_OPERATION_ENABLED)
    def is_operation_enabled(self) -> bool:
        """Statusword bit 2: operation enabled (power stage on)."""
        ...

    @signal(STATUSWORD, bit=SW_FAULT)
    def is_fault(self) -> bool:
        """Statusword bit 3: fault."""
        ...

    @signal(STATUSWORD, bit=SW_WARNING)
    def is_warning(self) -> bool:
        """Statusword bit 7: warning."""
        ...

    @signal(STATUSWORD, bit=SW_TARGET_REACHED)
    def is_target_reached(self) -> bool:
        """Statusword bit 10: target reached."""
        ...

    @signal(STATUSWORD, bit=SW_INTERNAL_LIMIT)
    def is_internal_limit_active(self) -> bool:
        """Statusword bit 11: internal limit active."""
        ...

    @signal(STATUSWORD, bit=SW_FOLLOWING_ERROR)
    def is_following_error(self) -> bool:
        """Statusword bit 13: following error (PP/CSP)."""
        ...

    @signal(CONTROLWORD)
    def controlword(self) -> int:
        """0x6040 controlword, as written."""
        ...

    def set_controlword(self, value: int) -> None:
        """Write the 0x6040 controlword."""
        self.write_object(CONTROLWORD, value & 0xFFFF)

    def set_controlword_bit(self, bit: int, value: bool) -> None:
        self.write_object_bit(CONTROLWORD, bit, value)

    @signal(ERROR_CODE)
    def error_code(self) -> int:
        """0x603F error code; 0 when no fault."""
        ...

    # ----------------------------------------------------------------
    #  Operation mode
    # ----------------------------------------------------------------

    @signal(MODES_DISPLAY)
    def mode_display(self) -> int:
        """0x6061 mode the drive is running in."""
        ...

    def set_mode(self, mode: int | str, timeout_s: float = 1.0) -> None:
        """Select the operation mode (e.g. "CSP", "CSV", "CST", "PP") and
        wait until 0x6061 reports it. CSP/CSV/CST need "dc_sync" in the
        slave's config entry."""
        if isinstance(mode, str):
            if mode not in OP_MODE:
                raise ValueError(f"unknown mode '{mode}', valid: {sorted(OP_MODE)}")
            mode = OP_MODE[mode]
        check_dc_for_mode(mode, self._dc, self._slave_label)
        self.write_object(MODES_OF_OPERATION, int(mode))
        deadline = time.monotonic() + timeout_s
        while self.mode_display() != mode:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"{type(self).__name__}: drive did not accept mode {mode} "
                    f"(display={self.mode_display()})")
            self.wait_cycles(1)

    # ----------------------------------------------------------------
    #  CiA 402 state machine
    # ----------------------------------------------------------------

    def reset_fault(self) -> None:
        """Rising edge on the fault-reset bit, each level held until the
        drive has seen it."""
        self.set_controlword_bit(CW_FAULT_RESET, False)
        self.wait_cycles(self._settle_cycles)
        self.set_controlword_bit(CW_FAULT_RESET, True)
        self.wait_cycles(self._settle_cycles)
        self.set_controlword_bit(CW_FAULT_RESET, False)

    def _wait_status(self, bit: int, what: str, deadline: float) -> None:
        while not self.status_bit(bit):
            if time.monotonic() > deadline:
                raise RuntimeError(f"{type(self).__name__}: did not reach {what} "
                                   f"(statusword=0x{self.statusword():04X})")
            self.wait_cycles(1)

    def enable(self, timeout_s: float = 2.0) -> None:
        """Fault reset -> shutdown -> switch on -> enable operation.

        The mapped targets are first set so the drive does not move when
        enabled: target position = actual position, target velocity and
        target torque = 0.
        """
        deadline = time.monotonic() + timeout_s
        while self.is_fault():
            self.reset_fault()
            time.sleep(0.01)
            if time.monotonic() > deadline:
                code = (f" (error code 0x{self.error_code():04X})"
                        if self.has_object(ERROR_CODE) else "")
                raise RuntimeError(f"{type(self).__name__}: fault did not clear{code}")

        if self.has_object(TARGET_POSITION) and self.has_object(POSITION_ACTUAL_VALUE):
            self.write_object(TARGET_POSITION, self.position_actual_value())
        if self.has_object(TARGET_VELOCITY):
            self.write_object(TARGET_VELOCITY, 0)
        if self.has_object(TARGET_TORQUE):
            self.write_object(TARGET_TORQUE, 0)

        self.set_controlword(0x0006)
        self._wait_status(SW_READY_TO_SWITCH_ON, "Ready-to-Switch-On", deadline)
        self.set_controlword(0x0007)
        self._wait_status(SW_SWITCHED_ON, "Switched-On", deadline)
        self.set_controlword(0x000F)
        self._wait_status(SW_OPERATION_ENABLED, "Operation-Enabled", deadline)

    def disable(self) -> None:
        """Shutdown (power stage off, Ready-to-Switch-On)."""
        self.set_controlword(0x0006)

    def quick_stop(self) -> None:
        """Quick stop: the drive decelerates with 0x6085, then disables."""
        self.set_controlword(0x0002)

    def halt(self, state: bool = True) -> None:
        """Set or clear the Halt bit (PP/PV/PT/HM)."""
        self.set_controlword_bit(CW_HALT, state)

    def resume(self) -> None:
        self.halt(False)

    # ----------------------------------------------------------------
    #  Actual / demand values (inputs)
    # ----------------------------------------------------------------

    @signal(POSITION_ACTUAL_VALUE)
    def position_actual_value(self) -> int:
        """0x6064 position actual value (user units)."""
        ...

    @signal(VELOCITY_ACTUAL_VALUE)
    def velocity_actual_value(self) -> int:
        """0x606C velocity actual value (user units/s)."""
        ...

    @signal(TORQUE_ACTUAL_VALUE)
    def torque_actual_value(self) -> int:
        """0x6077 torque actual value (0.1 % of rated torque)."""
        ...

    @signal(CURRENT_ACTUAL_VALUE)
    def current_actual_value(self) -> int:
        """0x6078 current actual value (0.1 % of rated current)."""
        ...

    @signal(POSITION_DEMAND_VALUE)
    def position_demand_value(self) -> int:
        """0x6062 position demand value (user units)."""
        ...

    @signal(VELOCITY_DEMAND_VALUE)
    def velocity_demand_value(self) -> int:
        """0x606B velocity demand value (user units/s)."""
        ...

    @signal(TORQUE_DEMAND_VALUE)
    def torque_demand_value(self) -> int:
        """0x6074 torque demand value (0.1 % of rated torque)."""
        ...

    @signal(FOLLOWING_ERROR_ACTUAL)
    def following_error_actual_value(self) -> int:
        """0x60F4 following error actual value (user units)."""
        ...

    @signal(DIGITAL_INPUTS)
    def digital_inputs(self) -> int:
        """0x60FD digital inputs word (bit 0 negative limit, 1 positive
        limit, 2 home switch, 16+ manufacturer inputs)."""
        ...

    # ----------------------------------------------------------------
    #  Targets (outputs)
    # ----------------------------------------------------------------

    @signal(TARGET_POSITION)
    def target_position(self) -> int:
        """0x607A target position, as written."""
        ...

    @signal(TARGET_VELOCITY)
    def target_velocity(self) -> int:
        """0x60FF target velocity, as written."""
        ...

    @signal(TARGET_TORQUE)
    def target_torque(self) -> int:
        """0x6071 target torque, as written (0.1 % of rated torque)."""
        ...

    def set_target_position(self, position: int) -> None:
        """0x607A target position (PP, CSP)."""
        self.write_object(TARGET_POSITION, int(position))

    def set_target_velocity(self, velocity: int) -> None:
        """0x60FF target velocity (PV, CSV)."""
        self.write_object(TARGET_VELOCITY, int(velocity))

    def set_target_torque(self, torque: int) -> None:
        """0x6071 target torque in 0.1 % of rated torque (PT, CST)."""
        self.write_object(TARGET_TORQUE, int(torque))

    def set_position_offset(self, offset: int) -> None:
        """0x60B0 position offset (CSP feed-forward)."""
        self.write_object(POSITION_OFFSET, int(offset))

    def set_velocity_offset(self, offset: int) -> None:
        """0x60B1 velocity offset (CSP/CSV feed-forward)."""
        self.write_object(VELOCITY_OFFSET, int(offset))

    def set_torque_offset(self, offset: int) -> None:
        """0x60B2 torque offset in 0.1 % (CSP/CSV/CST feed-forward)."""
        self.write_object(TORQUE_OFFSET, int(offset))

    # ----------------------------------------------------------------
    #  Torque parameters
    # ----------------------------------------------------------------

    def set_max_torque(self, torque: int) -> None:
        """0x6072 max torque in 0.1 % of rated torque, both directions."""
        self.write_object(MAX_TORQUE, int(torque))

    def set_torque_limits(self, positive: int, negative: int) -> None:
        """0x60E0 / 0x60E1 per-direction torque limits in 0.1 %."""
        self.write_object(POSITIVE_TORQUE_LIMIT, int(positive))
        self.write_object(NEGATIVE_TORQUE_LIMIT, int(negative))

    def set_torque_slope(self, slope: int) -> None:
        """0x6087 torque slope in 0.1 % per second (PT mode ramp)."""
        self.write_object(TORQUE_SLOPE, int(slope))

    def set_torque_profile_type(self, profile: int) -> None:
        """0x6088 torque profile type (0 = linear ramp)."""
        self.write_object(TORQUE_PROFILE_TYPE, int(profile))

    def set_max_current(self, current: int) -> None:
        """0x6073 max current in 0.1 % of rated current."""
        self.write_object(MAX_CURRENT, int(current))

    def set_max_motor_speed(self, speed: int) -> None:
        """0x6080 max motor speed; the speed limit in torque modes."""
        self.write_object(MAX_MOTOR_SPEED, int(speed))

    # ----------------------------------------------------------------
    #  Profile parameters (PP, PV, HM)
    # ----------------------------------------------------------------

    def set_profile_velocity(self, velocity: int) -> None:
        """0x6081 profile velocity (PP)."""
        self.write_object(PROFILE_VELOCITY, int(velocity))

    def set_profile_acceleration(self, accel: int) -> None:
        """0x6083 profile acceleration (PP, PV)."""
        self.write_object(PROFILE_ACCELERATION, int(accel))

    def set_profile_deceleration(self, decel: int) -> None:
        """0x6084 profile deceleration (PP, PV)."""
        self.write_object(PROFILE_DECELERATION, int(decel))

    def set_quick_stop_deceleration(self, decel: int) -> None:
        """0x6085 quick-stop deceleration."""
        self.write_object(QUICK_STOP_DECELERATION, int(decel))

    # ----------------------------------------------------------------
    #  Profile Position (PP)
    # ----------------------------------------------------------------

    def move_to(self, position: int, *, relative: bool = False,
                wait: bool = True, timeout_s: float = 30.0) -> None:
        """PP move with the set-point handshake. Speed and ramps come from
        0x6081/0x6083/0x6084.

        A zero-length move (already standing at the target) returns without
        the handshake: some drives never acknowledge such a set-point."""
        if self.is_target_reached():
            here = (self.position_demand_value()
                    if self.has_object(POSITION_DEMAND_VALUE)
                    else self.position_actual_value())
            if (position == 0) if relative else (int(position) == here):
                if not relative:
                    self.set_target_position(position)
                return

        self.set_target_position(position)
        cw = self.controlword() | (1 << CW_CHANGE_SET_IMMEDIATELY)
        cw = cw | (1 << CW_RELATIVE) if relative else cw & ~(1 << CW_RELATIVE)
        self.set_controlword(cw)

        self.trigger_new_set_point()

        if wait:
            # target-reached may still show the previous move; wait for it to
            # drop (it never does for a zero-length move, hence the short cap)
            drop = time.monotonic() + 0.1
            while self.is_target_reached() and time.monotonic() < drop:
                self.wait_cycles(1)
            deadline = time.monotonic() + timeout_s
            self._wait_status(SW_TARGET_REACHED, "target position", deadline)

    def trigger_new_set_point(self, timeout_s: float = 2.0) -> None:
        """New-set-point handshake on controlword bit 4, for a target
        already written with set_target_position().

        PP: raise New Set Point, wait for set-point acknowledge, drop the bit.
        HM: rising edge that starts homing; the bit is left high, since
        dropping it aborts homing.

        Each level of bit 4 is held for a number of bus cycles (not wall
        time) that covers the drive's Sync0 period, so the drive has the new
        target and sees the low level before the edge, even if the engine
        stalls. The acknowledge is not required to be low first: some
        drives hold it for the whole move yet accept a new
        set-point, and sometimes show no acknowledge at all, so the drive
        moving with target-reached changing state (the move started or
        already ended) also counts as accepted."""
        self.set_controlword_bit(CW_NEW_SET_POINT, False)
        self.wait_cycles(self._settle_cycles)
        was_reached = self.is_target_reached()
        start_pos = (self.position_actual_value()
                     if self.has_object(POSITION_ACTUAL_VALUE) else None)
        self.set_controlword_bit(CW_NEW_SET_POINT, True)
        if (self.has_object(MODES_DISPLAY)
                and self.mode_display() == OP_MODE["HM"]):
            return
        try:
            # hold the bit long enough to be seen even if ack is already high
            self.wait_cycles(self._settle_cycles)
            deadline = time.monotonic() + timeout_s
            while not self.status_bit(SW_SETPOINT_ACK):
                if (start_pos is not None
                        and self.is_target_reached() != was_reached
                        and self.position_actual_value() != start_pos):
                    break   # drive is moving: accepted without a visible ack
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{type(self).__name__}: did not reach "
                                       f"set-point acknowledge "
                                       f"(statusword=0x{self.statusword():04X})")
                self.wait_cycles(1)
        finally:
            self.set_controlword_bit(CW_NEW_SET_POINT, False)
