<!-- Translation of docs/compatibility.md. English text translated: git blob 35d2d65d08044fab43286939520c7988715cac64 (make docs-es-status lists the pages whose English changed since) -->

# Versiones y compatibilidad

OdyCAT sigue [Semantic Versioning](https://semver.org/). Una versión es `MAJOR.MINOR.PATCH`:

- Las releases **PATCH** corrigen errores.
- Las releases **MINOR** añaden funcionalidades. El código y las configuraciones escritos para una release minor anterior de la misma major siguen funcionando.
- Las releases **MAJOR** pueden quitar o cambiar lo que se cubre más abajo. El CHANGELOG dice qué ha cambiado y cómo actualizar.

## Qué se cubre

- **La API de Python**, tal como está documentada: las clases y funciones de `odycat`, `odycat.bus`, `odycat.device`, `odycat.devices` (las clases de dispositivo), `odycat.events`, `odycat.signal`, `odycat.trace` y `odycat.monitor`, con sus parámetros, valores de retorno y excepciones documentadas. Incluye las claves de `BusMonitor.snapshot()` y `layout()` que lista la [API de Python](python-api.md).
- **El fichero de configuración** (`config.json`): sus claves y lo que significan ([configuration.md](configuration.md)).
- **Los comandos**: `odycat` (el engine) y `odycat-config`, con sus argumentos.
- **La variable de entorno** `ODYCAT_RUN_DIR`.

## Qué no se cubre

- **Todo lo que empieza por `_`**: los módulos privados (`odycat._shm`, `odycat._config`, `odycat._scan`...) y los miembros privados. Las funciones de `odycat._config` documentadas en la API de Python se cubren con los nombres que se listan allí.
- **La memoria compartida del engine**: la disposición de las regiones, los offsets y los valores de los flags. Los scripts la usan solo a través del paquete. Mantenerla interna es lo que permite que releases posteriores mejoren cómo comparten datos el engine y los scripts sin romper los scripts.
- **`odycat.testing`**: el engine simulado sigue al engine y a las herramientas de test, y puede cambiar en cualquier release.
- **El texto de los mensajes**, el formato de la salida de consola del engine y de los informes de `odycat-config`. Interpreta los valores documentados, no el texto.

## El engine y el paquete

Instala el engine y el paquete de Python **de la misma release**. El paquete comprueba la disposición de la memoria compartida del engine al conectarse y rechaza la de otra versión major, indicando qué lado es más nuevo. La versión major de esa disposición solo cambia en una release major, así que dentro de una major un engine y un paquete de releases minor distintas se siguen conectando, pero solo se prueban juntos los de la misma release.

## Obsolescencia

Lo que está cubierto no se quita sin aviso:

1. Se marca como obsoleto en una release minor: sigue funcionando, lanza un `DeprecationWarning` al usarse (Python) o imprime un aviso (configuración, comandos), y el CHANGELOG lo lista en *Deprecated* con lo que hay que usar en su lugar.
2. Se mantiene al menos hasta la siguiente release major, en la que se puede quitar.

## Versiones soportadas

Las correcciones, incluidas las de seguridad, van en la última release minor de la versión major actual: actualiza dentro de la major para recibirlas, algo que las reglas anteriores mantienen libre de cambios en tu código. Ver [SECURITY.md](../SECURITY.md) para saber qué releases reciben correcciones de seguridad y cómo comunicar una vulnerabilidad.

| Requisito | Soportado |
|---|---|
| Python | 3.11 y posteriores |
| Sistemas operativos | Debian 13; Ubuntu 24.04 y 26.04 LTS; Raspberry Pi OS y Armbian basados en ellos |
| Arquitecturas | x86-64 y aarch64 |
| IgH EtherCAT Master | 1.6 (el instalador instala la 1.6.13) |
| cJSON (engine) | 1.7, tal como lo empaqueta la distribución, con sus actualizaciones de seguridad (probado: 1.7.18 en Debian 13, 1.7.17 en Ubuntu 24.04) |

El engine enlaza `libethercat` de IgH y cJSON del sistema; OdyCAT no los incluye. El instalador toma IgH de su propio repositorio, en la versión fijada, y lo compila en la máquina. Cada release se comprueba contra las vulnerabilidades conocidas de ambos, y un aviso de seguridad indica cuándo una afecta a los usuarios de OdyCAT.
