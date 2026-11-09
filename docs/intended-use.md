# Intended use

## Intended purpose

OdyCAT reads and writes the process data of EtherCAT devices from Python
programs on Linux, through the IgH EtherCAT Master, in the supported
environment described in [compatibility.md](compatibility.md).

OdyCAT is not a safety function and must not be used as one. Functions that
protect people must be implemented with safety-rated components,
independently of OdyCAT and of the computer that runs it.

The engine continues to send the last published outputs if a Python script
hangs, is killed or crashes without running its exit cleanup. A slave's
communication watchdog does not detect this while valid frames keep arriving.
Assess the resulting motion or output state for each machine, including
velocity and torque commands; use independent protective measures appropriate
to the hazard. Setting an output to zero or disabling a drive is not a
universally safe state (for example, a suspended load may need a brake).

## Security environment

- The bus uses a network interface of its own, connected only to the
  EtherCAT devices.
- Only the people and services that operate the machine are members of the
  shm group (`odycat` by default): membership gives control of every output
  on the bus, and of the slaves' EEPROM (`odycat-config prepare`).

## Foreseeable misuse

- Connecting the bus interface to another network: EtherCAT has no
  authentication, so anyone on that network could control the devices.
- Giving untrusted users or services membership of the shm group.

See also the safety notes in
[getting-started.md](getting-started.md#safety-notes).
