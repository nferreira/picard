"""Impact: what a set of changed files affects, and the interface of the engines that compute it."""

from __future__ import annotations

import os
import posixpath
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from picard import gitutil, lockfiles
from picard.globs import matches

if TYPE_CHECKING:
    from picard.config import Config, Package
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
    # The root "." is the shallowest owner, not a one-character path that ties with "b".
    return max(owners, key=lambda p: 0 if p.path == "." else len(p.path)).name if owners else None


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
        narrowed = [f for f in files if matches(f, cfg.narrow_lockfiles)]
        listed = set(narrowed)
        rest = [f for f in files if f not in listed]
        # A lockfile picard recognises that is not narrowed is global too, whether or not
        # [paths] global lists it: a dependency bump must not plan nothing.
        global_files = tuple(
            f for f in rest if matches(f, cfg.global_globs) or lockfiles.is_lockfile(f)
        )
        if global_files:
            decided = dict.fromkeys(cfg.packages, "global file")
            result = Impact((), {n: (n,) for n in cfg.packages}, True, global_files, (), decided)
        elif rest:
            result = self.compute(cfg, rest)
        else:
            result = Impact((), {}, False, ())
        if narrowed:
            result = merge(cfg, result, self.lockfile_impact(cfg, narrowed))
        return result

    @abstractmethod
    def compute(self, cfg: Config, files: list[str]) -> Impact:
        """Changed packages plus their dependents, for files that are not global."""

    def lockfile_impact(self, cfg: Config, lockfiles: list[str]) -> Impact:
        """What the changed `[lockfiles] narrow` files affect.

        Each lockfile is diffed between the base and the head, and the workspace members its
        changes reach become changed packages. Fails closed: a lockfile whose change cannot be
        attributed to picard packages, for any reason, affects every package and says why.
        """
        result = Impact((), {}, False, ())
        for path in lockfiles:
            result = merge(cfg, result, self._one_lockfile(cfg, path))
        return result

    def _one_lockfile(self, cfg: Config, path: str) -> Impact:
        try:
            return _attribute(cfg, path, self.revisions)
        except _Unattributed as exc:
            reason = str(exc)
        except (gitutil.GitError, lockfiles.LockfileError) as exc:
            reason = str(exc)
        note = f"{path} changed and cannot be attributed to packages ({reason}): running everything"
        decided = dict.fromkeys(cfg.packages, "lockfile")
        return Impact((), {n: (n,) for n in cfg.packages}, True, (path,), (note,), decided)

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
        test_only = [n for n in changed if _only_tests(cfg.packages[n], files)]
        walked = cfg.dependency_graph().with_dependents(n for n in changed if n not in test_only)
        # A test-only change affects its package and not, through it, the package's dependents.
        # It is still reached when another changed package leads to it.
        affected = {n: walked.get(n, (n,)) for n in cfg.packages if n in walked or n in test_only}
        decided = dict.fromkeys(affected, self.name)
        return Impact(
            tuple(n for n in cfg.packages if n in changed),
            affected,
            False,
            (),
            (),
            decided,
            tuple(test_only),
        )


def _only_tests(package: Package, files: list[str]) -> bool:
    """Every changed file `package` owns matches its `test_paths`, relative to its path.

    Narrows, so it fails closed: no `test_paths`, or a glob that cannot be compiled, means the
    change is not test-only and the dependents are walked as usual.
    """
    if not package.test_paths:
        return False
    own = [f for f in files if owns(f, package.path)]
    prefix = "" if package.path == "." else package.path + "/"
    try:
        return bool(own) and all(
            f.startswith(prefix) and matches(f[len(prefix) :], package.test_paths) for f in own
        )
    except ValueError:
        return False


class _Unattributed(Exception):
    """A lockfile change picard cannot attribute to packages; the message says why."""


def _read_lockfile(cfg: Config, path: str, revisions: Revisions) -> tuple[str, str]:
    """The old (base) and new (head, or working tree under --local) text of a lockfile."""
    old = gitutil.show_file(revisions.base, path, cwd=cfg.root)
    if path in revisions.uncommitted:
        try:
            new: str | None = ((cfg.root or Path.cwd()) / path).read_text(encoding="utf-8")
        except FileNotFoundError:
            new = None
        except (OSError, UnicodeDecodeError) as exc:
            raise _Unattributed(f"the working-tree file cannot be read: {exc}") from exc
    else:
        new = gitutil.show_file(revisions.head, path, cwd=cfg.root)
    if old is None:
        raise _Unattributed(f"it is not in the base {revisions.base[:12]}")
    if new is None:
        raise _Unattributed("it was deleted")
    return old, new


def _member_package(cfg: Config, kind: str, path: str, key: str) -> str | None:
    """The picard package a lockfile member key names: uv and cargo by name, npm by path.

    A uv or cargo name counts only for a package that provider discovered: a package of the
    same name from another provider, or listed by hand, is a different thing and maps to nothing.
    """
    if kind == "npm":
        where = posixpath.normpath(posixpath.join(posixpath.dirname(path) or ".", key or "."))
        return next((p.name for p in cfg.packages.values() if p.path == where), None)
    if key == lockfiles.UV_ROOT:
        return None
    package = cfg.packages.get(key)
    return key if package is not None and package.provider == kind else None


def _versions(change: lockfiles.LockChange) -> str:
    listed = [
        f"{name} {old or 'none'} -> {new or 'none'}" for name, (old, new) in change.changed.items()
    ]
    if len(listed) > 3:
        listed = [*listed[:3], f"and {len(listed) - 3} more"]
    return ", ".join(listed) or "the entries of " + ", ".join(change.members)


def _attribute(cfg: Config, path: str, revisions: Revisions | None) -> Impact:
    """The packages one lockfile change reaches. Raises when it cannot be attributed."""
    basename = posixpath.basename(path)
    if basename not in lockfiles.SUPPORTED:
        raise _Unattributed(f"picard cannot parse '{basename}'")
    if revisions is None:
        raise _Unattributed("lockfile narrowing needs --base or --local")
    change = lockfiles.diff(path, *_read_lockfile(cfg, path, revisions))
    if change.text_only:
        raise _Unattributed("no resolved version, source or dependency changed")
    if change.orphans:
        raise _Unattributed(f"{', '.join(change.orphans)} reaches no workspace member")
    hits = []
    for key in change.members:
        name = _member_package(cfg, change.kind, path, key)
        if name is None:
            member = "the workspace root" if key in ("", lockfiles.UV_ROOT) else f"'{key}'"
            raise _Unattributed(f"{member} is not a picard package")
        hits.append(name)
    if not hits:
        raise _Unattributed("the change reaches no package")
    walked = cfg.dependency_graph().with_dependents(hits)
    affected = {n: walked[n] for n in cfg.packages if n in walked}
    note = (
        f"{path} changed {_versions(change)}: reaches {len(affected)} of {len(cfg.packages)} "
        f"packages ({', '.join(affected)})"
    )
    return Impact(
        tuple(n for n in cfg.packages if n in hits),
        affected,
        False,
        (),
        (note,),
        dict.fromkeys(affected, "lockfile"),
    )
