<!-- Translation of docs/intended-use.md. English text translated: git blob b805b446242cec6d36e8d95688c0f86f3df6af89 (make docs-es-status lists the pages whose English changed since) -->

# Uso previsto

## Finalidad prevista

OdyCAT lee y escribe los datos de proceso de dispositivos EtherCAT desde programas de Python en Linux, a través del IgH EtherCAT Master, en el entorno soportado que describe [compatibility.md](compatibility.md).

OdyCAT no es una función de seguridad y no debe usarse como tal. Las funciones que protegen a las personas deben implementarse con componentes de seguridad específicos (safety-rated), con independencia de OdyCAT y del ordenador que lo ejecuta.

El engine sigue enviando las últimas salidas publicadas si un script de Python se cuelga, el proceso muere o falla sin ejecutar su limpieza de salida. Esto puede estar provocado por sobrecarga computacional en los scripts de usuario, errores y/o malas prácticas de programación, fallos del sistema operativo, entre otros. El watchdog de comunicación de un esclavo no lo detecta mientras sigan llegando tramas válidas. Evalúa en cada máquina el movimiento o el estado de las salidas que resultan, incluidas las consignas de velocidad y de par; usa medidas de protección independientes y adecuadas al peligro. Poner una salida a cero o deshabilitar un accionamiento no es un estado seguro en todos los casos (por ejemplo, una carga suspendida puede necesitar un freno).

## Entorno de ciberseguridad

- El bus usa una interfaz de red propia, conectada solo a los dispositivos EtherCAT.
- Solo las personas y los servicios que operan la máquina son miembros del grupo de la memoria compartida (`odycat` por defecto): pertenecer a él da el control de todas las salidas del bus y de la EEPROM de los esclavos (`odycat-config prepare`).
- Una escalada de privilegios en la máquina podría llegar a otorgar control sobre el bus

## Uso indebido previsible

- Conectar la interfaz del bus a otra red: aunque EtherCAT no es un protocolo con capacidad IP y su funcionamiento es complejo, es una tecnología de bus que no tiene autenticación, así que cualquiera con acceso a la red puede interaccionar ilimitadamente los dispositivos de esta.

- Dar a usuarios o servicios que no sean de confianza la pertenencia al grupo de la memoria compartida.

- Saturación por computación (scripts de visión pesados, cargas matemáticas complejas) de los scripts de interacción puede dar como resultado que comandos de control no lleguen nunca al bus por atasco en el script que les da origen.

- Interferencias y condiciones de carrera entre objetos de un mismo script al emplear sin las precaucioens adecuadas librerías que empleen sockets con polling, threads o que internamente ejecuten otros procesos de memoria compartida que compitan por el GIL.

- Trabajar con el sistema operativo sin espacio de almacenamiento disponible: puede dar lugar a un ralentizado general y saturación de los procesos de interrupción del sistema operativo causado por los reintentos de los ciclos de lectura/escritura a disco.


Ver también las notas de seguridad en [getting-started.md](getting-started.md#safety-notes).
