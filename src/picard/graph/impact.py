"""Impact: what a set of changed files affects, and the interface of the engines that compute it."""

from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from picard.globs import matches

if TYPE_CHECKING:
    from picard.config import Config
    from picard.registry import Registries


@dataclass(frozen=True)
class Impact:
    """What a set of changed files touches in the dependency graph."""

    changed: tuple[str, ...]  # packages that own a changed file
    affected: dict[str, tuple[str, ...]]  # package -> chain down to a changed package
    everything: bool  # a global file (lockfile, config) changed
    global_files: tuple[str, ...]
    notes: tuple[str, ...] = ()  # something worth telling the user, e.g. an engine fell back
    # package -> who put it in: an engine name, "rule" or "global file" (shown as `via ...`)
    decided_by: dict[str, str] = field(default_factory=dict)

    def chain(self, package: str) -> str:
        return " -> ".join(self.affected[package])


def owns(path: str, package_path: str) -> bool:
    return package_path == "." or path == package_path or path.startswith(package_path + "/")


def owner(cfg: Config, path: str) -> str | None:
    """The package that owns `path`: the deepest package directory containing it."""
    owners = [p for p in cfg.packages.values() if owns(path, p.path)]
    return max(owners, key=lambda p: len(p.path)).name if owners else None


def everything(cfg: Config, why: Iterable[str] = ()) -> Impact:
    """Every package is affected, for a reason that is not a global file (a rule said `full`)."""
    return Impact(
        (),
        {n: (n,) for n in cfg.packages},
        True,
        tuple(why),
        (),
        dict.fromkeys(cfg.packages, "rule"),
    )


class SetupError(RuntimeError):
    """An engine could not be set up, or its setup is out of date (`picard setup --check`)."""


class ImpactEngine(ABC):
    """Decides which packages a set of changed files affects.

    Template method: `impact` applies the policy every engine shares (a global file such as a
    lockfile affects everything), then asks the subclass to do the engine-specific part in
    `compute`. An engine never decides what to run, only what is affected.
    """

    name: ClassVar[str]

    def __init__(self, options: Mapping[str, Any] | None, registries: Registries) -> None:
        self.options: dict[str, Any] = dict(options or {})
        self.registries = registries

    @property
    def debug(self) -> bool:
        """`debug = true` in [impact], or PICARD_DEBUG=1 in the environment."""
        return bool(self.options.get("debug")) or os.environ.get("PICARD_DEBUG") == "1"

    def log(self, message: str) -> None:
        """Debug trace on stderr, so the JSON a run prints on stdout stays clean."""
        if self.debug:
            print(f"[picard:debug:{self.name}] {message}", file=sys.stderr)

    def impact(self, cfg: Config, files: Sequence[str]) -> Impact:
        files = list(files)
        global_files = tuple(f for f in files if matches(f, cfg.global_globs))
        if global_files:
            decided = dict.fromkeys(cfg.packages, "global file")
            return Impact((), {n: (n,) for n in cfg.packages}, True, global_files, (), decided)
        return self.compute(cfg, files)

    @abstractmethod
    def compute(self, cfg: Config, files: list[str]) -> Impact:
        """Changed packages plus their dependents, for files that are not global."""

    def problem(self, cfg: Config) -> str | None:
        """What stops this engine from working here (a missing tool), or None when it is ready."""
        return None

    def install_help(self) -> str:
        """How to fix what `problem` reports. Shown only when the user asks (`picard doctor`)."""
        return f"The '{self.name}' engine needs nothing installed."

    def setup(
        self, cfg: Config, *, check: bool = False, say: Callable[[str], None] = print
    ) -> None:
        """Prepare the repository for this engine (`picard setup`), or with `check` only verify it.

        Most engines need nothing. One that does overrides this, writes its own files and raises
        SetupError when something is missing or out of date.
        """
        say(f"the '{self.name}' engine needs no setup")


class NativeImpactEngine(ImpactEngine):
    """Walks the dependency graph the providers discovered. No external tool, no setup."""

    name = "native"

    def compute(self, cfg: Config, files: list[str]) -> Impact:
        changed = [p.name for p in cfg.packages.values() if any(owns(f, p.path) for f in files)]
        affected = cfg.dependency_graph().with_dependents(changed)
        decided = dict.fromkeys(affected, self.name)
        return Impact(
            tuple(n for n in cfg.packages if n in changed), affected, False, (), (), decided
        )
