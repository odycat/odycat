#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ocean Octane S.L.U.
"""
Events on the demo box: functions called when PDO values change.

What it shows (works with nothing wired: the script drives one output
itself, and outputs can be watched like inputs):

- ev.rising / ev.falling on one channel (dio.in00, dio.out00);
- ev.change on a whole word, and with a deadband on an analog value;
- ev.when: a condition that has to hold for some cycles;
- ev.lost / ev.back: a slave leaving OP and coming back;
- ev.engine: engine events (wrong working counter, slave lost, ...);
- the loop waits with ev.wait_cycles() instead of bus.wait_cycles(): the
  handlers run inside that wait, like at the end of a PLC scan, in this
  thread, never in the middle of the loop's own code. ev.sleep() is the
  same for time.sleep(); ev.run() is for scripts that only react to events.

A device variable without () is the variable itself, which is what the
decorators take: dio.in00() is the input's value now, dio.in00 is the
input. Every handler gets an Event: e.value, e.old, e.cycle (bus cycle it
happened in), e.slave (label), e.field. A handler may also take no argument.

Try it: run it, and while it runs force a slave out of OP from another
terminal (ethercat states -p 2 PREOP, then ethercat states -p 2 OP), or wire
something to the EC3A inputs.

Hardware (examples/demo_box.json, adapt to your config):
  EC3A-IO1632 at alias 40   16 DI / 16 DO, OUT00 toggled by the script
  ECT60 at alias 60         stepper drive (only watched, never moved)
  CATIO-A at alias 50       no device class: watched by entry label

Run with the engine started:  python3 examples/events_demo.py
"""

from odycat import Bus, Device, Events
from odycat.device import DeviceLostError
from odycat.devices.amsamotion import EC3A_IO1632
from odycat.devices.leadshine import ECT60
from odycat import trace

TOGGLE_CYCLES = 500     # OUT00 changes every 500 cycles (0.5 s at 1 ms)
RUN_CYCLES = 10000      # stop after 10000 cycles (10 s at 1 ms)

bus = Bus()
dio = EC3A_IO1632(alias=40)
drive = ECT60(alias=60)
catio = Device(alias=50)          # a slave without a device class
ev = Events()


# -- one point: output OUT00 (the script toggles it) -------------------------
@ev.rising(dio.out00)
def output_on(e):
    print(f"cycle {e.cycle}: {e.slave} OUT00 went ON")


@ev.falling(dio.out00)
def output_off(e):
    print(f"cycle {e.cycle}: {e.slave} OUT00 went OFF")


# -- inputs: fire only if something is wired --------------------------------
@ev.rising(dio.in00, debounce=5)                     # held 5 cycles: no bounce
def input0_pressed(e):
    print(f"cycle {e.cycle}: IN00 pressed")


@ev.change(dio.inputs)                                  # the whole 16-bit word
def inputs_changed(e):
    print(f"cycle {e.cycle}: inputs {e.old:016b} -> {e.value:016b}")


@ev.change(catio, "analog_in_1", deadband=200)          # no class: slave + entry label
def analog_moved(e):
    print(f"cycle {e.cycle}: analog in 1 {e.old} -> {e.value}")


# -- conditions and drive state ------------------------------------------------
@ev.when(drive.velocity_actual_value, lambda v: abs(v) > 10000, hold=20)
def drive_fast(e):
    print(f"cycle {e.cycle}: drive above 10000 for 20 cycles ({e.value})")


@ev.rising(drive.is_fault)
def drive_fault():                                      # no argument: also fine
    print("drive reports a fault")


# -- slaves leaving OP and coming back -----------------------------------------
@ev.lost(dio)
def dio_lost(e):
    print(f"cycle {e.cycle}: {e.slave} left OP; the engine zeroed its outputs")


@ev.back(dio)
def dio_back(e):
    print(f"cycle {e.cycle}: {e.slave} is back in OP")


@ev.engine(trace.WKC_WRONG, trace.WKC_OK)
def wkc(e):
    print(f"cycle {e.cycle}: engine reports {e.name} (working counter {e.arg})")


def main() -> None:
    start = bus.wait_cycles(0)
    output = False
    try:
        while True:
            now = ev.wait_cycles(TOGGLE_CYCLES)         # handlers run inside the wait
            if now - start >= RUN_CYCLES:
                break
            output = not output
            try:
                dio.set_out00(output)
            except DeviceLostError as e:                # slave out of OP: skip this toggle
                print(f"cannot write: {e}")
    except KeyboardInterrupt:
        pass
    finally:
        print("done")                                   # outputs written are set back to 0


if __name__ == "__main__":
    main()
