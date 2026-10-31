"""The shared part of the engines that ask a monorepo tool what is affected (nx, turbo, moon, bazel-diff).

    [impact]
    engine   = "nx"          # or turbo, moon, bazel-diff
    command  = "nx"          # the launcher, default the tool's name
    fallback = "native"      # who answers what the tool cannot (default "native")
    timeout  = 600           # seconds for each call to the tool
    debug    = false

Each tool has a more precise graph than picard's for the projects it knows, so picard asks it,
and only for those. The policy is the same for every tool and lives here, once:

- Files owned by a package the tool knows go to the tool; the rest go to the fallback engine.
- Uncommitted files (`--local`) are always also answered by the fallback: a tool that reads git
  refs cannot see them.
- The tool's answer is intersected with picard's packages, then the owners of the files it was
  given and every dependent from picard's graph are added. That can only widen the answer, and it
  covers a tool that returns only the projects that changed.
- A missing tool, a non-zero exit, a timeout or output of an unexpected shape hands the whole
  question to the fallback, with a note in the plan. Never a smaller answer.

A subclass says which packages the tool knows (`known`) and asks it (`query`). Both raise
RuntimeError (or let OSError, TimeoutExpired, ValueError, KeyError, TypeError escape) on
anything unexpected.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from picard.graph.impact import Impact, ImpactEngine, merge, owner

if TYPE_CHECKING:
    from picard.config import Config

FAILURES = (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError, KeyError, TypeError)


class Skip(Exception):  # noqa: N818 - not an error: the tool was not asked, and the note says why
    """The tool is not asked this question; the fallback answers it and `note` says why."""

    def __init__(self, note: str) -> None:
        super().__init__(note)
        self.note = note


class ToolImpactEngine(ImpactEngine):
    """An engine that asks an external monorepo tool, on the shared fail-closed policy."""

    label: ClassVar[str]  # how people write the tool's name, for warnings: "Nx", "Turborepo"
    default_command: ClassVar[str]
    default_timeout: ClassVar[float] = 600
    install: ClassVar[str]  # what `picard doctor --how-to-install` prints
    needs_revisions: ClassVar[bool] = False  # the tool reads git refs, not a list of files

    # --- options -------------------------------------------------------------------------

    def _argv(self) -> list[str]:
        command = self.options.get("command", self.default_command)
        return shlex.split(command) if isinstance(command, str) else [str(a) for a in command]

    def _timeout(self) -> float:
        value = self.options.get("timeout", self.default_timeout)
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise ValueError("[impact] timeout must be a positive number of seconds")
        return float(value)

    def _root(self, cfg: Config) -> Path:
        return Path(cfg.root) if cfg.root is not None else Path.cwd()

    @staticmethod
    def _found(launcher: str, cfg: Config) -> bool:
        if os.sep in launcher:
            path = Path(launcher)
            path = path if path.is_absolute() or cfg.root is None else Path(cfg.root) / path
            return path.is_file() and os.access(path, os.X_OK)
        return shutil.which(launcher) is not None

    def launchers(self) -> list[tuple[str, str]]:
        """(label, launcher) for each program the engine runs, checked by `problem`."""
        argv = self._argv()
        return [(self.label, argv[0])] if argv else []

    def problem(self, cfg: Config) -> str | None:
        if not self._argv():
            return f"[impact] command is empty, so there is no {self.label} launcher to run"
        try:
            self._timeout()
        except ValueError as exc:
            return str(exc)
        for label, launcher in self.launchers():
            if not self._found(launcher, cfg):
                return f"{label} is not installed: '{launcher}' was not found"
        return None

    def install_help(self) -> str:
        return self.install

    def _warn_once(self, problem: str, fallback: str) -> None:
        """Tell the user on stderr, once per run, however many tiers ask the same question."""
        memo = self.registries.memo
        if ("warned", self.name) not in memo:
            memo[("warned", self.name)] = True
            print(
                f"picard: warning: {problem}. Using the {fallback} engine instead. "
                "To see how to install it, run: picard doctor --how-to-install",
                file=sys.stderr,
            )

    def _fallback(self) -> ImpactEngine:
        engine = self.registries.engines.get(self.options.get("fallback", "native"))(
            {}, self.registries
        )
        engine.revisions = self.revisions  # a git-based fallback needs the commits too
        return engine

    # --- running the tool ----------------------------------------------------------------

    def run(
        self,
        cfg: Config,
        args: Sequence[str],
        *,
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        argv: Sequence[str] | None = None,
    ) -> str:
        """Run the tool (or `argv`) with `args` and return stdout. Raises on a non-zero exit."""
        command = [*(self._argv() if argv is None else argv), *args]
        result = subprocess.run(  # noqa: S603
            command,
            cwd=cwd or self._root(cfg),
            input=stdin,
            stdin=None if stdin is not None else subprocess.DEVNULL,
            env={**os.environ, **env} if env else None,
            capture_output=True,
            text=True,
            timeout=self._timeout(),
            check=False,
        )
        if result.returncode != 0:
            shown = " ".join(args[:4])
            raise RuntimeError(
                f"`{shown}` exited {result.returncode}: {result.stderr.strip()[:200]}"
            )
        return result.stdout

    def keep(self, cfg: Config, names: Sequence[str], what: str) -> set[str]:
        """The names that are picard packages. The others are only logged, in debug."""
        unknown = sorted(set(names) - set(cfg.packages))
        if unknown:
            self.log(f"{what}: not picard packages, ignored: {_short(unknown)}")
        return {n for n in names if n in cfg.packages}

    @abstractmethod
    def known(self, cfg: Config) -> set[str]:
        """The picard packages this tool knows: their files go to the tool."""

    @abstractmethod
    def query(self, cfg: Config, files: list[str]) -> set[str]:
        """The picard packages the tool says `files` affect."""

    # --- the shared policy ---------------------------------------------------------------

    def compute(self, cfg: Config, files: list[str]) -> Impact:
        fallback = self._fallback()
        if (problem := self.problem(cfg)) is not None:
            self.log(problem)
            self._warn_once(problem, fallback.name)
            return _answered_by(
                fallback.compute(cfg, files), f"{problem}; the {fallback.name} engine answered"
            )
        if self.needs_revisions and self.revisions is None:
            note = f"{self.name} answers from git refs only: the {fallback.name} engine answered"
            self.log(note)
            return _answered_by(fallback.compute(cfg, files), note)
        try:
            known = self.known(cfg)
            owned = [f for f in files if owner(cfg, f) in known]
            answer = self.query(cfg, owned) if owned else set()
        except Skip as skip:
            self.log(skip.note)
            return _answered_by(fallback.compute(cfg, files), skip.note)
        except FAILURES as exc:
            self.log(f"{self.name} failed: {exc}")
            return _answered_by(
                fallback.compute(cfg, files),
                f"{self.name} failed ({exc}); the {fallback.name} engine answered",
            )

        self.log(f"files given ({len(files)}): {_short(files)}")
        self.log(f"{self.name} knows {len(known)} package(s): {_short(sorted(known))}")
        self.log(f"{self.name} answered for {len(owned)} file(s): {_short(sorted(answer))}")
        changed = {p for f in owned if (p := owner(cfg, f))}
        graph = cfg.dependency_graph()
        # The tool says *that* a package is affected; picard's graph adds every dependent (it
        # can only widen) and says *through whom* where it can.
        through = graph.with_dependents(changed)
        reached = graph.with_dependents(changed | answer)
        chains: dict[str, tuple[str, ...]] = {}
        for name in cfg.packages:
            if name in through:
                chains[name] = through[name]
            elif name in answer:
                chains[name] = (name, self.name)
            elif name in reached:
                chains[name] = (*reached[name], self.name)
        result = Impact(
            tuple(n for n in cfg.packages if n in changed),
            chains,
            False,
            (),
            (),
            dict.fromkeys(chains, self.name),
        )

        uncommitted = set(self.revisions.uncommitted) if self.revisions else set()
        owned_set = set(owned)
        rest = [f for f in files if f not in owned_set]
        dirty = [f for f in owned if f in uncommitted]
        if rest or dirty:
            other = fallback.compute(cfg, rest + dirty)
            notes = list(other.notes)
            if rest:
                notes.append(
                    f"{len(rest)} file(s) are not owned by {self.name}: "
                    f"answered by the {fallback.name} engine"
                )
            if dirty:
                notes.append(
                    f"{len(dirty)} uncommitted file(s): also answered by the {fallback.name} engine"
                )
            result = merge(cfg, result, _with_notes(other, tuple(notes)))
        if self.debug:
            self._compare(cfg, files, result)
        return result

    def _compare(self, cfg: Config, files: list[str], result: Impact) -> None:
        """Log this engine's packages next to what the native engine says for the same files."""
        native = self.registries.engines.get("native")({}, self.registries).compute(cfg, files)
        self.log(f"result ({self.name} + fallback): affected={list(result.affected)}")
        self.log(f"native engine: affected={list(native.affected)}")
        only_tool = sorted(set(result.affected) - set(native.affected))
        only_native = sorted(set(native.affected) - set(result.affected))
        if only_tool or only_native:
            self.log(f"DIFFERENCE only in {self.name}: {only_tool}; only in native: {only_native}")
        else:
            self.log("same packages from both engines")


def _with_notes(base: Impact, notes: tuple[str, ...]) -> Impact:
    return Impact(
        base.changed,
        base.affected,
        base.everything,
        base.global_files,
        notes,
        base.decided_by,
        base.test_only,
    )


def _answered_by(base: Impact, note: str) -> Impact:
    """The fallback's answer, with a note saying why it was asked."""
    return _with_notes(base, (*base.notes, note))


def _short(items: Sequence[str], limit: int = 20) -> str:
    more = f" ... (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more
