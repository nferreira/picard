"""Any external engine (Nx, Bazel, Gradle, a script): ask a command what is affected.

    [impact]
    engine  = "command"
    command = "python3 tools/affected.py"

The command runs from the repository root. The changed files arrive on stdin, separated by NUL
(never on the command line), and it prints
    {"changed": ["pkg-a"], "affected": ["pkg-a", "pkg-b"], "notes": ["optional text"]}
using the package names picard knows. Any failure falls back to the native engine, never to a
smaller answer.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import TYPE_CHECKING

from picard.graph.impact import Impact, ImpactEngine

if TYPE_CHECKING:
    from picard.config import Config

TIMEOUT_SECONDS = 300


class CommandImpactEngine(ImpactEngine):
    name = "command"

    def compute(self, cfg: Config, files: list[str]) -> Impact:
        native = self.registries.engines.get("native")(self.options, self.registries)
        command = self.options.get("command")
        if not command:
            return _with_note(
                native.compute(cfg, files), "impact engine 'command' has no command, used native"
            )
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        try:
            result = subprocess.run(  # noqa: S603
                argv,
                input="\0".join(files),
                cwd=cfg.root,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"exited {result.returncode}: {result.stderr.strip()[:200]}")
            data = json.loads(result.stdout)
            changed = [str(n) for n in data["changed"]]
            affected = [str(n) for n in data["affected"]]
            unknown = set(changed + affected) - set(cfg.packages)
            if unknown:
                raise RuntimeError(f"unknown package(s) {', '.join(sorted(unknown))}")
        except (
            OSError,
            subprocess.TimeoutExpired,
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            return _with_note(
                native.compute(cfg, files), f"impact command failed ({exc}); used the native engine"
            )
        ordered = {n: (n,) for n in cfg.packages if n in set(affected) | set(changed)}
        notes = tuple(str(n) for n in data.get("notes", []))
        decided = dict.fromkeys(ordered, self.name)
        return Impact(
            tuple(n for n in cfg.packages if n in changed), ordered, False, (), notes, decided
        )


def _with_note(impact: Impact, note: str) -> Impact:
    return Impact(
        impact.changed,
        impact.affected,
        impact.everything,
        impact.global_files,
        (*impact.notes, note),
        impact.decided_by,
    )
