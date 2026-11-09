# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
# odycat/signal.py
"""
Signals — a device's variables, by name.

A device class declares each variable it exposes with @signal: the object
it is read from (index and subindex, as in the vendor's manual) and,
optionally, one bit of it. The decorator supplies the body::

    class EC3A_IO1632(Device):
        @signal(0x6000, 1, bit=3)
        def input_3(self) -> bool:
            \"\"\"Digital input 3.\"\"\"
            ...

On a device object the name then has two uses, as in Qt (text() /
setText()): with () it is the value now, without () it is the variable
itself, which odycat.Events watches::

    io.in03()                # True or False, now
    @ev.rising(io.in03)      # call a function when it goes true

Writing is an action and keeps its verb (io.set_out03(True)).

A variable is not a value: `if io.in03:` or `io.in03 == True` raise
TypeError instead of being always true / always false, which is what a
forgotten () would otherwise silently do.

Objects are found by index, so signals work whatever the entries are called
in the config. For multi-axis CiA 402 drives the index is the axis 1 one;
the device's axis offset is added when it is read.
"""

from __future__ import annotations

from typing import Any, Callable, Generic, NoReturn, Optional, TypeVar, overload

_R = TypeVar("_R")
_R2 = TypeVar("_R2")


class SignalSpec(Generic[_R]):
    """What a signal reads: object, bit, conversion. Shared by all devices
    of a class; Signal binds it to one device."""

    def __init__(self, index: int, subindex: int, bit: Optional[int],
                 convert: Optional[Callable[[Any], Any]]):
        self.index = index
        self.subindex = subindex
        self.bit = bit
        self.convert = convert
        self.name = ""
        self.__doc__ = None

    def __call__(self, func: Callable[[Any], _R2]) -> "SignalSpec[_R2]":
        self.name = func.__name__
        self.__doc__ = func.__doc__
        self.__wrapped__ = func
        return self  # type: ignore[return-value]

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    @overload
    def __get__(self, obj: None, owner: Optional[type] = None) -> "SignalSpec[_R]": ...
    @overload
    def __get__(self, obj: object, owner: Optional[type] = None) -> "Signal[_R]": ...

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return Signal(obj, self)

    def value_from(self, raw: Any) -> Any:
        """The signal's value from its object's raw value."""
        if self.bit is not None:
            raw = bool((int(raw) >> self.bit) & 1)
        return self.convert(raw) if self.convert is not None else raw

    def describe(self) -> str:
        bit = f" bit {self.bit}" if self.bit is not None else ""
        return f"0x{self.index:04X}:{self.subindex:02X}{bit}"

    def __repr__(self) -> str:
        return f"<signal {self.name}: {self.describe()}>"


def signal(index: int, subindex: int = 0, *, bit: Optional[int] = None,
           convert: Optional[Callable[[Any], Any]] = None) -> SignalSpec[Any]:
    """Declare a device variable: object index/subindex, optionally one bit
    (the value is then a bool) and a conversion applied to the raw value."""
    return SignalSpec(index, subindex, bit, convert)


def _not_a_value(sig: "Signal", what: str) -> NoReturn:
    raise TypeError(f"{sig.name} (slave '{sig.label}') is the variable itself, not "
                    f"its value: call it, {sig.name}(), to {what}")


class Signal(Generic[_R]):
    """A device variable: call it for its value, pass it to Events to watch
    it. Refuses to be used as a value (see the module docstring)."""

    __slots__ = ("device", "spec")

    def __init__(self, device: Any, spec: SignalSpec[_R]):
        self.device = device
        self.spec = spec

    def __call__(self) -> _R:
        offset = getattr(self.device, "_axis_offset", 0)
        raw = self.device._read_object(self.spec.index + offset, self.spec.subindex)
        return self.spec.value_from(raw)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def label(self) -> str:
        """The slave's label in the config."""
        return getattr(self.device, "_slave_label", None) or type(self.device).__name__

    @property
    def qualname(self) -> str:
        """Slave label and name: io_module.in03."""
        return f"{self.label}.{self.spec.name}"

    @property
    def index(self) -> int:
        """Absolute object index (axis offset included)."""
        return self.spec.index + getattr(self.device, "_axis_offset", 0)

    @property
    def subindex(self) -> int:
        return self.spec.subindex

    @property
    def bit(self) -> Optional[int]:
        return self.spec.bit

    # -- the guard: a variable is not a value --------------------------------

    def __bool__(self) -> NoReturn:      # editors grey out `if io.in03:` code
        _not_a_value(self, "test it")

    def __eq__(self, other) -> bool:
        if isinstance(other, Signal):
            return self.device is other.device and self.spec is other.spec
        _not_a_value(self, "compare it")

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash((id(self.device), id(self.spec)))

    def __lt__(self, other):
        _not_a_value(self, "compare it")

    __le__ = __gt__ = __ge__ = __lt__

    def __int__(self):
        _not_a_value(self, "use it as a number")

    __float__ = __index__ = __int__

    def __repr__(self) -> str:
        return f"<signal {self.qualname}: {self.spec.describe()}>"


__all__ = ["signal", "Signal", "SignalSpec"]
