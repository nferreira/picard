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
    # The subset of `depends_on` that exists only in a development group (tests, tooling). It is
    # not what the package needs to run, so it never makes a dependency cycle, but it stays an
    # edge: a change to the dependency must still select the package whose tests use it.
    dev_depends_on: tuple[str, ...] = ()


def checked(found: list[Discovered]) -> list[Discovered]:
    """Reject unsafe names before they can reach a command line."""
    for item in found:
        if not NAME_OK.fullmatch(item.name):
            raise DiscoveryError(f"unsafe package name {item.name!r} at {item.path}")
    return found


def find_cycles(depends_on: Mapping[str, Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    """The cycles of a graph, each as the packages that depend on one another.

    One entry per strongly connected component of two or more packages, or of one package that
    depends on itself. Members follow the order of `depends_on`, and so do the entries, so the
    answer is stable. Iterative (Tarjan), so a long chain cannot exhaust the interpreter's stack.
    Every dependency must be a key of `depends_on`, as `DependencyGraph` also requires.
    """
    order = {name: position for position, name in enumerate(depends_on)}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    open_: list[str] = []
    on_stack: set[str] = set()
    found: list[tuple[str, ...]] = []

    for root in depends_on:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        open_.append(root)
        on_stack.add(root)
        work = [(root, iter(depends_on[root]))]
        while work:
            node, deps = work[-1]
            descended = False
            for dep in deps:
                if dep not in index:
                    index[dep] = low[dep] = len(index)
                    open_.append(dep)
                    on_stack.add(dep)
                    work.append((dep, iter(depends_on[dep])))
                    descended = True
                    break
                if dep in on_stack:
                    low[node] = min(low[node], index[dep])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                members: list[str] = []
                while True:
                    member = open_.pop()
                    on_stack.discard(member)
                    members.append(member)
                    if member == node:
                        break
                if len(members) > 1 or node in depends_on[node]:
                    found.append(tuple(sorted(members, key=order.__getitem__)))
    return tuple(sorted(found, key=lambda cycle: order[cycle[0]]))


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
