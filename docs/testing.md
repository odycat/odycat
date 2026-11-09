# Tests and CI

## 1. Unit tests (no hardware, no IgH, no root)

```sh
./ci/test.sh                                        # pytest if installed, else unittest
python3 -m unittest discover -s tests -t tests      # stdlib only, nothing to install
python3 -m unittest tests.test_monitor -v           # one module
./ci/test.sh tests/test_pdo_layout.py               # same, with pytest
```

`tests/` holds the unit tests. The fake engine they use is part of the
package, `odycat.testing`, so device classes and applications can test
against it too (see [devices.md](devices.md)); `tests/_support.py` adds the
repository's configs. `odycat.testing` is not covered by the compatibility
promise: it follows the engine and the test tooling, and may change in any
release. The tests exercise the Python side of OdyCAT against a **fake
engine**: shared-memory regions represented by files in a temporary
directory. `Bus`, `Device` and `BusMonitor` run their real code paths against
that model.

| Module | What it pins down |
|---|---|
| `test_config_lookup.py` | `find_slave()` / `slave_config()`: position, alias (alias N = offset from the slave carrying alias N), label, and the offsets of the shipped configs |
| `test_pdo_layout.py` | `pdo_format_from_entries()` / `pdo_size_from_entries()` / `pdo_entry_layout()`: byte-aligned runs, padding-only runs not mirrored, `calcsize(fmt) == size` |
| `test_pdo_codec.py` | `decode_entry()` and the `Device` bit-field codec: signed vs unsigned, straddling bytes, out-of-range values |
| `test_bus_device.py` | `Bus` header fields and cycle accumulation, `Device` lookup, entry layout, input reads, staged and immediate output writes, `transmit_outputs()`, torn-read protection |
| `test_standard_io.py` | `mdp.DigitalIO` on the Beckhoff, EC3A and Delta layouts, analog entries left out; `cia401.GenericIO` digital families and analog channels; channel variables with `Events` |
| `test_scan.py` | `odycat-config scan` (and its EtherCATInfo parser) against `ethercat` output recorded from the demo box (`tests/data/demo_box`): profiles matched, slaves written in full with the same layout and signedness as the device classes, addressing, `--update` (kept, added, replaced, removed), `expand`, `python3 -m odycat`, CLI |
| `test_trace_events.py` | `Trace`: slots per cycle, outputs and bits, lost cycles, not-in-OP bitmap, engine events; `Events`: edges, deadband, debounce, `when`/hold, handler errors, device loss, overrun, ordering |
| `test_monitor.py` | `BusMonitor.layout()` / `snapshot()` / `sample()`: absolute bit offsets, status table, WKC mismatch, starting/stalled states, engine restart with a new config |

## 2. What the fake engine models

Everything a reader depends on is mirrored from the engine (`engine/shm_abi.h`
for the binary layout, `engine/layout.c` for the block placement), so the tests
compare the package against an independent model instead of against itself.
The package keeps its own copy of the layout in `odycat/_shm.py`; the fake
engine does not import it.

- 256-byte data header with magic and layout version (`seq_counter`,
  `cycle_count`, `last_cycle_ns`, `bus_state`, `error_flags`, `work_counter`,
  `expected_wkc`, the wrong-WKC streak and total, `skipped_cycles`,
  `status_offset`, `status_count`, `slaves_responding`, `master_al_states`,
  `link_up`, `rt_flags`, `cycle_us`), set with `eng.set(name=value)`;
- 256-byte config header (engine version, SHA-256 of the JSON, pid, instance
  id) before the JSON;
- slave blocks 8-byte aligned, first at offset 256, block size = inputs +
  outputs, each direction being all its PDOs (`txpdo` as one PDO or a list)
  laid out as one run;
- PDO chunks: a run of entries is only mirrored when it closes on a byte
  boundary and holds at least one real entry — gap-only runs are not mirrored,
  so a bit gap is *not* a hole in SHM;
- the per-slave status table after the last data block, 20 bytes per slave,
  with each slave's block (offset, input and output bytes) as the engine
  publishes it;
- the trace: 128-byte header, slots with a 64-byte header followed by the
  data region's blocks, the event ring;
- total size rounded up to a page multiple;
- seqlock: odd counter while inputs are in flight, even when consistent;
- expected WKC: `2 × slaves-with-outputs + slaves-with-inputs` (single
  direction: one FMMU per slave);
- the wrong-WKC counters as the engine keeps them each cycle.

If you change the layout in `engine/shm_abi.h` or `engine/layout.c`, change
`src/odycat/testing.py` in the same commit — the tests are the cross-check between the two.

## 3. CI

CI runs the same scripts in `ci/` used locally: lint, unit tests, engine
checks and documentation builds. The public GitHub workflow also builds
wheels and fuzzes the config parser. See [GitHub CI](../.github/workflows/ci.yml)
for the current jobs and platforms.

Ruff settings live in `pyproject.toml` (`target-version = "py311"`, default
`E4/E7/E9/F` rule set, `F401` ignored in `__init__.py` re-exports).
`pip install -r requirements-dev.txt` installs pytest and ruff.

## 4. What these tests cannot cover

Shared-memory behaviour is only half of the system. Anything that involves the
master itself is checked on hardware benches (see
[tools.md](tools.md#hardware-testing)).

Hardware-dependent behaviour, including bus configuration and cycle-time
jitter, needs testing on the target setup.

---

OdyCAT® is developed by Javier Martínez García at Ocean Octane and published
and supported by Yoctovision S.L.U. under licence. Copyright © 2026 Ocean
Octane S.L.U.; OdyCAT® is its registered trademark. GPL v3 or commercial
licence, see [LICENSING.md](../LICENSING.md).
