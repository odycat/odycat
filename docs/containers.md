# Scripts in containers

The engine runs on the machine itself: it needs real-time scheduling, the
bus network card and the IgH master. Your scripts can run in containers: a
vision application, a SCADA, a data collector, each in its own image,
attached to the engine through its shared memory exactly as a script on the
machine is.

## What a container needs

1. **The engine's run directory**, at the same path:
   `-v /run/odycat:/run/odycat`. Mount the directory, not the files in it:
   when the engine restarts it creates new files.
2. **The engine's group**, by number: `--group-add <GID>`, the GID of the
   `odycat` group on the machine (`getent group odycat`). The group name does
   not exist inside the image; the number is what the kernel checks.
3. **The `odycat` package of the same release as the engine**, installed in
   the image (`pip install odycat==X.Y.Z`).

Nothing else: no `--privileged`, no host network, no devices, no extra
capabilities. A container never opens `/dev/EtherCAT0`.

## Example

```dockerfile
FROM python:3.13-slim
RUN pip install --no-cache-dir odycat==X.Y.Z
COPY app.py /app/app.py
CMD ["python", "/app/app.py"]
```

```sh
docker run --rm \
  -v /run/odycat:/run/odycat \
  --group-add "$(getent group odycat | cut -d: -f3)" \
  my-vision-app
```

With Docker Compose:

```yaml
services:
  vision:
    build: .
    volumes:
      - /run/odycat:/run/odycat
    group_add:
      - "987"                 # getent group odycat
    restart: unless-stopped
```

A run directory other than `/run/odycat` (`ODYCAT_RUN_DIR` on the machine)
is mounted at the same path and given to the container with
`-e ODYCAT_RUN_DIR=...`.

## Starting, stopping, restarting

- **Start the engine first.** A container that attaches while the engine is
  still bringing the bus to OP is refused: it cannot tell a starting engine
  from one that died. A restart policy (`restart: unless-stopped`) or a retry
  in the script covers it.
- **When the engine stops or restarts**, scripts in containers get
  `EngineStoppedError` on device access. Restart the script to attach again;
  a restart policy helps if the container's main process exits.
- **`docker stop`** sends SIGTERM. If the Python script exits normally before
  Docker forces it to stop, its output cleanup runs (see
  [Outputs when the script ends](python-api.md#outputs-when-the-script-ends)).
- **`docker kill`, an out-of-memory kill or a hung container** leave the
  outputs as they were, and the engine keeps sending them. The same rule as
  for any script applies: see [intended use](intended-use.md).

## Timing

The engine does not wait for containerised scripts. The machine's load and
container CPU limits still matter for timing: see
[Scripts](realtime.md#scripts) in the real-time guide.
