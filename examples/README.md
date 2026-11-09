# Examples

Small scripts that use the OdyCAT Python library against a running engine.
Device labels come from the engine config; adapt them to yours.

Everything in this folder is under the MIT licence
([text](../LICENSES/MIT.txt)): copy it into your own code. The `odycat`
package it uses keeps its own licence, see [LICENSING.md](../LICENSING.md).

| Script | Shows |
|---|---|
| [`demo.py`](demo.py) with [`config.json`](config.json) | First steps: bus status, reading inputs, writing outputs, a CiA 402 servo |
| [`button_fan_recovery.py`](button_fan_recovery.py) | Timing in bus cycles with `wait_cycles()` instead of `time.sleep()`, and recovering from a slave that drops out of OP with `DeviceLostError` |
| [`events_demo.py`](events_demo.py) | `Events`: functions called on rising/falling edges, changes, conditions, slaves leaving OP and engine events; works on the demo box with nothing wired |
