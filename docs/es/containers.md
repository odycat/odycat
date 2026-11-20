<!-- Translation of docs/containers.md. English text translated: git blob c529a6b3bfa38313fb1668869cb9f62a31fa87af (make docs-es-status lists the pages whose English changed since) -->

# Scripts en contenedores

El engine se debe ejecutar nativo en la propia máquina (fuera del contenedor). Ello se debe a que necesita planificación en tiempo real, acceso directo a la tarjeta de red del bus y comunicación con el maestro IgH. Tus scripts pueden ejecutarse en contenedores: una aplicación de visión, un SCADA, un recolector de datos, cada uno en su imagen, conectados al engine a través de su memoria compartida igual que un script en la máquina. Esto permite reutilizar y versionar aplicaciones para distintos modelos de equipo, backups de linea de producción, entornos de test, etc.

## Qué necesita un contenedor

1. **El directorio de ejecución del engine**, en la misma ruta: `-v /run/odycat:/run/odycat`. Monta el directorio, no los ficheros que contiene: cuando el engine se reinicia crea ficheros nuevos.
2. **El grupo del engine**, por número: `--group-add <GID>`, el GID del grupo `odycat` en la máquina (`getent group odycat`). El nombre del grupo no existe dentro de la imagen; lo que comprueba el kernel es el número.
3. **El paquete `odycat` de la misma release que el engine**, instalado en la imagen (`pip install odycat==X.Y.Z`).

Nada más: sin `--privileged`, sin la red del host, sin dispositivos, sin capacidades adicionales. Un contenedor nunca abre `/dev/EtherCAT0`ni necesita acceder a el.

## Ejemplo

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

Con Docker Compose:

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

Un directorio de ejecución distinto de `/run/odycat` (`ODYCAT_RUN_DIR` en la máquina) se monta en la misma ruta y se pasa al contenedor con `-e ODYCAT_RUN_DIR=...`.

## Arrancar, parar, reiniciar

- **Arranca primero el engine.** Un contenedor que se conecta mientras el engine todavía lleva el bus a OP es rechazado: no puede distinguir un engine que arranca de uno que ha muerto. Una política de reinicio (`restart: unless-stopped`) o un reintento en el script lo resuelven.
- **Cuando el engine se para o se reinicia**, los scripts en contenedores reciben `EngineStoppedError` al acceder a los dispositivos. Reinicia el script para volver a conectarse; una política de reinicio ayuda si termina el proceso principal del contenedor.
- **`docker stop`** envía SIGTERM. Si el script de Python termina con normalidad antes de que Docker lo fuerce a parar, se ejecuta su limpieza de salidas (ver [Salidas cuando el script termina](python-api.md#outputs-when-the-script-ends)).
- **`docker kill`, una terminación por falta de memoria o un contenedor colgado** dejan las salidas como estaban, y el engine las sigue enviando. Se aplica la misma regla que a cualquier script: ver [uso previsto](intended-use.md).

## Tiempos

El engine no espera a los scripts en contenedores. La carga de la máquina y los límites de CPU de los contenedores siguen importando para los tiempos: ver [Scripts](realtime.md#scripts) en la guía de tiempo real.
