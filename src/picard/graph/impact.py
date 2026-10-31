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
class Revisions:
    """The commits a change lies between, when picard knows them (--base, --local)."""

    base: str  # the merge-base commit (full sha) the diff starts from
    head: str  # the head the diff ends at, as given (default "HEAD")
    uncommitted: tuple[str, ...] = ()  # working-tree files that are also in the change (--local)


@dataclass(frozen=True)
class Impact:
    """What a set of changed files touches in the dependency graph."""

    changed: tuple[str, ...]  # packages that own a changed file
    affected: dict[str, tuple[str, ...]]  # package -> chain down to a changed package
    everything: bool  # a global file (lockfile, config) changed
    global_files: tuple[str, ...]
    notes: tuple[str, ...] = ()  # something worth telling the user, e.g. an engine fell back
    # package -> who put it in: an engine name, "rule", "global file", "lockfile", "always" or
    # "forced" (shown as `via ...`)
    decided_by: dict[str, str] = field(default_factory=dict)
    test_only: tuple[str, ...] = ()  # changed packages whose changed files are all test files

    def chain(self, package: str) -> str:
        return " -> ".join(self.affected[package])


def owns(path: str, package_path: str) -> bool:
    return package_path == "." or path == package_path or path.startswith(package_path + "/")


def owner(cfg: Config, path: str) -> str | None:
    """The package that owns `path`: the deepest package directory containing it."""
    owners = [p for p in cfg.packages.values() if owns(path, p.path)]
    return max(owners, key=lambda p: len(p.path)).name if owners else None


def everything(cfg: Config, why: Iterable[str] = (), by: str = "rule") -> Impact:
    """Every package is affected, for a reason that is not a global file (a rule said `full`)."""
    return Impact(
        (),
        {n: (n,) for n in cfg.packages},
        True,
        tuple(why),
        (),
        dict.fromkeys(cfg.packages, by),
    )


def merge(cfg: Config, first: Impact, second: Impact) -> Impact:
    """The union of two answers. `first` keeps its chains and labels where both name a package."""
    changed = {*first.changed, *second.changed}
    affected = {**second.affected, **first.affected}
    decided = {**second.decided_by, **first.decided_by}
    test_only = {*first.test_only, *second.test_only}
    return Impact(
        tuple(n for n in cfg.packages if n in changed),
        {n: affected[n] for n in cfg.packages if n in affected},
        first.everything or second.everything,
        tuple(dict.fromkeys((*first.global_files, *second.global_files))),
        (*first.notes, *second.notes),
        {n: decided[n] for n in cfg.packages if n in decided},
        tuple(n for n in cfg.packages if n in test_only),
    )


class SetupError(RuntimeError):
    """An engine could not be set up, or its setup is out of date (`picard setup --check`)."""


class ImpactEngine(ABC):
    """Decides which packages a set of changed files affects.

    Template method: `impact` applies the policy every engine shares (a global file such as a
    lockfile affects everything, a lockfile listed under `[lockfiles] narrow` is attributed by
    `lockfile_impact`), then asks the subclass to do the engine-specific part in `compute`. An
    engine never decides what to run, only what is affected.

    `self.revisions` holds the commits the change lies between while `impact` runs, or None
    when the change has no git range (`--files`, `--staged`, `--working`). An engine that needs
    the commits reads it; with None it answers from the files alone or hands over to its
    fallback and says so in a note. See ADR 0007.
    """

    name: ClassVar[str]

    def __init__(self, options: Mapping[str, Any] | None, registries: Registries) -> None:
        self.options: dict[str, Any] = dict(options or {})
        self.registries = registries
        self.revisions: Revisions | None = None

    @property
    def debug(self) -> bool:
        """`debug = true` in [impact], or PICARD_DEBUG=1 in the environment."""
        return bool(self.options.get("debug")) or os.environ.get("PICARD_DEBUG") == "1"

    def log(self, message: str) -> None:
        """Debug trace on stderr, so the JSON a run prints on stdout stays clean."""
        if self.debug:
            print(f"[picard:debug:{self.name}] {message}", file=sys.stderr)

    def impact(
        self, cfg: Config, files: Sequence[str], revisions: Revisions | None = None
    ) -> Impact:
        self.revisions = revisions
        # A listed lockfile is attributed to packages, even when it also matches [paths] global:
        # listing it under [lockfiles] narrow is the opt-in.
        lockfiles = [f for f in files if matches(f, cfg.narrow_lockfiles)]
        listed = set(lockfiles)
        rest = [f for f in files if f not in listed]
        global_files = tuple(f for f in rest if matches(f, cfg.global_globs))
        if global_files:
            decided = dict.fromkeys(cfg.packages, "global file")
            result = Impact((), {n: (n,) for n in cfg.packages}, True, global_files, (), decided)
        elif rest:
            result = self.compute(cfg, rest)
        else:
            result = Impact((), {}, False, ())
        if lockfiles:
            result = merge(cfg, result, self.lockfile_impact(cfg, lockfiles))
        return result

    @abstractmethod
    def compute(self, cfg: Config, files: list[str]) -> Impact:
        """Changed packages plus their dependents, for files that are not global."""

    def lockfile_impact(self, cfg: Config, lockfiles: list[str]) -> Impact:
        """What the changed `[lockfiles] narrow` files affect.

        Fails closed: until a change can be attributed to packages, every package is affected.
        """
        notes = tuple(
            f"{path} changed and cannot be attributed to packages yet: running everything"
            for path in lockfiles
        )
        decided = dict.fromkeys(cfg.packages, "lockfile")
        return Impact((), {n: (n,) for n in cfg.packages}, True, tuple(lockfiles), notes, decided)

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
