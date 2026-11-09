# Cycle time and jitter

Ways to reduce bus-cycle jitter on a Linux PC. Many I/O setups and drives in
free-run or with a longer DC Sync0 period may work without additional tuning.
Measure under your actual load before choosing a cycle time or relying on a
particular timing margin.

If you need a tighter cycle, or you see cycles skipped or devices dropping
out of OP, this page describes how to tune the machine the engine runs on.

## Is scheduling your problem?

| What you see | Likely cause | Where to look |
|---|---|---|
| Wrong working counter (`bus.wkc_ok` false, `wkc_wrong` events in the trace) | Check wiring, connectors, slave power and states first; timing faults may also affect communication | [troubleshooting.md](troubleshooting.md), `ethercat slaves` |
| No skipped cycles, but outputs change late or unevenly | Your script: heavy work, blocking calls, threads competing for the GIL. If the trace shows the output on the wire on time but the device acts late, look at the device and its wiring instead | [Scripts](#scripts) below |
| An I/O module's outputs freeze after a while; it stays in OP and the WKC is fine | The module latched a sync error after late cycles (SM-synchronous mode) | [troubleshooting.md](troubleshooting.md): free-run |
| DC drives drop out of OP (`0x7500`, a `DeviceLostError` that mentions DC), `cycles_skipped` events, or you need a shorter cycle | Scheduling: the engine wakes up late | The rest of this page |

The quick check: watch `Trace.skipped_cycles` while the machine runs its
real load. If it stays at 0, the engine is not waking up late. The trace
records the outputs as the engine sent them, so it also tells you when an
output left the PC.

## What a late cycle does

The engine wakes up once per cycle on an absolute time grid: each cycle's
time is counted from the start of the engine, not from the end of the
previous cycle, so a late cycle does not push the next ones back. If the
engine **wakes up half a period late or more**, it skips the missed slots
instead of sending frames back to back (see [architecture.md](architecture.md)).
At 1 kHz (1 ms) the engine has 500 µs of margin; at 2 kHz (500 µs), 250 µs.
The cycle's own work takes some ten microseconds: what eats the margin is
the time the operating system takes to let the engine run.

A skipped cycle delays the next exchange of outputs and inputs. Its effect
depends on the slave and the machine: even for I/O, a delayed response may
matter to the application. For a slave with `dc_sync` it can leave a Sync0
period without new data: past the slave's limit it may drop out of OP
(drives often report `0x7500`) and Python gets a `DeviceLostError`.

The trace counts every skip (`Trace.skipped_cycles`, `cycles_skipped`
events) and the engine prints the total when it stops.

A wrong working counter (WKC) is a different diagnostic from a skipped slot.
Check the wiring, slave power and states first, then investigate timing if
needed (see [troubleshooting.md](troubleshooting.md)).

## Steps, in order of effect

To make the engine wake up on time (a.k.a. reducing frame jitter), these
are the options. Steps 2 to 5 can be tried on a running machine, without a
reboot, and are lost when it reboots: try them, measure, and then make the
ones you keep permanent (last section).

### 1. Run the engine with real-time scheduling

`SCHED_FIFO` and locked memory: run it with `sudo`, or grant `CAP_SYS_NICE`
and `CAP_IPC_LOCK` (see [getting-started.md](getting-started.md)). Set
`"require_rt": true` in the config so the engine refuses to start without
them instead of warning. Check:

```sh
ps -o pid,cls,rtprio,psr,comm -C odycat     # CLS FF, RTPRIO 80
```

If `SCHED_FIFO` is refused even as root, the kernel was built with
real-time group scheduling (`CONFIG_RT_GROUP_SCHED`, for example Armbian's).
With cgroups v2 it gives no real-time time to any process outside the root
cgroup, a systemd service or a login session included. Boot with
`rt_group_sched=0` on the kernel command line (Armbian: `extraargs=` in
`/boot/armbianEnv.txt`); `chrt -f 80 true` then works and the engine gets
real time.

### 2. Full kernel preemption

A stock Debian kernel is built with `PREEMPT_DYNAMIC` (`uname -v`) but boots
in *voluntary* mode: while another process runs kernel code (a system call,
a page fault), the engine cannot take the CPU until that code reaches a
preemption point. Under load this is what makes cycles late. Switch to
*full* at runtime with:

```sh
sudo cat /sys/kernel/debug/sched/preempt        # the active mode is in parentheses
echo full | sudo tee /sys/kernel/debug/sched/preempt
```

### 3. Give the engine a core of its own

Pin the engine to one CPU, leave the other hardware thread of that core
(SMT) free, and run the Python scripts on the other cores:

```sh
cat /sys/devices/system/cpu/cpu7/topology/thread_siblings_list   # e.g. 7,15: one core
sudo taskset -c 7 odycat machine.json
taskset -c 0-5,8-13 python3 app.py                               # scripts elsewhere
taskset -cp $(pgrep -x odycat)                            # check: "7"
```

Take one of the last cores rather than core 0, which the kernel and most
interrupts use by default.

Besides shorter delays, the engine keeps its caches, as no other process on
that core competes for them.

### 4. The NIC interrupt next to it

With the generic IgH driver (`ec_generic`) every frame goes through the
network interrupt of the EtherCAT NIC. Move it to a neighbouring core, so
the engine's core stays free:

```sh
grep MASTER0_DEVICE /etc/sysconfig/ethercat       # the NIC, often given as a MAC address
ip -br link | grep -i 00:11:22:33:44:55           # that MAC: the NIC's name, e.g. enp5s0
grep enp5s0 /proc/interrupts                      # the IRQ number(s) of the NIC, e.g. 51
echo 6 | sudo tee /proc/irq/51/smp_affinity_list
cat /proc/irq/51/effective_affinity_list          # check: "6"
```

`irqbalance`, if it runs, moves interrupts back: stop it or exclude that IRQ.
NICs with several queues have one IRQ per queue.

### 5. A fixed CPU frequency

With the default governor the CPU slows down when it has little to do and
speeds up again when work arrives, so a cycle can start on a core that is
still running slowly. The `performance` governor keeps the engine's cores
at full speed:

```sh
for c in 6 7 14 15; do
    echo performance | sudo tee /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor
done
```

### 6. If the margin is still short

- **Isolated core.** Even without your scripts, kernel threads and the timer
  tick still run on the engine's CPU. Boot with
  `isolcpus=managed_irq,domain,7,15 nohz_full=7,15 rcu_nocbs=7,15`.
- **`PREEMPT_RT` kernel.** Debian ships one (`linux-image-rt-amd64`). The
  IgH kernel modules must be built for it, and some proprietary drivers do
  not support it (the NVIDIA driver, for one): check that before installing
  it on a machine with a desktop, it suits a machine without one.
- **A native IgH NIC driver** (`ec_r8169`, `ec_igb`, `ec_e1000e`...)
  instead of `ec_generic`, when IgH supports your NIC on your kernel: frames
  bypass the Linux network stack. You need a NIC whose chip that driver
  supports.

### Undoing the runtime changes

```sh
echo voluntary | sudo tee /sys/kernel/debug/sched/preempt
echo 0-15 | sudo tee /proc/irq/51/smp_affinity_list          # all CPUs (here, 16)
for c in 6 7 14 15; do
    echo schedutil | sudo tee /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor
done
```

and start the engine without `taskset`. A reboot undoes them too.

## How to measure your machine

- On the engine side, `Trace.skipped_cycles` and the `cycles_skipped`
  events tell whether the margin holds under your real load; run the
  machine with its scripts for as long as you can.
- `cyclictest` (package `rt-tests`) measures the latency the platform alone
  gives, without OdyCAT.

Change one thing at a time and measure after each: that tells you which
steps your machine needs.

## DC slaves

Sync0 must be a whole multiple of the bus cycle. When the engine cannot get
real-time scheduling, or the margin is short, give DC slaves a Sync0 of
several bus cycles (see
[configuration.md](configuration.md#sync-modes-and-a-master-without-real-time-scheduling)).
The steps above are what let a drive run with a Sync0 of one bus cycle.

## Scripts

The engine does not wait for scripts, so a slow or stalled script does not
directly block its cycle loop. The engine itself can still run late because
of the OS, hardware or other load. A script can also miss cycles itself: if
it does heavy work with libraries such as OpenCV or numpy, draws with
matplotlib or PyQtGraph, or talks to a database such as SQLite or a
Redis-like cache, it may miss cycles on the Python side. Keep time-sensitive
control separate from heavy work. What happens when a script stalls or stops
is in [intended use](intended-use.md).

For example, on our test rig at 2 kHz, each of 300 test scripts running at
the same time and reading every cycle missed about 28 % of them (1.5 % at
1 kHz). Keep per-cycle loops to the few scripts that need them, and use
`Events` or the trace for the rest.

## Keeping the settings

For more in-depth information about real-time scheduling, see your
distribution's documentation and the IgH EtherCAT Master documentation.

To make the steps above persist across reboots:

| Setting | Persistent form |
|---|---|
| Full preemption | `preempt=full` on the kernel command line (`GRUB_CMDLINE_LINUX_DEFAULT` in `/etc/default/grub`, then `sudo update-grub`) |
| Engine CPU | `taskset -c` in the command that starts the engine, or `CPUAffinity=` in its systemd unit |
| NIC IRQ | A boot-time unit or script writing `/proc/irq/<n>/smp_affinity_list` (the IRQ number can change between boots: look it up by NIC name) |
| Governor | `cpufreq.default_governor=performance` on the kernel command line |
| Isolated core | `isolcpus=`, `nohz_full=`, `rcu_nocbs=` on the kernel command line |
