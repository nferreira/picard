"""The vocabulary of the dependency graph: what a provider returns and how the graph is walked."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# Names end up in shell commands ({package}) and job names, and come from files a PR can edit.
NAME_OK = re.compile(r"^[@A-Za-z0-9._/-]+$")


class DiscoveryError(ValueError):
    """A manifest could not be read, or describes something unusable."""


@dataclass(frozen=True)
class Discovered:
    """One package a provider found."""

    name: str
    path: str
    depends_on: tuple[str, ...]
    provider: str


def checked(found: list[Discovered]) -> list[Discovered]:
    """Reject unsafe names before they can reach a command line."""
    for item in found:
        if not NAME_OK.fullmatch(item.name):
            raise DiscoveryError(f"unsafe package name {item.name!r} at {item.path}")
    return found


class DependencyGraph:
    """Packages and the edges between them, with the one walk every engine needs.

    An edge `a -> b` means "a depends on b". The graph knows nothing about languages, files or
    git: it is built from a mapping of name to dependency names.
    """

    def __init__(self, depends_on: Mapping[str, Sequence[str]]) -> None:
        self._depends_on = {name: tuple(deps) for name, deps in depends_on.items()}
        self._dependents: dict[str, list[str]] = {name: [] for name in self._depends_on}
        for name, deps in self._depends_on.items():
            for dep in deps:
                self._dependents[dep].append(name)

    def __contains__(self, name: object) -> bool:
        return name in self._depends_on

    def names(self) -> tuple[str, ...]:
        return tuple(self._depends_on)

    def dependencies(self, name: str) -> tuple[str, ...]:
        return self._depends_on[name]

    def dependents(self, name: str) -> tuple[str, ...]:
        return tuple(self._dependents[name])

    def with_dependents(self, changed: Iterable[str]) -> dict[str, tuple[str, ...]]:
        """`changed` plus everything that depends on it, transitively.

        Maps each package to its chain, read from the package down to the change, for example
        some-api -> component-def -> component-abc. Breadth first, so the chain is the shortest.
        """
        chains: dict[str, tuple[str, ...]] = {name: (name,) for name in changed}
        queue = deque(chains)
        while queue:
            current = queue.popleft()
            for dependent in self._dependents[current]:
                if dependent not in chains:
                    chains[dependent] = (dependent, *chains[current])
                    queue.append(dependent)
        return {name: chains[name] for name in self._depends_on if name in chains}
