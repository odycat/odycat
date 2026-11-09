#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ocean Octane S.L.U.
"""
OdyCAT Demo — shows how to use the Python library with a running C engine.

Prerequisites:
  1. Start the engine:    odycat-config expand examples/config.json -o machine.json
                          sudo odycat machine.json (see getting started)
  2. Run this script:     python3 examples/demo.py

This demo assumes examples/config.json:
  - Slave 0: EL2809  (16-ch digital output)
  - Slave 1: EL1809  (16-ch digital input)
  - Slave 2: Servo   (CiA 402 servo)

No need to pass SHM paths or offsets — everything is auto-detected
from the config SHM that the C engine creates at startup.
"""

import time
import sys
from odycat import Bus, Device
from odycat.devices.beckhoff import EL2809, EL1809
from odycat.devices.cia402 import Servo


def main():
    # ------------------------------------------------------------------
    #  Connect to the bus (auto-detect bus from /run/odycat/*_config)
    # ------------------------------------------------------------------
    try:
        bus = Bus()
    except RuntimeError as e:
        print(f"Cannot connect to OdyCAT engine: {e}")
        sys.exit(1)

    print(f"Bus state : {'OP' if bus.is_op else 'not in OP'}  (all slaves OP: {bus.slaves_ok})")
    print(f"WKC       : {bus.work_counter} / {bus.expected_wkc}  (ok={bus.wkc_ok})")
    print(f"Cycle cnt : {bus.cycle_count}")
    print()

    # ------------------------------------------------------------------
    #  Instantiate devices — just pass the slave position
    # ------------------------------------------------------------------
    outputs = EL2809(position=0)
    inputs  = EL1809(position=1)
    servo   = Servo(position=2)

    # ------------------------------------------------------------------
    #  Digital I/O demo
    # ------------------------------------------------------------------
    print("=== Digital Output Demo (EL2809) ===")

    # Turn on channel 1
    outputs.set_output(1, True)
    print(f"  CH1 = {outputs.output_1()}")

    # Set all outputs
    pattern = [i % 2 == 0 for i in range(16)]  # alternating pattern
    outputs.set_outputs(pattern)
    print(f"  All outputs: {outputs.outputs():016b}")
    time.sleep(0.5)

    # Clear all
    outputs.set_outputs(0)
    print(f"  Cleared:     {outputs.outputs():016b}")
    print()

    # ------------------------------------------------------------------
    #  Digital input demo
    # ------------------------------------------------------------------
    print("=== Digital Input Demo (EL1809) ===")
    print(f"  All inputs: {inputs.inputs():016b}")
    for ch in range(1, 17):
        state = inputs.input(ch)
        if state:
            print(f"  CH{ch} = ON")
    print()

    # ------------------------------------------------------------------
    #  Servo demo
    # ------------------------------------------------------------------
    print("=== Servo Demo (CiA 402) ===")
    print(f"  Statusword     : 0x{servo.statusword():04X}")
    print(f"  Fault          : {servo.is_fault()}")
    print(f"  Actual position: {servo.position_actual_value()}")
    print(f"  Actual velocity: {servo.velocity_actual_value()}")

    # Enable the drive
    print("  Enabling servo...")
    try:
        servo.enable(timeout_s=3.0)
        print("  Servo enabled!")
    except RuntimeError as e:
        print(f"  Enable failed: {e}")
        return

    # Move to a target position (CSP mode example)
    target = servo.position_actual_value() + 10000
    print(f"  Moving to position {target} ...")
    servo.set_target_position(target)

    # Wait until target reached (simple polling)
    t0 = time.monotonic()
    while not servo.is_target_reached():
        time.sleep(0.01)
        if time.monotonic() - t0 > 5.0:
            print("  Timeout waiting for target reached!")
            break
    else:
        print(f"  Target reached. Actual = {servo.position_actual_value()}")

    # ------------------------------------------------------------------
    #  Deferred write demo (multi-axis sync)
    # ------------------------------------------------------------------
    print()
    print("=== Deferred Write Demo ===")
    # Stage values on two devices, then flush in one shot
    outputs.stage_rxpdo(0xAAAA)
    servo.stage_object(0x607A, servo.position_actual_value() + 5000)  # target pos
    Device.transmit_outputs()
    print("  Staged outputs flushed.")

    # Disable servo
    servo.disable()
    print("  Servo disabled.")

    # ------------------------------------------------------------------
    #  Continuous monitoring example
    # ------------------------------------------------------------------
    print()
    print("=== Bus Monitor (Ctrl+C to stop) ===")
    try:
        while True:
            print(f"\r  cycle={bus.cycle_count:>10d}  wkc={bus.work_counter}/{bus.expected_wkc}"
                  f"  inputs=0x{inputs.read_txpdo()[0]:04X}"
                  f"  pos={servo.position_actual_value():>10d}", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
