# API reference

Generated from the docstrings. [python-api.md](python-api.md) explains how
the pieces fit together; the [device catalogue](generated/device-catalogue.md)
lists every device class with its variables.

## Bus and devices

```{eval-rst}
.. autoclass:: odycat.bus.Bus
   :members:
```

```{eval-rst}
.. automodule:: odycat.device
   :members: Device, DeviceLostError, ObjectNotMappedError
```

## Events

```{eval-rst}
.. automodule:: odycat.events
   :members:
```

## Device variables

```{eval-rst}
.. automodule:: odycat.signal
   :members: signal, Signal
```

## Trace

```{eval-rst}
.. automodule:: odycat.trace
   :members:
```

## Bus monitor

```{eval-rst}
.. automodule:: odycat.monitor
   :members:
```

## Standard profiles

```{eval-rst}
.. autoclass:: odycat.devices.cia402.Servo
   :members:
```

```{eval-rst}
.. autoclass:: odycat.devices.mdp.DigitalIO
   :members:
```

```{eval-rst}
.. autoclass:: odycat.devices.cia401.GenericIO
   :members:
```

## Testing without hardware

```{eval-rst}
.. automodule:: odycat.testing
   :members: fake_engine, FakeEngine, mini_config, device_slave, word_slave, bit_slave
```

