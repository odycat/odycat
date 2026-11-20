<!-- Translation of docs/getting-started.md. English text translated: git blob f6434dbd8b491aa4bd69746bf02d1b82cb149ef9 (make docs-es-status lists the pages whose English changed since) -->

# Primeros pasos

## Inicio rápido

Con una instalación limpia de Debian 13, Ubuntu 24.04 o 26.04 LTS, Raspberry Pi OS o Armbian (x86-64 o aarch64), y con los esclavos EtherCAT en una tarjeta de red propia:

```sh
sudo apt install git
git clone https://github.com/odycat/odycat
sudo odycat/install/install.sh          # asks which network card is the bus
# log out and in again: the installer added you to the odycat group

mkdir odycat_test && cd odycat_test
python3 -m venv .venv
source .venv/bin/activate
pip install odycat
odycat-config scan -o busconfig.json    # a config from the connected slaves
sudo odycat busconfig.json              # the engine: leave it running

# in another terminal, in odycat_test:
.venv/bin/python -c "from odycat import Bus; b = Bus(); print('OP' if b.is_op else 'not OP', b.work_counter, '/', b.expected_wkc)"
```

Paso a paso:

1. **El instalador** pregunta qué tarjeta de red es el bus, compila los módulos del kernel de IgH con DKMS (una actualización del kernel los vuelve a compilar), instala el engine y los permisos, y comprueba que el maestro funciona. Sus opciones (`--help`) y lo que cambia están en [install/README.md](../install/README.md).


2. **Reinicia el equipo (o reinicia la sesión de tu usuario)** El instalador te añade al grupo `odycat`, que da acceso al maestro (`/dev/EtherCAT0`) y a la memoria compartida del engine. El motivo del reinicio es porque es necesario recargar los permisos de usuarios. Hasta que esto no suceda, el scan falla con "Permission denied" por carecer de acceso a la memoria compartida.


3. **El paquete de Python** va en un entorno virtual de tu proyecto (también puedes instalarlo en el sistema con la opción de pip, pero no se recomienda). El engine odycat y la librería deben estar en la misma versión, `pip install odycat` instala la última release, que es la que instala el `main` del repositorio. Para una release anterior, `pip install odycat==X.Y.Z`.


4. **`odycat-config scan`** escribe una configuración a partir de los esclavos que encuentra en el bus, leyendo directamente desde la memoria del dispositivo. La configuración generada cuenta con etiquetas que puedes renombrar (campo label) para que coincida con tu setup/esquema eléctrico ([3. Escribir una configuración](#3-write-a-config)).


5. **Cuando el engine** arranca el bus, imprime unos mensajes mínimos sobre su estado. Si existe alguna inconsistencia o error en la configuración de esclavos, el motor se detiene y el proceso finaliza. ([4. Arrancar el engine](#4-start-the-engine)). Cuando el motor está corriendo, se detiene con Ctrl-C en el terminal o sigkill/pkill al proceso correspondiente.

6. **La comprobación** se conecta al engine en marcha: `OP` y un working counter igual al esperado significan que todos los esclavos intercambian datos de proceso. Después, sigue en [5. Primer script](#5-first-script).

Para máquinas en producción, instala desde los ficheros de la release en lugar del repositorio: descarga `odycat-install-X.Y.Z.tar.gz` y `SHA256SUMS` de la release y después:

```sh
sha256sum -c --ignore-missing SHA256SUMS
tar -xzf odycat-install-X.Y.Z.tar.gz
sudo odycat-install-X.Y.Z/install.sh
```

El resto de esta página es la instalación manual, y explica lo que hace el instalador.

## 1\. Requisitos

| Qué | Notas |
|---|---|
| Linux: Debian 13, Ubuntu 24.04 o 26.04 LTS, Raspberry Pi OS o Armbian ([compatibility.md](compatibility.md)); x86_64 o aarch64 | Se recomienda un kernel PREEMPT_RT para un jitter bajo, pero no es obligatorio; ver [realtime.md](realtime.md). Placas ARM: ver más abajo |
| Maestro EtherCAT IgH ≥ 1.6 | Módulo del kernel `ec_master` + userland `libethercat` y `ecrt.h` (probado con 1.6.13, instalado en `/usr/local`) |
| `libcjson-dev`, `gcc`, `make` | Para compilar el engine |
| Python ≥ 3.11 | Sin dependencias en tiempo de ejecución. Al instalar el paquete se compila un pequeño módulo en C (`odycat._atomic`); en ARM es necesario para escribir salidas, así que instala antes `build-essential` y `python3-dev` |

### Dispositivo del maestro IgH

El engine usa el **maestro 0 de IgH** salvo que la configuración indique `"master"`. Qué tarjeta de red usa un maestro lo decide IgH, no OdyCAT: pon `MASTER0_DEVICE` (dirección MAC) y `DEVICE_MODULES` en `/etc/ethercat.conf` (o `/etc/sysconfig/ethercat`) y arranca el maestro (`sudo systemctl start ethercat`). Compruébalo con:

```sh
ethercat master      # link up, slaves found
ethercat slaves      # positions, aliases, states
```

El campo `bus` de la configuración de OdyCAT **da nombre** al bus: a sus regiones de memoria compartida (`/run/odycat/<bus>_shared`, `_config`, `_trace`) y al nombre al que se conectan los scripts. No elige la tarjeta de red: eso lo hace IgH, y el `"master"` de la configuración elige el maestro.

### Placas ARM (Raspberry Pi, Orange Pi, ...)

Para un sistema operativo de 64 bits con (aarch64), lo que cambia respecto a un PC (x86-64):

- **La tarjeta del bus.** IgH no suele tener driver nativo para el Ethernet de una placa: usa `DEVICE_MODULES="generic"`. Evita que el gestor de red configure esa tarjeta (enviaría peticiones DHCP por el bus); si usa NetworkManager:

  ```ini
  # /etc/NetworkManager/conf.d/90-ethercat-unmanaged.conf
  [keyfile]
  unmanaged-devices=interface-name:end0
  ```

  Una tarjeta no gestionada se queda apagada al arrancar, y el driver genérico la necesita levantada. Espera también al enlace: el maestro lee la identidad (SII) de cada esclavo en cuanto arranca, y una lectura sin respuesta deja ese esclavo sin identificar (`ethercat slaves` muestra `0x00000000:0x00000000`, y el log del kernel "SII read datagram failed") hasta un `ethercat rescan`:

  ```ini
  # /etc/systemd/system/ethercat.service.d/link-up.conf
  [Service]
  ExecStartPre=/sbin/ip link set end0 up
  ExecStartPre=/bin/sh -c "for i in $(seq 100); do [ \"$(cat /sys/class/net/end0/carrier)\" = 1 ] && exec sleep 1; sleep 0.1; done"
  ```
- **Compilar IgH.** Necesita las cabeceras del kernel en uso (Armbian: `linux-headers-current-<family>`). Con un kernel más nuevo que la release de IgH, puede que el driver genérico no compile; busca una corrección en el repositorio de IgH antes de parchear.
- **Planificación de tiempo real.** Si el engine avisa de que se le ha denegado `SCHED_FIFO` aunque se ejecuta como root, ver "Run the engine with real-time scheduling" en [realtime.md](realtime.md). Con pocos núcleos, dar al engine un núcleo propio importa más que en un PC.
- **El paquete de Python** compila `odycat._atomic` al instalarse; sin compilador de C se instala sin él y se niega a escribir salidas, con un mensaje que lo explica.

### Varios buses

Cada tarjeta de red es un maestro IgH propio (`MASTER1_DEVICE`, ... en `/etc/ethercat.conf`) y tiene su propio engine, con su propia configuración, tiempo de ciclo y memoria compartida. Da a cada configuración su maestro y un nombre de bus (`"master": 1, "bus": "io"`; `odycat-config scan --master 1` escribe ambos, con `bus1` salvo que `--bus` diga otra cosa), arranca un engine por configuración y pasa `bus=` en Python (ver la API de Python, "Bus auto-detection").

### Permisos

El engine guarda su memoria compartida como ficheros en tmpfs, en su directorio de ejecución `/run/odycat` (modo `0750`: solo el engine crea ficheros ahí). Las regiones pertenecen a un grupo (por defecto `odycat`; la región de datos, donde los scripts escriben salidas, tiene modo `0660`, las demás `0640`). Todo usuario que ejecute un script de Python, o el engine, tiene que estar en él:

```sh
sudo groupadd odycat
sudo usermod -aG odycat $USER     # then log out and in again
```

El engine necesita además acceso a `/dev/EtherCAT0` y, para funcionar en tiempo real, `CAP_SYS_NICE` (planificación) y `CAP_IPC_LOCK` o un límite de memlock suficiente (para bloquear su memoria en RAM). Ejecutar el engine con `sudo` cubre ambas cosas; la memoria compartida sigue siendo del grupo `odycat`, así que los scripts de Python pueden ejecutarse como un usuario normal. Para ejecutar el engine sin root, el usuario necesita acceso de lectura y escritura a `/dev/EtherCAT0`, por ejemplo con esta regla de udev:

```sh
sudo cp packaging/udev/99-ethercat.rules /etc/udev/rules.d/   # GROUP="odycat", MODE="0660"
sudo udevadm control --reload && sudo udevadm trigger
```

## 2\. Compilar e instalar

OdyCAT tiene dos partes, que se instalan por separado. Instala las dos de la misma versión: comparten el formato de la memoria compartida.

**El engine** (C, se arranca con `sudo`). Compílalo como tu usuario y después instálalo con `sudo`: va a `/usr/local/bin`, propiedad de root, para que solo root pueda sustituir el programa que ejecuta `sudo` (`make -C engine deps` indica cualquier dependencia de compilación que falte):

```sh
make -C engine
sudo make -C engine install     # /usr/local/bin/odycat (PREFIX=... for another place)
```

**El paquete de Python**, en el virtualenv de tu proyecto:

```sh
pip install odycat
```

Desde este checkout, en cambio, `make install` construye el wheel y lo reinstala en el virtualenv activo (o en `.venv`, o en `python3`). `make` lista las demás tareas.

Para desarrollo, usa el repositorio directamente: pon `src/` en `PYTHONPATH`, compila con `make engine` y arranca `engine/odycat`.

{#3-write-a-config}
## 3\. Escribir una configuración

Deja que OdyCAT escriba una primera a partir de los esclavos del bus (no hace falta sudo) y después renombra las etiquetas:

```sh
odycat-config scan -o machine.json
```

Escribe los esclavos que tienen clase de dispositivo a partir de la clase (con los mismos nombres de entradas que la clase y los ejemplos) y los demás a partir de lo que informan, todo completo, así que el engine la acepta tal cual (ver [configuration.md](configuration.md#deriving-a-config-from-the-hardware)). Para ver los esclavos tú mismo:

```sh
ethercat slaves -v      # vendor id, product code, alias
ethercat pdos -p 0      # PDO mapping of slave 0
```

Para un dispositivo que tiene clase en OdyCAT (ver [devices.md](devices.md)), nómbralo con `device` y da su dirección y una etiqueta; la clase completa la identidad, el mapeo de PDO y los ajustes de DC. Configuración mínima con un terminal de 16 salidas Beckhoff EL2809:

```json
{
  "bus": "bus0",
  "cycle_us": 1000,
  "slaves": [
    { "device": "beckhoff.EK1100", "position": 0, "label": "header" },
    { "device": "beckhoff.EL2809", "position": 1, "label": "outputs" }
  ]
}
```

Cualquier otro esclavo se describe completo: la identidad y los PDO que mapear, con `rxpdo` para sus salidas (RxPDO `0x16xx`) y `txpdo` para sus entradas (TxPDO `0x1Axx`), tal como los lista `ethercat pdos`:

```json
{
  "position": 1,
  "label": "my_io",
  "vendor_id": "0x00000b95",
  "product_code": "0x00001101",
  "rxpdo": { "index": "0x1600", "entries": [
    { "index": "0x7000", "subindex": 1, "type": "u16", "label": "do" } ] },
  "txpdo": { "index": "0x1A00", "entries": [
    { "index": "0x6000", "subindex": 1, "type": "u16", "label": "di" } ] }
}
```

Ver [configuration.md](configuration.md) para todos los campos.

{#4-start-the-engine}
## 4\. Arrancar el engine

```sh
sudo odycat machine.json
```

`odycat` es el engine compilado y acepta configuraciones escritas completas, como las escribe `odycat-config scan`. Si has escrito una configuración que nombra clases de dispositivo (`"device": "leadshine.ECT60"`), deberás expandirla automáticamente para que el engine la pueda usar, para ello:

```sh
odycat-config expand config.json -o machine.json
```

`sudo` lo encuentra en `/usr/local/bin` (paso 2). En un checkout del repositorio también puede ejecutarse desde `engine/odycat`.

Imprime lo que requiere atención, y poco más cuando todo va bien:

```
OdyCAT: engine 1.0.0 (git ...; security fixes until ...)
OdyCAT: loaded config — bus 'machine', 6 slave(s), cycle 1000 µs
OdyCAT: bus scan — 6 slave(s) found, 6 configured
OdyCAT: bus in OP.
OdyCAT: entering RT loop (cycle = 1000 µs).
```

Un esclavo que falta o que no está en OP tiene su propia línea en el scan y después de `bus in OP`, y se imprime cada `WARNING:`. Un dispositivo de otra identidad en una dirección configurada detiene el engine antes de que configure nada. `odycat -v machine.json` imprime además el scan completo del bus, los SDO de arranque, el DC y la disposición de los datos de proceso de cada esclavo, cada estado alcanzado y las rutas de la memoria compartida: úsalo durante la puesta en marcha, o cuando algo va mal.

Cualquier problema al arrancar termina el engine con `ERROR: ...` y código de salida 1 (ver [troubleshooting.md](troubleshooting.md)). Se detiene con Ctrl-C o `SIGTERM`; cerrar su terminal (`SIGHUP`) lo detiene igual: el engine desactiva y libera el maestro, y después elimina sus regiones de memoria compartida y su bloqueo.

Solo puede ejecutarse un engine por nombre de bus (fichero de bloqueo `/run/odycat/<bus>.lock`).

### Tres formas de arrancarlo

| | Cómo | Directorio de ejecución |
|---|---|---|
| **A mano** (bancos de pruebas, sistemas sin systemd) | `sudo odycat machine.json` | `/run/odycat`, creado por el engine (`0750`, `root:odycat`) si no existe |
| **Como servicio** (máquinas) | `sudo make -C engine install-service` una vez, después una configuración en `/etc/odycat/<name>.json` (`root:odycat`, `0640`) y `systemctl enable --now odycat@<name>` | `/run/odycat`, creado por systemd (`0750`, propiedad del usuario de sistema `odycat`) y eliminado cuando el servicio se detiene |
| **Desarrollo, sin sudo** | `ODYCAT_RUN_DIR=$XDG_RUNTIME_DIR/odycat engine/odycat machine.json` | Tu directorio runtime privado: solo tus propios scripts pueden conectarse. Pon el mismo `ODYCAT_RUN_DIR` para ellos |

El servicio se ejecuta como el usuario de sistema `odycat`, solo con las dos capacidades que necesita el tiempo real (`CAP_SYS_NICE`, `CAP_IPC_LOCK`), en un sandbox: sin root en ningún momento. Los miembros del grupo `odycat` pueden arrancarlo y detenerlo sin sudo (`systemctl start odycat@<name>`, regla de polkit). La CPU de su bucle de tiempo real se indica en un drop-in (`CPUAffinity=`, ver [realtime.md](realtime.md)).

Python busca las regiones en `$ODYCAT_RUN_DIR`, si no en `/run/odycat`, y rechaza una región que no haya creado el engine (ver [troubleshooting.md](troubleshooting.md)).

{#slaves-out-of-op}
### Esclavos fuera de OP

Si un esclavo configurado falta o no llega a OP, el engine dice cuál y por qué (su estado y su código AL status) y hace funcionar el bus sin él. Los scripts reciben `DeviceLostError` en ese esclavo, de modo que es el script el que decide si la máquina puede funcionar sin él. El esclavo se incorpora cuando lleva 500 ms en OP: IgH reintenta enseguida un esclavo que se ha caído por sí solo, y el engine vuelve a pedírselo a IgH para cualquier otro (tras 1 s, después 2, 4, ... hasta una vez por minuto), así que también se reintenta un esclavo que rechazó un cambio de estado. `sudo odycat --strict machine.json` detiene el engine en su lugar, que es lo que se quiere durante la puesta en marcha. Un dispositivo distinto en una dirección configurada lo detiene siempre.

Un esclavo que se queda fuera de OP es un fallo que hay que corregir, no un estado en el que trabajar: el informe de arranque y `DeviceLostError` dan el motivo, y `BusMonitor` muestra el estado y el código AL status de cada esclavo para un HMI o una comprobación de salud.

{#the-kernel-log}
### El log del kernel

IgH registra en el log del kernel cada intento de llevar un esclavo a OP. Un esclavo que no deja de caerse de OP (acepta OP y lo rechaza un momento después, como un accionamiento que necesita `dc_sync` y no lo tiene) se reintenta varias veces por segundo, y cada intento escribe unas 15 líneas: unas 25 líneas por segundo, mientras dure el fallo. Es IgH funcionando como debe, pero hay que preverlo:

- El log del kernel es un anillo de tamaño fijo, así que un esclavo que entra y sale de OP expulsa en minutos los mensajes anteriores (el arranque del propio engine, los errores de un driver de red). `journalctl -k` conserva lo que guardó journald; ajusta sus límites (`SystemMaxUse=` en `/etc/systemd/journald.conf`) a lo que quepa en el disco.
- rsyslog, donde está instalado, copia los mensajes del kernel en `/var/log/kern.log` y `/var/log/syslog`: unos 20 MB por hora mientras un esclavo entra y sale de OP. En placas que tienen `/var/log` en RAM (el log en zram de Armbian, unos 50 MB) eso lo llena en dos o tres horas, y después otros servicios no pueden escribir sus logs. Rota esos ficheros por tamaño, o deja los mensajes del kernel fuera de rsyslog si ya los tiene journald.
- En almacenamiento flash, los logs persistentes a ese ritmo lo desgastan: en tarjetas SD y eMMC, mantén el journal en un disco RAM o con límites estrictos.
- Los mensajes del kernel de nivel error solo salen por la consola si el nivel de log de la consola del kernel lo permite (`loglevel=` en la línea de comandos del kernel). En una consola serie esa impresión es lenta y puede retrasar los ciclos del engine: mantén bajo el nivel de la consola (`loglevel=3` o menos) en las máquinas que hacen funcionar un bus.

{#5-first-script}
## 5\. Primer script

```python
from odycat import Bus
from odycat.devices.beckhoff import EL2809

bus = Bus()                       # auto-detects the running engine
print(bus.is_op, bus.work_counter, bus.expected_wkc)

out = EL2809(label="outputs")     # or EL2809(position=0)
out.set_output_1(True)            # channel 1 on (or out.set_output(1, True))
print(out.output_1(), f"{out.outputs():016b}")
```

Todo (el bus, el offset de la memoria compartida, la disposición de los PDO) se descubre del engine en marcha. Si no hay ningún engine en marcha, los constructores lanzan `RuntimeError`. Para un esclavo sin clase, `Device(label="my_io")` lee y escribe sus entradas por etiqueta: `read_entry("di")`, `write_entry("do", 0x0001)`.

Siguiente: [python-api.md](python-api.md) y [devices.md](devices.md).

## Tests

Los tests unitarios no necesitan hardware, ni IgH, ni root:

```sh
./ci/test.sh            # pytest if installed, else stdlib unittest
```

Ver [testing.md](testing.md) para lo que cubren los tests y lo que ejecuta la CI.

{#safety-notes}
## Notas de seguridad

- **Las salidas que ha escrito un script vuelven a 0 cuando termina** (final normal, excepción no capturada, Ctrl+C, SIGTERM, SIGHUP), salvo que haya llamado a `keep_outputs_on_exit()`; un accionamiento recibe controlword 0 y se deshabilita (su posición de consigna conserva el último valor). Ver [python-api.md](python-api.md#outputs-when-the-script-ends).
- **Las salidas conservan su último valor cuando el script no puede hacer esa limpieza:** `kill -9`, una terminación por falta de memoria, un fallo, `os._exit()`, o un script que se cuelga. El engine sigue ciclando con esos valores, así que el watchdog de pérdida de bus del esclavo no detectará un script parado. Un `try/finally` o `servo.disable()` no pueden gestionar un kill ni un cuelgue. Evalúa los modos de fallo de cada máquina y aplica medidas de protección independientes donde haga falta; una salida a cero o un accionamiento deshabilitado no es necesariamente un estado seguro.
- Detener el engine lleva los esclavos a PREOP; el comportamiento físico resultante depende de la configuración de cada dispositivo y de su manual. Compruébalo en tu máquina en lugar de suponer que todas las salidas se apagan de forma segura.
- OdyCAT no es ni implementa en ningún caso funciones de seguridad: ver [intended-use.md](intended-use.md).
- Varios scripts pueden conectarse al mismo engine. Nada impide que dos de ellos escriban la misma salida; gana la última escritura.
