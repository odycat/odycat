<!-- Translation of docs/troubleshooting.md. English text translated: git blob d843be5f7e577896c1e6947f904fc46ce16b175f (make docs-es-status lists the pages whose English changed since) -->

# Resolución de problemas

## Errores al arrancar el engine

El engine imprime un escaneo del bus antes de configurar nada: los esclavos que necesitan atención y, con `-v` (`sudo odycat -v machine.json`), todos. Léelo primero:

```
OdyCAT: bus scan — 6 slave(s) found, 6 configured
  [ 0] #60    0x00000A88:0x0A880002  PREOP  ECT60 ...      OK -> stepper_x (60:0)
  [ 1]        0x00000B95:0x00001101  PREOP  ...            OK -> io_module (60:1)
  [ 2] #1     0x000001DD:0x10518000  PREOP  ...            not in config (stays in PREOP)
```

| Mensaje | Causa / solución |
|---|---|
| `config missing field '<x>'` / `... in slave <i>` | Falta un campo obligatorio o tiene un tipo incorrecto (`vendor_id` y `product_code` deben ser cadenas) |
| `config JSON parse error near: ...` | JSON no válido (coma final, comentario, ...) |
| `slave <i> uses "device": <X>, a device class from the Python package` | El engine acepta configuraciones escritas completas: `odycat-config expand config.json -o machine.json` (como tu usuario), y arráncalo con `machine.json` |
| `unknown device '<X>' (known: ...)` | No hay ninguna clase con ese nombre: usa uno de los nombres listados o escribe la entrada del esclavo completa |
| `<rxpdo/txpdo> index 0xNNNN is not an RxPDO / a TxPDO` (or `is outside ...`) | `rxpdo` contiene las salidas del esclavo (RxPDO `0x16xx`) y `txpdo` sus entradas (TxPDO `0x1Axx`): mueve el PDO a la otra clave |
| `another OdyCAT instance is already running for bus '<x>' (pid N)` | Ya hay un engine funcionando para ese bus (también como servicio: `systemctl status 'odycat@*'`). Páralo |
| `cannot create run directory '/run/odycat'` | Sin sudo el engine no puede crearlo: arráncalo con `sudo`, como servicio, o define `ODYCAT_RUN_DIR=$XDG_RUNTIME_DIR/odycat` (y lo mismo para tus scripts) |
| `the engine cannot create files in run directory '/run/odycat'` | El directorio pertenece a root o al usuario del servicio: igual que el caso anterior |
| `run directory '<d>' is writable by its group or by others` / `... belongs to uid N` / `... is not on tmpfs` | El directorio de ejecución debe ser privado del engine y estar en RAM: aplícale `chmod 0750`, o apunta `ODYCAT_RUN_DIR` a un directorio tuyo bajo `/run` |
| `'<file>' already exists and was not left by an OdyCAT engine (owner uid N)` | Hay un fichero que no creó el engine donde va una región. Averigua de dónde viene antes de borrarlo |
| `group 'odycat' does not exist` | `sudo groupadd odycat; sudo usermod -aG odycat $USER`, y vuelve a iniciar sesión; o define `shm_group` |
| `cannot give '...' to group '...'` | El usuario del engine no está en el grupo (o no ha vuelto a iniciar sesión). Ejecútalo con sudo o corrige el grupo |
| `could not acquire IgH master N ...` | `ec_master` no está cargado (`sudo systemctl start ethercat`), el maestro N no está configurado (`MASTERn_DEVICE` en `/etc/ethercat.conf`), no hay acceso a `/dev/EtherCATn`, u otro engine o programa tiene ese maestro (dos configuraciones con el mismo `"master"`) |
| `'<slave>' is on the engine of bus 'X'; this Trace/Events reads 'Y'` | Un dispositivo de un bus pasado a los `Events`/`Trace` de otro. Crea un `Events(bus=...)` por bus |
| `N configured slave(s) missing or with wrong identity` | Mira el escaneo: `MISMATCH` = otro fabricante/producto en esa dirección (siempre fatal); `MISSING` = la dirección no está en el bus (fatal solo con `--strict`). Revisa el orden del cableado, el alias y la alimentación |
| `WARNING: N configured slave(s) not on the bus: the engine runs without them` | El bus funciona; los scripts reciben `DeviceLostError` (fuera de línea) en esos esclavos hasta que aparezcan. Revisa cableado, alimentación y alias. Con `--strict` se niega a arrancar |
| `WARNING: slave A:P (label) did not reach SAFEOP in 15 s` / `left OP after N ms: the engine runs without it`, then `N slave(s) not in OP` with the state and AL status code | El bus funciona sin ese esclavo y el engine sigue pidiéndole OP; se incorpora cuando se mantiene en OP durante 500 ms. Cada intento queda en `dmesg`, varias veces por segundo para un esclavo que no deja de caerse (ver [getting-started.md](getting-started.md#the-kernel-log)). El código de estado AL dice por qué: "Freerun not supported" (0x0027) = el esclavo necesita `dc_sync`; "Invalid input/output configuration" (0x001E/0x001D) = el mapeo de PDO no coincide con el esclavo; los códigos "Synchronization error" / "DC ..." = ajustes de DC o ciclos tardíos |
| `alias N is set on more than one slave` | Los alias deben ser únicos; reprograma uno (`ethercat alias -p <pos> <alias>`) |
| `slaves 'a' and 'b' have the same address` | `alias:position` duplicado en la configuración |
| `... entries do not end on a byte boundary (N trailing bits)` | Añade una entrada de relleno `{"index": "0x0000", "subindex": 0, "bits": N}` |
| `... 'type' must be one of ...` / `needs a 'type' ... or 'bits'` / `has "bits": 16: give a value of that size its type` | Ver la tabla de tipos en [configuration.md](configuration.md) |
| `... has entries but no PDO 'index'` | Da a cada PDO con entradas su `index` (`"0x1A00"`, `"0x1600"`, ...) |
| `too many PDOs in one direction` / `too many PDO entries in one direction` | Los límites son 32 PDO y 256 entradas por sentido |
| `... entry 0xXXXX:YY registration failed` / `at bit N, expected M` | La entrada no está en el PDO con el que ha quedado el esclavo; probablemente el esclavo ignoró el mapeo (PDO fijos). Copia el mapeo de `ethercat pdos` |
| `... SDO N: 8-byte SDOs are not supported` | Usa tipos de 1, 2 o 4 bytes |
| `... SDO N: 'value' X does not fit u16 (0..65535)` (or another type) | El valor está fuera del rango del `type` declarado. Corrige el valor o el tipo; no se trunca nada |
| `... SDO N: 'value' must be an integer ...` / `'index' must be ...` / `'subindex' must be ...` | Escribe los números como números JSON, o como cadenas en decimal o hexadecimal `0x` sin nada más |
| `cycle_us=N is not supported` | Usa uno de estos valores: 125, 250, 500, 1000, 2000, 4000, 8000, 10000 µs |
| `could not lock memory (mlockall)` / `could not set SCHED_FIFO ... require_rt is set` | Fatal solo con `require_rt`. Ejecútalo como root, o da al engine `CAP_IPC_LOCK` (o un `RLIMIT_MEMLOCK` grande) y `CAP_SYS_NICE` |
| `ecrt_master_activate failed` | Normalmente, una configuración de PDO no válida |
| `could not reach PREOP` (with `--strict`) | Los esclavos no responden: cableado, alimentación, enlace |
| `slave A:P (label) is <state>, expected SAFEOP` then `could not reach SAFEOP` (with `--strict`) | Mapeo de PDO rechazado, un SDO abortado o (accionamientos con DC) `dc_sync` ausente o incorrecto. El estado incluye el código de estado AL; `dmesg` muestra la parte de IgH (por ejemplo, el código de abort del SDO) |
| `... expected OP` / `could not reach OP` (with `--strict`) | A menudo es DC: el accionamiento necesita Sync0 (`dc_sync`) u otro `assign_activate`; o un problema de watchdog o de SM |
| `slave A:P (label) reached OP and left it after N ms` (with `--strict`) | El esclavo aceptó OP y luego lo rechazó con un error; el código de estado AL del mensaje dice por qué. "Freerun not supported" (0x0027) significa que el esclavo necesita `dc_sync` |
| `EngineStoppedError: the engine of bus 'X' stopped` / `has not run a cycle for N ms` | El engine se ha parado, lo han matado, ha fallado o está colgado. Arráncalo de nuevo y reinicia el script |
| `... was left by an engine that is no longer running (pid N)` | Al engine lo mataron o falló y dejó su región. Arranca el engine de nuevo |
| `WARNING: could not lock memory (mlockall)` | No es fatal (salvo con `require_rt`). Los fallos de página pueden retrasar ciclos. Ejecútalo como root o concede `CAP_IPC_LOCK` / un límite memlock mayor |
| `WARNING: could not set SCHED_FIFO priority 80` | No es fatal (salvo con `require_rt`). Ejecútalo como root o concede `CAP_SYS_NICE` / un límite rtprio para la planificación en tiempo real. Sin ella, da a los esclavos con DC un `sync0_cycle_ns` más largo (ver la referencia de configuración) |
| Las salidas de un esclavo dejan de cambiar al cabo de unos minutos, el esclavo sigue en OP y el WKC es correcto | El esclavo se ha quedado con un error de sincronización tras ciclos tardíos: `ethercat upload -p N --type bool 0x1c32 0x20` lee 1. Bórralo con `ethercat states -p N SAFEOP` y después `OP`. El engine pone en free-run los esclavos sin `dc_sync` para evitarlo; comprueba que el log de arranque lo dice para ese esclavo |
| `device names are "vendor.Model" ...; got "ECT60". Did you mean 'leadshine.ECT60'?` | Las configuraciones nombran las clases de dispositivo con su fabricante: `"device": "leadshine.ECT60"` |
| `CSP needs DC: add "dc_sync": ...` | Los modos síncronos cíclicos necesitan DC: añade `"dc_sync": 0` (o un periodo de Sync0 en ns) a ese esclavo y ejecuta el engine con planificación en tiempo real |
| `WARNING: slave ... left OP. It uses DC ... without real-time scheduling` (engine), same text in `DeviceLostError` | Un ciclo tardío dejó un periodo de Sync0 sin datos. Ejecuta el engine con `sudo` / `CAP_SYS_NICE`, o quita `dc_sync` si el esclavo no lo necesita (los modos de perfil y las E/S no lo necesitan) |
| Un accionamiento con DC cae a INIT/PREOP cada pocos minutos y vuelve (código de error `0x7500`) | Periodos de Sync0 sin datos nuevos por ciclos tardíos. Planificación en tiempo real, o un `dc_sync.sync0_cycle_ns` más largo |
| `dc_sync Sync0 period N ns must be a whole multiple of the bus cycle` | Usa `cycle_us × 1000` multiplicado por 1, 2, 4... |

`dmesg -w` (el log del kernel de IgH) y `ethercat slaves`, `ethercat pdos -p N`, `ethercat sdos -p N`, `ethercat upload -p N 0x6041 0` ayudan a diagnosticar los esclavos.

## Módulos Delta AX-516 atascados sin datos de proceso

Llegan a PREOP pero nunca a SAFEOP / no muestran PDO: hay que completar su SII una vez, con `odycat-config prepare --position <ring position>` (ver [Preparar un dispositivo](tools.md#preparing-a-device-odycat-config-prepare)). Los constructores de `AX516AM`/`AX516AN` lanzan `RuntimeError` si la configuración no mapea 2 bytes.

## Una actualización del kernel se detiene con un error de DKMS (Armbian)

`apt upgrade` termina con `Error! Bad return status for module build on
kernel: <version>` y `linux-image-... post-installation script subprocess
returned error exit status 1`, y
`/var/lib/dkms/ethercat/<IgH version>/build/make.log` dice
`scripts/basic/fixdep: not found`. El paquete del kernel compiló los módulos
de IgH antes de que el paquete de cabeceras hubiera preparado sus
herramientas de compilación. Cuando apt haya terminado, completa la
actualización:

```sh
sudo dpkg --configure -a
dkms status          # ethercat/<IgH version>, <nuevo kernel>: installed
```

y reinicia con el kernel nuevo.

## Errores de Python

| Error | Causa / solución |
|---|---|
| `no running engine detected (no *_config in <run dir>)` | Arranca el engine; comprueba que ha llegado al bucle RT. Un engine arrancado con `ODYCAT_RUN_DIR` necesita el mismo `ODYCAT_RUN_DIR` en el script |
| `... refusing to use it` (symbolic link, open to all users, run directory writable by others, belongs to uid N) | La región no la creó el engine, o el directorio de ejecución no es privado. No lo esquives: averigua quién creó el fichero |
| `multiple engines detected (...)` | Pasa `bus=` |
| `no permission to open /run/odycat/...` | Añade el usuario al grupo de la memoria compartida y vuelve a iniciar sesión |
| `KeyError: no slave with address A:P ...` | La dirección debe coincidir con la entrada de la configuración tal como está escrita (alias + posición), o usa `label=` |
| `KeyError: no entry '<name>' (available: ...)` | Usa una de las etiquetas listadas o `"0xINDEX:SUB"` |
| `ObjectNotMappedError: object 0xXXXX:YY is not in this slave's PDO map` (also from `Events` when watching it) | Añádelo a la configuración (`txpdo` para las entradas, `rxpdo` para las salidas) o defínelo como SDO de arranque, y reinicia el engine |
| `TypeError: in03 (slave '...') is the variable itself, not its value` | Una variable de dispositivo sin `()` usada como valor (`if io.in03:`). Escribe `io.in03()` |
| `ValueError: ... is an input; only outputs can be written` | Las entradas vienen del esclavo; no se pueden escribir |
| `struct.error: unpack requires a buffer of N bytes` | Una clase de formato fijo no coincide con el mapeo de la configuración. Ajusta la configuración a la disposición documentada |
| `RuntimeError: OdyCAT: the engine stopped in the middle of copying the inputs` | El engine no avanzó durante 10 ciclos (al menos 50 ms) mientras copiaba las entradas: lo mataron o se congeló a mitad de ciclo. Reinícialo. Una máquina cargada o un engine sin RT solo hacen esperar las lecturas, nunca fallar |
| `RuntimeError: ... did not reach Operation-Enabled (statusword=0x....)` | El accionamiento no está listo: fallo, falta la alimentación de la etapa de potencia, STO activo, DC sin configurar. Revisa `error_code()` |
| `RuntimeError: drive did not accept mode N` | El accionamiento no admite ese modo, o 0x6061 no está bien mapeado |

Cuando el engine se reinicia, los objetos `Bus` y `Device` existentes no se reconectan. El acceso a los dispositivos lanza `EngineStoppedError`; crea objetos nuevos. `BusMonitor` se vuelve a conectar solo.

## Síntomas en funcionamiento

| Síntoma | Comprobar |
|---|---|
| `bus.wkc_ok` false / `error_flags & 1` | Un esclavo no intercambia datos (perdido, no está en OP). `BusMonitor().snapshot()` muestra cuál |
| `error_flags & 2` | Un esclavo configurado está fuera de línea o no operativo, o responden menos esclavos de los configurados |
| `cycle_count` not increasing | Engine parado o bloqueado; mira su terminal |
| Se escriben salidas pero no pasa nada | Esclavo equivocado, o punto de E/S equivocado (los nombres siguen lo que está impreso en el dispositivo, ver [devices.md](devices.md#io-names)); salida sobrescrita por otro script; accionamiento no habilitado |
| El accionamiento se habilita pero no se mueve en CSP/CSV | Modo no establecido (comprueba `mode_display()`); falta DC; la consigna no cambia; límites activos (`digital_inputs()`) |
| Valores con un ciclo de retraso | Es lo esperado: las entradas son de la última trama recibida y las salidas salen con la siguiente trama |
