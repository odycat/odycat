# Architecture

## Processes

- **`odycat`** (C11, `engine/`) owns the IgH master. It is the
  only process that talks to the bus. After startup it runs a fixed-period
  loop with `SCHED_FIFO` priority 80, no heap allocation and no locks.

  | Source | Part | IgH |
  |---|---|---|
  | `main.c` | Startup order, shutdown | yes |
  | `config.c`, `config.h` | JSON config parser, config model | no |
  | `layout.c` | PDO byte chunks, placement of the slave blocks, expected WKC | no |
  | `shm_abi.h` | Binary layout of the shm regions, mirrored by the Python package | no |
  | `shm.c` | Run directory, shm regions, shm group, instance lock | no |
  | `trace.c` | Trace ring | no |
  | `igh.c` | Bus scan, slave configuration (PDOs, SDOs, DC, free-run), way to OP | yes |
  | `rt.c` | Real-time setup and the cycle loop | yes |
  | `util.c` | `die()` with cleanup hooks, signals | no |

  The parts without IgH build and can be tested without the IgH userland. The
  Makefile compiles all sources in one call with `-flto`, so the cycle loop is
  inlined across files as if it were one source.
- **Python processes** (any number) map the engine's shared memory. They
  never call the engine and the engine never waits for them.

There is no RPC, socket or command channel between them: the only interface is
the shared memory. Consequences:

- Python cannot do SDO reads/writes, change state or reconfigure the bus at
  runtime. SDOs are written once at startup from the config.
- The engine does not know if a Python process is alive. It keeps sending
  the outputs last published until someone changes them or the engine
  stops. A `Device` sets the output bits it wrote back to 0 when its script
  ends normally (see python-api.md, "Outputs when the script ends"); after
  `kill -9`, a crash or a hang the outputs keep their last value.

## Startup sequence

1. Parse the config (`cJSON`). Bad or missing fields are fatal.
2. Check that the `shm_group` exists. Prepare the run directory
   (`$ODYCAT_RUN_DIR`, else `/run/odycat`): create it if missing (`0750`,
   group `shm_group`), then require a real directory on tmpfs, owned by root
   or by the engine's user, that only its owner can write to. Take the
   instance lock `<run dir>/<bus>.lock`.
3. Create the regions as files in the run directory, exclusively
   (`O_EXCL`, `O_NOFOLLOW`): one left by an engine that did not clean up
   (owned by the engine's user or root) is replaced, anything else is
   refused. Group `shm_group`; the data region gets mode `0660` (scripts
   write outputs into it), the config region `0640` (only the engine writes
   it). Zero the data region, copy the raw config JSON into the config
   region. Create the trace
   region (mode `0640`) unless `history_s` is 0, and touch all of it. The
   output buffers, sync-group words and output table are fixed by the
   config and written here, so scripts can write outputs from the start.
4. `ecrt_request_master(master)` (config `"master"`, default 0); scan the bus and print every slave with its
   verdict (OK / MISMATCH / not in config). Resolve `alias:position`
   addresses to ring positions. Any configured slave missing or with a
   different vendor/product code is fatal.
5. Per slave: `ecrt_master_slave_config`, PDO assignment
   (`ecrt_slave_config_pdos`, SM2 = outputs, SM3 = inputs unless the PDOs'
   `sm` names others, checked against the slave's sync managers), queue startup
   SDOs (`ecrt_slave_config_sdo8/16/32`, sent by IgH in PREOP), optional DC
   (`ecrt_slave_config_dc`), register PDO entries in one domain. Slaves
   without DC first get free-run SDOs, when `ecrt_master_sdo_upload` of
   `0x1C32:04` / `0x1C33:04` shows free-run is supported.
6. Activate the master, wait for PREOP (5 s), SAFEOP (15 s), OP (10 s), and
   for the bus to stay in OP for 500 ms (a slave can accept OP and refuse it
   a moment later). A slave that misses a timeout or drops out in those
   500 ms, or a configured slave that is not on the bus, is marked down: the
   engine prints it with its state and AL status code and runs without it.
   IgH retries a slave that dropped out on its own by itself; one that
   refused a state change carries IgH's error flag and is tried again only
   when a state is requested, so while any slave is out of OP the RT loop
   calls `ecrt_master_reset()` (after 1 s, doubling up to 60 s; slaves in
   OP are not touched). A slave counts as back after 500 ms in OP. With
   `--strict` the engine exits
   instead. A different device at a configured address always stops it.
7. Publish `expected_wkc`, `bus_state = 8` and the slave status table.
8. Lock memory (`mlockall`) and request `SCHED_FIFO` 80. If either is
   denied the engine warns and goes on, or exits when the config sets
   `require_rt`. Publish `rt_flags`, enter the RT loop.

Slaves on the bus that are not in the config are left in PREOP and ignored.

## RT loop (every `cycle_us`)

1. `clock_nanosleep(TIMER_ABSTIME)` to the next period (no drift). Frames
   always go out at least half a period apart: a cycle runs only if it is
   less than half a period late for its slot and the previous frame went out
   at least half a period ago. Otherwise (no RT scheduling, a stall, the
   previous cycle preempted before its send) the engine **skips** the missed
   slots of the same grid (the DC phase is kept) and runs in the current slot
   or waits for the next one. It never runs missed cycles back to back:
   frames a few µs apart would go out before the previous one came back
   (wrong WKC, stale inputs). Each skip is an engine event (`cycles_skipped`,
   with the count) and adds to `skipped_cycles` in the trace header;
   `cycle_count` counts only cycles that ran.
2. DC: application time, sync reference clock, sync slave clocks.
3. `ecrt_master_receive`, `ecrt_domain_process`; store the working counter
   and set/clear the WKC-mismatch flag.
4. **Inputs and outputs**, inside one seqlock write window: inputs domain →
   shm; then for each sync group, claim its published output buffers (see
   "Concurrency rules") and copy them to the domain and to the "as sent"
   half of each slave block. A buffer written before the slave last left OP
   is sent as zeros.
5. `ecrt_domain_queue`, `ecrt_master_send`.
6. `cycle_count++` (u32 and u64), `last_cycle_ns`.
7. Refresh one entry of the status table (round-robin over slaves, then the
   master), update the slave-lost flag. A slave seen leaving OP gets its
   `drop_count` incremented, which makes every output buffer written before
   stale: it returns with all outputs off until a script writes again.
8. **Trace**: record the cycle in the trace ring (inputs, outputs as sent,
   header values, not-in-OP bitmap). Engine events (slave lost/back, WKC
   wrong/ok, link down/up) are recorded where they are detected. Both cost
   the same every cycle: no allocation, no syscall but `clock_gettime`.

On SIGINT, SIGTERM or SIGHUP (its terminal was closed): deactivate the
master, unlink the shm regions, release the master and the lock. The handlers
are installed before anything is created, so a signal during startup also
cleans up; SIGPIPE is ignored.

## Shared memory

The regions are plain files on tmpfs in the engine's run directory
(`/run/odycat`, or `$ODYCAT_RUN_DIR`), mapped with `mmap`. Only the
directory's owner can create files there, so no other local user can plant a
region or hold the lock before the engine starts. Python opens them through
`odycat._config.open_shm`, which refuses a symbolic link, a file open to all
users, a run directory others can write to, or a region owned by anyone but
root or the directory's owner.

### Layout version

Every region starts with a magic number and a layout version, mirrored in
Python by `odycat/_shm.py` (the only place in the package that knows the
offsets). A new field goes into reserved space and raises the **minor**
version; older readers ignore it. An incompatible change raises the
**major**, and Python refuses a region whose major differs from its own,
saying which side is newer: install the engine and the package from the same
version. Current: 2.0.

### Config region `<run dir>/<bus>_config`

A 256-byte header, written once at startup, then the config file exactly as
it was loaded. Python reads the JSON to find each slave's layout, and the
header to tell which engine runs which config (`Bus.engine_info`,
`odycat._config.engine_info()`).

| Offset | Type | Field | Meaning |
|---|---|---|---|
| 0 | u32 | `magic` | `0x4643434F`, `"OCCF"`, written last |
| 4 | u16, u16 | `layout_major`, `layout_minor` | |
| 8 | u32 | `header_size` | 256: where the JSON starts |
| 12 | u32 | `json_len` | |
| 16 | char[32] | `engine_version` | e.g. `1.0.0` |
| 48 | char[48] | `engine_git` | `git describe` of the build |
| 96 | u8[32] | `config_sha256` | SHA-256 of the JSON bytes (also printed at startup): the evidence of which config ran |
| 128 | u32 | `engine_pid` | |
| 132 | u32 | `support_end` | End of the support period as YYYYMM, 0 = not set (build option `SUPPORT_END`) |
| 136 | u64 | `start_realtime_ns` | `CLOCK_REALTIME` at startup |
| 144 | u8[16] | `instance_id` | Random, new at every start |
| 160 | | reserved | |

### Data region `<run dir>/<bus>_shared`

```
0      256-byte header
256    slave 0 block   [ inputs as received | outputs as sent ]
...    slave N block   (each block starts 8-byte aligned)
...    output buffers: 3 per slave with outputs (64-byte aligned)
...    pub words:  u64 per sync group (scripts write)     (64-byte aligned)
...    busy words: u64 per sync group (the engine writes) (64-byte aligned)
...    output table, 16 bytes per configured slave (64-byte aligned)
...    slave status table, 20 bytes per configured slave
```

Header (little-endian). The first cache line holds what readers look at
every cycle; unused bytes are reserved (zero).

| Offset | Type | Field | Meaning |
|---|---|---|---|
| 0 | u32 | `magic` | `0x4144434F`, `"OCDA"`, written once the header is set up |
| 4 | u16, u16 | `layout_major`, `layout_minor` | |
| 8 | u32 | `header_size` | 256: the first block's offset |
| 12 | u32 | `features` | bit 0: the trace region exists |
| 16 | u32 atomic | `seq_counter` | Seqlock; odd while the engine writes inputs |
| 20 | | reserved | |
| 24 | u64 | `cycle_count` | Cycles run since the start, never wraps. Written outside the seqlock: read it until two reads agree |
| 32 | u64 | `last_cycle_ns` | `CLOCK_MONOTONIC` time of the last cycle |
| 40 | u32 | `bus_state` | 0 during startup, 8 (OP) once running |
| 44 | u32 atomic | `error_flags` | bit 0 WKC mismatch, bit 1 slave lost |
| 48 | u32 | `work_counter` | WKC of the last cycle |
| 52 | u32 | `expected_wkc` | Expected WKC, set once |
| 56 | u32 | `wkc_wrong_streak` | Consecutive cycles with a wrong WKC up to the last one; 0 when it was right |
| 64 | u64 | `wkc_wrong_cycles` | Cycles with a wrong WKC since the start |
| 72 | u64 | `skipped_cycles` | Cycles skipped since the start (see the RT loop) |
| 80 | u32 | `status_offset` | Offset of the status table, 0 until OP |
| 84 | u32 | `status_count` | Entries in the status table |
| 88 | u32 | `slaves_responding` | From `ecrt_master_state` |
| 92 | u32 | `master_al_states` | OR of all slaves' AL states |
| 96 | u32 | `link_up` | 0/1 |
| 100 | u32 | `rt_flags` | bit 0 `SCHED_FIFO` active, bit 1 memory locked; set before the RT loop |
| 104 | u32 | `cycle_us` | Bus cycle |
| 108 | u32 | `out_table_offset` | Offset of the output table |
| 112 | u32 | `group_count` | Sync groups |
| 116 | u32 | `groups_offset` | Offset of the pub words |
| 120 | u32 | `busy_offset` | Offset of the busy words |
| 128 | u64 | `out_group_skips` | Times a group kept the previous cycle's outputs (see "Concurrency rules") |
| 136 | | reserved | |

Status table entry (20 bytes, config order): `u8 al_state, u8 online,
u8 operational, u8 refreshed, u16 ring_pos, u16 drop_count, u32
block_offset, u16 in_bytes, u16 out_bytes, u16 al_code, u16 reserved`.
`al_code` is the slave's AL status code (register 0x0134) while it is out of
OP, read without blocking through an IgH register request at each refresh;
0 in OP. A slave the bus started without is out of OP from the first cycle
with no drop counted (`ring_pos` 0xFFFF if it is not on the bus). The last three are the block
the engine placed for the slave: `Device` checks its own layout against
them and refuses to attach on a mismatch (wrong device class for the slave,
or engine and package of different versions). `drop_count` counts the
times the slave left OP (wraps at 65535); Python raises `DeviceLostError`
when it changes. A full refresh takes `slaves + 1` cycles: about 0.5 s with
512 slaves at 1 ms, so a dropped slave can take that long to show up here (a
wrong WKC is flagged in the same cycle).

`error_flags` bit 1 (slave lost) is set while any configured slave is offline
or not operational, or fewer slaves respond than configured.

Output table entry (16 bytes, config order): `u32 buf_offset` (buffer 0;
buffer k at `buf_offset + k * buf_stride`), `u32 buf_stride` (a multiple of
64), `u16 group` (`0xFFFF` for a slave without outputs), `u8 member` (its 2
bits in the group words: `2*member`, `2*member+1`), 5 bytes reserved.

Output buffer: `u32 epoch` (the slave's `drop_count` when the buffer was
filled), `u32` reserved, then the outputs packed as in the slave block.

Sync groups: every slave with outputs belongs to one, its `"sync_group"`
in the config or a group of its own; at most 32 slaves per group. Groups
are numbered in order of their first slave, members in config order.

### Slave block layout

Inputs first, then outputs (the outputs **as sent** in the last cycle,
written by the engine; scripts write the output buffers). Within a direction, the PDOs in config order
(`txpdo` as one PDO or a list), back to back as on the wire:
a direction is one run of entries. Entries are packed in config order,
**without** alignment padding, so a `u16` followed by an `i32` occupies 6
bytes. Python reads them with a `=`-prefixed `struct` format.

Sub-byte entries (e.g. 1-bit digital channels) are packed bit by bit, LSB
first, and copied as whole bytes. A run of entries must end on a byte
boundary; runs may cross PDO boundaries (IgH places the PDOs of a sync
manager back to back), so a direction must end on one, not each PDO. Gap entries (index `0x0000`) take room on the wire; a run made only
of gaps is **not** copied into shm, so a trailing pad byte after a `u8`
mode field does not appear in Python.

Block offsets are always computed (first block at 256, each block 8-byte
aligned, in config order). Python applies the same rules (`odycat._config.resolve_slave_shm_offset`).

### Concurrency rules

- **Inputs**: the engine increments `seq_counter` to odd, copies all inputs,
  increments it to even. Readers copy the bytes they need and retry while the
  counter is odd or changed (`bus._seqlock_read`): they spin, then yield the
  CPU, and raise only if the engine is stuck (odd counter and no new cycle
  for 10 cycles, at least 50 ms). Every input read is therefore a consistent
  snapshot of one cycle.
- **Outputs**: scripts never write what the engine is sending. Each slave
  with outputs has 3 buffers; its sync group's pub word holds, 2 bits per
  member, the index of each member's published buffer.
  - A write (`odycat.device._publish_group`) takes the group's lock: an OFD
    lock (`F_OFD_SETLKW`) on byte `<group>` of the data region, released
    by the kernel if the process dies, plus a thread lock in the process.
    For each slave it picks a buffer that is neither published nor in the
    engine's busy word, copies the published outputs into it (zeros if that
    buffer's epoch is not the current `drop_count`), stamps the epoch,
    applies the patch (only the bits written, so several scripts can share
    an output module, even the same byte), and finally publishes every slave
    of the group with **one 8-byte store** of the pub word.
  - Every cycle the engine, per group: loads pub, stores it into busy
    (sequentially consistent), loads pub again and retries if it moved (at
    most 4 tries; then the group keeps last cycle's outputs and
    `out_group_skips` counts it). From then on no writer picks those
    buffers, since writers skip the busy ones.
  - So the engine sends a slave's outputs from before a write or from
    after it, never in between; the slaves of a group from the same
    `transmit_outputs()` go out in the same cycle; a writer that dies before
    its store publishes nothing. Groups share nothing: writers of different
    groups never wait for each other.
  - The store must become visible after the buffer it publishes. On x86-64
    a plain store does that; on other architectures `odycat._atomic` provides
    the required release store. Without it, output writes are refused there.

### Trace region `<run dir>/<bus>_trace`

Mode `0640`: members of `shm_group` can read it, only the engine writes, so
a script cannot rewrite the recorded history. Absent when `history_s` is 0.
Size: `history_s / cycle_us` slots; the engine refuses more than 512 MB.

```
0      128-byte trace header
128    slot ring: slot_count slots of slot_size bytes
...    event ring: 1024 events of 32 bytes
```

Trace header (little-endian): `u32 magic` (`0x5254434F`, `"OCTR"`, written last),
`u32 version` (2), `u32 slot_size`, `u32 slot_count`, `u32 slots_offset`,
`u32 blocks_end` (data-region offset where the blocks end), `u32
not_op_offset` (within a slot), `u32 event_count`, `u32 events_offset`, `u32
cycle_us`, `u64 head` (cycle of the newest slot, offset 40), `u64 event_head`
(seq of the newest event, offset 48), `u64 skipped` (offset 56), `u32
header_size` (128, offset 64), `u32 blocks_start` (data-region offset of the
first block, offset 68), reserved up to 128.

A slot keeps a 64-byte header whatever the size of the data header, so a
bigger data header does not make the trace bigger:

| Offset | Content |
|---|---|
| 0 | `u64 cycle` (written last; 0 while the slot is rewritten), `u64 mono_ns` (= `last_cycle_ns`), `u64 real_ns` (`CLOCK_REALTIME`), `u32 work_counter`, `u32 error_flags`, 32 reserved bytes |
| 64 | The data region's bytes `blocks_start .. blocks_end`, the slave blocks: **inputs as received** in that cycle, **outputs as sent** (copied from the IgH domain, not from shm). Data-region offset X is at `X - blocks_start + 64` |
| `not_op_offset` | Bitmap, bit i = slave i out of OP as the status table saw it |

`Trace` hands slots out in data-region layout (`Slot.data`: the slot header,
padding up to `blocks_start`, then the blocks), so entries decode with the
data region's offsets.

Cycle c is in slot `(c - 1) % slot_count`. A reader checks the slot's
`cycle` before and after copying it; a different value means the slot was
overwritten and that cycle is lost to this reader.

Event: `u64 seq` (written last), `u64 cycle` (0 = before the first cycle),
`u64 real_ns`, `u16 type` (1 engine start with `arg` = `rt_flags`, 2 engine
stop, 3 slave lost with `arg` = AL state | online << 8, 4 slave back, 5 WKC
wrong with `arg` = WKC, 6 WKC ok, 7 link down, 8 link up), `u16 slave`
(config index, `0xFFFF` = none), `u32 arg`. Event n is at `(n - 1) % 1024`.

## Expected working counter

Mirrors IgH: one FMMU per direction per slave that has PDOs in it. With both
directions present in the domain (LRW), each output slave counts 2 and each
input slave 1; otherwise every slave counts 1. A slave with inputs and
outputs therefore counts 3, a pure input slave 1, a pure output slave 2, a
slave without PDOs 0.
