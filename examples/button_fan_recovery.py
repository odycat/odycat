#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ocean Octane S.L.U.
"""
Button + fan demo that survives a slave dropping off the bus.

- Pressing the button (input L0) runs the fan (output L0) for
  10 s; pressing it again while the fan runs stops it.
- The button lamp (output OUT08 of an I/O module) blinks while the fan is off and
  stays lit while it runs.
- All timing is in bus cycles (wait_cycles) instead of time.sleep(): if the
  engine stalls, the program waits with it instead of skipping time.
- If a slave leaves OP (cable, power, sync error), the engine zeroes its
  outputs and every read/write on it raises DeviceLostError. The demo catches
  it, keeps the fan off, waits until every device answers again and carries
  on. The fan does not restart by itself: press the button again.

Hardware (labels from examples/demo_box.json, adapt to your config):
  delta_di16  16 DI module, button on L0
  delta_do16  16 DO module, fan on L0
  io_module   EC3A-IO1632, button lamp on OUT08

Run with the engine started:  python3 examples/button_fan_recovery.py
"""

from odycat import Bus
from odycat.device import DeviceLostError
from odycat.devices.amsamotion import EC3A_IO1632
from odycat.devices.delta import AX516AM, AX516AN

READ_CYCLES = 100       # poll the button every 100 cycles
FAN_CYCLES = 10000      # fan runs for 10000 cycles after a press
REARM_CYCLES = 200      # after switching on, ignore the button for 200 cycles
BLINK_CYCLES = 500      # lamp toggles every 500 cycles

bus = Bus()
button_in = AX516AM(label="delta_di16")
fan_out = AX516AN(label="delta_do16")
lamp_io = EC3A_IO1632(label="io_module")


def cycles_since(stamp: int, now: int) -> int:
    """Cycles elapsed since 'stamp' (the cycle counter is 64-bit, no wrap)."""
    return now - stamp


def wait_for_devices() -> None:
    """Block until every device can be accessed without DeviceLostError."""
    while True:
        bus.wait_cycles(READ_CYCLES)
        try:
            button_in.read_txpdo()
            fan_out.read_rxpdo()
            lamp_io.read_rxpdo()
            return
        except DeviceLostError:
            pass    # still out of OP, or reporting the drop once: keep waiting


def main() -> None:
    now = bus.wait_cycles(0)                  # current cycle count
    fan_stamp = now - FAN_CYCLES - 1          # fan off at start
    blink_stamp = now
    lamp_on = False

    try:
        while True:
            try:
                now = bus.wait_cycles(READ_CYCLES)
                fan_on = cycles_since(fan_stamp, now) < FAN_CYCLES

                if button_in.l0():
                    if fan_on and cycles_since(fan_stamp, now) > REARM_CYCLES:
                        # pressed while running: stop the fan, wait for release
                        fan_stamp = now - FAN_CYCLES - 1
                        while button_in.l0():
                            bus.wait_cycles(READ_CYCLES)
                    else:
                        # pressed while off: start (restarts while held)
                        fan_stamp = now

                fan_on = cycles_since(fan_stamp, now) < FAN_CYCLES
                fan_out.set_l0(fan_on)

                # lamp: blink while the fan is off, steady while it runs
                if cycles_since(blink_stamp, now) >= BLINK_CYCLES:
                    blink_stamp = now
                    lamp_on = True if fan_on else not lamp_on
                    lamp_io.set_out08(lamp_on)

            except DeviceLostError as e:
                print(f"WARNING: {e}", flush=True)
                # the engine already zeroed the outputs; keep the fan off
                fan_stamp = now - FAN_CYCLES - 1
                wait_for_devices()
                now = bus.wait_cycles(0)
                blink_stamp = now
                print("Devices back, carrying on (fan off)", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            lamp_io.set_out08(False)
            fan_out.set_l0(False)
        except DeviceLostError:
            pass    # bus down: the outputs are already zero
        print("done")


if __name__ == "__main__":
    main()
