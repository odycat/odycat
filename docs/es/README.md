<!-- Translation of docs/README.md. English text translated: git blob fea17b4215e77dc679e9d06afe76ff3b3c02bda5 (make docs-es-status lists the pages whose English changed since) -->

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/odycat_letters_dark.svg">
    <img src="img/odycat_letters_bright.svg" alt="OdyCAT® Logo" width="360">
  </picture>
</p>


<!-- site: the documentation website includes this page from here on -->

# Documentación de OdyCAT

OdyCAT es un framework EtherCAT para Linux. Se compone de un engine de tiempo real en C (`odycat`) que hace funcionar el maestro EtherCAT IgH y replica los datos de proceso de cada esclavo en memoria compartida POSIX. Los scripts de Python se conectan a esa memoria compartida a través del paquete python `odycat` e interaccionan con el bus sin bloquear al engine.

```
 ┌──────────────┐  shm /<iface>_shared   ┌──────────────────┐   IgH    ┌────────┐
 │ Python app   │ <────────────────────> │  odycat   │ <──────> │ slaves │
 │ (odycat pkg) │  shm /<iface>_config   │  (C, SCHED_FIFO) │  kernel  └────────┘
 └──────────────┘ <───────────────────── └──────────────────┘
```

## Contenido

| Documento | Contenido |
|---|---|
| [getting-started.md](getting-started.md) | Instalar, compilar, arrancar el engine, primer script |
| [architecture.md](architecture.md) | Cómo funcionan el engine, la memoria compartida y el seqlock |
| [configuration.md](configuration.md) | Descripción y detalles del `config.json` (referencia completa de los campos) |
| [python-api.md](python-api.md) | Objetos `Bus`, `Device`, `BusMonitor` y las utilidades de configuración |
| [devices.md](devices.md) | Uses las clases de dispositivo incluidas y creación de nuevas |
| [tools.md](tools.md) | `odycat-config` (scan, expand, prepare), compilar el engine y el wheel |
| [realtime.md](realtime.md) | Configuración y ajustes para un control fino del timming del bus y patrones de funcionamiento para resolver errores (ciclos que llegan tarde o se saltan, los accionamientos con DC se caen, etc) |
| [containers.md](containers.md) | Scripts en Docker u otros contenedores |
| [troubleshooting.md](troubleshooting.md) | Errores reportados por el engine y tabla causa/solución |
| [compatibility.md](compatibility.md) | Números de versión, control de cambios y obsolescencia de funciones |
| [testing.md](testing.md) | Qué cubren los tests unitarios, cómo ejecutarlos, funcionamiento de CI |
| [api.md](api.md) | Todas las clases y funciones, a partir de los docstrings (mejor en la web de la documentación, `make docs`, que también genera el catálogo de dispositivos) |
| [llm-reference.md](llm-reference.md) | Resumen autocontenido para asistentes de programación con IA (apunta a tu agente hacia este archivo para que conozca como usar la librería) |

## Estructura del repositorio

```
Makefile               Development tasks: make engine | wheel | install | test | lint
engine/                C real-time engine and its Makefile (sources: architecture.md)
setup.py, pyproject.toml
                       Wheel build: the Python package (and its C helper; the engine is installed apart)
LICENSE, LICENSING.md  GPL v3 text; dual licensing (GPL v3 or commercial)
src/odycat/            Python package
  _config.py           Config SHM reading, slave lookup, PDO layout rules
  bus.py               Bus: shm header (state, WKC, cycle counter)
  device.py            Device: base class for all slave classes
  monitor.py           BusMonitor: read-only whole-bus snapshots
  trace.py             Trace: the engine's cycle history (trace shm)
  events.py            Events: handlers called on PDO changes
  _scan.py             `odycat-config scan`: a config from the slaves on the bus
  _prepare.py          `odycat-config prepare`: the one-off change some devices need
  _esi.py              EtherCATInfo XML from `ethercat xml` (internal, for scan)
  testing.py           Fake engine for tests without hardware (may change in any release)
  _profiles.py         Device profiles ("device": "leadshine.ECT60" in the config)
  devices/             Device catalogue: vendors, "vendor.Model" names
  devices/<vendor>/    Device classes by vendor (see devices.md)
  devices/cia402/      Standard CiA 402 drive profile (Servo)
  devices/mdp/         ETG.5001 Modular Device Profile digital I/O (DigitalIO)
  devices/cia401/      CiA 401 generic I/O (GenericIO)
install/               The installer: IgH, the bus card and the engine
examples/              Example scripts and configs (demo.py + config.json, demo_box.json, events, recovery)
packaging/             System files: udev rule, systemd unit, sysusers.d, polkit rule
tests/                 Unit tests, no hardware needed (see testing.md); bus output recorded from the demo box in tests/data/
docs/                  This documentation; the website is built from it (make docs)
ci/                    The scripts the CI runs (lint, tests, builds); they work the same locally
```

## Convenciones de esta documentación

- **Los nombres de dirección siguen EtherCAT (ETG).** Los PDO se nombran desde el lado del esclavo, como en los ficheros ESI, TwinCAT y `ethercat pdos`: `rxpdo` es lo que el esclavo *recibe* (sus **salidas**: control, consignas; RxPDO `0x16xx`, SM2) y `txpdo` es lo que *transmite* (sus **entradas**: estado, valores reales; TxPDO `0x1Axx`, SM3; los esclavos sin mailbox usan SM0/SM1, indicados con `"sm"`).
- Las direcciones de los esclavos se escriben `alias:position`, como las imprime `ethercat slaves`.

---

OdyCAT® ha sido desarrollado por Javier Martínez en Ocean Octane y lo publica y mantiene Yoctovision S.L.U. bajo licencia. OdyCAT® se publica como licencia dual: GPL v3 o licencia comercial, ver [LICENSING.md](../LICENSING.md).
El nombre y símbolos OdyCAT® son una marca registrada de Ocean Octane.
