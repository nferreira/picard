"""Use Pants for what it is best at, import-level impact for the languages it understands.

    [impact]
    engine   = "pants"
    command  = "pants"        # the launcher, default "pants"
    fallback = "native"       # who answers for files Pants does not own (default "native")

Pants is only an engine here: you do not have to adopt it for the whole repository, and nothing
else in picard changes. The changed files go to Pants through --spec-files (never the command
line). `pants list` says which files it owns and `pants dependents --transitive` what depends on
them. Files Pants does not own (JavaScript, lockfiles, configs) are answered by the fallback
engine, so Pants narrowing the answer can never lose a package it simply does not understand.
If Pants fails, the fallback answers the whole question and says so.

`debug = true` (or PICARD_DEBUG=1) prints, on stderr, what Pants returned, what the native
engine returns for the same files and where they differ.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from picard.graph.engines.pants_setup import PantsWorkspace, python_packages
from picard.graph.impact import Impact, ImpactEngine, SetupError, owner

if TYPE_CHECKING:
    from picard.config import Config

TIMEOUT_SECONDS = 600

INSTALL_HELP = """\
Pants is not installed (or not on your PATH). Install it with one of:

  macOS (Homebrew)   brew install pantsbuild/tap/pants
  macOS or Linux     curl --proto '=https' --tlsv1.2 -fsSL https://static.pantsbuild.org/setup/get-pants.sh | bash
                     (installs ~/.local/bin/pants: make sure that directory is on your PATH)
  Other ways         https://www.pantsbuild.org/stable/docs/getting-started/installing-pants

Then run `picard setup` to prepare the repository and `picard setup --check` to verify it.
The first Pants run downloads Pants itself, so it can take a minute without printing anything.
In CI, install a pinned release rather than piping a script to a shell.

Pants is optional. Without it picard answers with the native engine, which needs nothing
installed. To stop looking for Pants, set `engine = "native"` under `[impact]`."""


class PantsImpactEngine(ImpactEngine):
    name = "pants"

    def _argv(self) -> list[str]:
        command = self.options.get("command", "pants")
        return shlex.split(command) if isinstance(command, str) else list(command)

    def problem(self, cfg: Config) -> str | None:
        argv = self._argv()
        if not argv:
            return "[impact] command is empty, so there is no Pants launcher to run"
        launcher = argv[0]
        if os.sep in launcher:
            path = Path(launcher)
            path = path if path.is_absolute() or cfg.root is None else cfg.root / path
            found = path.is_file() and os.access(path, os.X_OK)
        else:
            found = shutil.which(launcher) is not None
        return None if found else f"Pants is not installed: '{launcher}' was not found"

    def install_help(self) -> str:
        return INSTALL_HELP

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

    def setup(
        self, cfg: Config, *, check: bool = False, say: Callable[[str], None] = print
    ) -> None:
        if (problem := self.problem(cfg)) is not None:
            raise SetupError(
                f"{problem}. To see how to install it, run: picard doctor --how-to-install"
            )
        workspace = PantsWorkspace(cfg.root, self.options, python_packages(cfg), say)
        workspace.check() if check else workspace.setup()

    def _fallback(self) -> ImpactEngine:
        return self.registries.engines.get(self.options.get("fallback", "native"))(
            {}, self.registries
        )

    def _run(self, argv: list[str], spec_file: str, goal: list[str], cwd: Path | None) -> list[str]:
        result = subprocess.run(  # noqa: S603
            [*argv, f"--spec-files={spec_file}", "--unmatched-cli-globs=ignore", *goal],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`{' '.join(goal)}` exited {result.returncode}: {result.stderr.strip()[:200]}"
            )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def compute(self, cfg: Config, files: list[str]) -> Impact:
        fallback = self._fallback()
        if (problem := self.problem(cfg)) is not None:
            self.log(problem)
            self._warn_once(problem, fallback.name)
            return _answered_by(
                fallback.compute(cfg, files), f"{problem}; the {fallback.name} engine answered"
            )
        argv = self._argv()
        with tempfile.NamedTemporaryFile(
            "w", suffix=".specs", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("\n".join(files))
            spec_file = handle.name
        try:
            owned_paths = {a.split(":")[0] for a in self._run(argv, spec_file, ["list"], cfg.root)}
            dependents = self._run(argv, spec_file, ["dependents", "--transitive"], cfg.root)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            self.log(f"pants failed: {exc}")
            return _answered_by(
                fallback.compute(cfg, files),
                f"pants failed ({exc}); the {fallback.name} engine answered",
            )
        finally:
            Path(spec_file).unlink(missing_ok=True)

        self.log(f"files given ({len(files)}): {_short(files)}")
        self.log(f"pants list -> {len(owned_paths)} owned file(s): {_short(sorted(owned_paths))}")
        self.log(f"pants dependents --transitive -> {len(dependents)}: {_short(dependents)}")
        handled = [f for f in files if f in owned_paths]
        rest = [f for f in files if f not in owned_paths]
        changed = {p for f in handled if (p := owner(cfg, f))}
        affected = set(changed)
        affected.update(p for a in dependents if (p := owner(cfg, a.split(":")[0])))
        # Pants says *that* a package is affected; the graph says *through whom* (best effort).
        through = cfg.dependency_graph().with_dependents(changed)
        chains = {
            n: (n,) if n in changed else through.get(n, (n, "pants"))
            for n in cfg.packages
            if n in affected
        }
        decided = dict.fromkeys(chains, self.name)
        notes: list[str] = []
        if rest:
            other = fallback.compute(cfg, rest)
            changed |= set(other.changed)
            for name, chain in other.affected.items():
                chains.setdefault(name, chain)
                decided.setdefault(name, other.decided_by.get(name, fallback.name))
            notes += [
                *other.notes,
                f"{len(rest)} file(s) are not owned by pants: answered by the {fallback.name} engine",
            ]
        ordered = {n: chains[n] for n in cfg.packages if n in chains}
        result = Impact(
            tuple(n for n in cfg.packages if n in changed),
            ordered,
            False,
            (),
            tuple(notes),
            {n: decided[n] for n in ordered},
        )
        if self.debug:
            self._compare(cfg, files, result)
        return result

    def _compare(self, cfg: Config, files: list[str], result: Impact) -> None:
        """Log this engine's packages next to what the native engine says for the same files."""
        native = self.registries.engines.get("native")({}, self.registries).compute(cfg, files)
        self.log(
            f"result (pants + fallback): changed={list(result.changed)} "
            f"affected={list(result.affected)}"
        )
        self.log(
            f"native engine:             changed={list(native.changed)} "
            f"affected={list(native.affected)}"
        )
        only_pants = sorted(set(result.affected) - set(native.affected))
        only_native = sorted(set(native.affected) - set(result.affected))
        if only_pants or only_native:
            self.log(f"DIFFERENCE only in pants: {only_pants}; only in native: {only_native}")
        else:
            self.log("same packages from both engines")


def _answered_by(base: Impact, note: str) -> Impact:
    """The fallback's answer, with a note saying why it was asked."""
    return Impact(
        base.changed,
        base.affected,
        base.everything,
        base.global_files,
        (*base.notes, note),
        base.decided_by,
    )


def _short(items: list[str], limit: int = 20) -> str:
    more = f" ... (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more
