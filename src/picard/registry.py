"""Named, pluggable implementations.

Everything picard can be extended with (graph providers, impact engines, executors, rule
actions) is looked up by name in a Registry. Built-ins and plugins register the same way, so
adding a capability never means editing the code that uses it (open for extension, closed for
modification). A Registries object is built per run and passed down: there is no global state.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class UnknownPluginError(KeyError):
    """A configuration names something nothing has registered."""

    def __init__(self, kind: str, name: str, known: tuple[str, ...]) -> None:
        self.kind, self.name, self.known = kind, name, known
        super().__init__(f"unknown {kind} '{name}', known: {', '.join(known) or '(none)'}")

    def __str__(self) -> str:
        return self.args[0]


class Registry(Generic[T]):
    """name -> implementation, for one kind of plugin."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T, *, replace: bool = False) -> None:
        if name in self._items and not replace:
            raise ValueError(f"{self.kind} '{name}' is already registered")
        self._items[name] = item

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            raise UnknownPluginError(self.kind, name, self.names()) from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())


@dataclass
class Registries:
    """Everything pluggable, in one place, handed to whoever needs to look something up."""

    providers: Registry[Any] = field(default_factory=lambda: Registry("graph provider"))
    engines: Registry[Any] = field(default_factory=lambda: Registry("impact engine"))
    executors: Registry[Any] = field(default_factory=lambda: Registry("executor"))
    actions: Registry[Any] = field(default_factory=lambda: Registry("rule action"))
    # Results worth computing once per run, e.g. an impact engine that shells out to Pants.
    memo: dict[Any, Any] = field(default_factory=dict)
