"""Ask moon which projects a change affects.

    [impact]
    engine   = "moon"
    command  = "moon"        # the launcher (default "moon")
    fallback = "native"
    timeout  = 600

moon projects are matched to picard packages by their `source` directory: `moon query projects`
lists them. picard then runs `moon query projects --affected --downstream deep` and writes the
files moon owns on stdin, in the shape `moon query changed-files` prints:
{"files": [...], "options": {}}. It reads {"projects": [{"id": ..., "source": ...}]} back.

Neither the stdin input nor the output shape is verified against moon's documentation, so
anything unexpected is a failure that widens to the fallback. The shared policy (fallback,
dependents, failures) is in `tool.py`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from picard.graph.engines.tool import ToolImpactEngine

if TYPE_CHECKING:
    from picard.config import Config

INSTALL_HELP = """\
moon is not installed (or not on your PATH). Install it with proto (`proto install moon`), its
install script, or the @moonrepo/cli package: https://moonrepo.dev/docs/install

Without moon picard answers with the native engine. To stop looking for it, set
`engine = "native"` under `[impact]`."""


def _sources(stdout: str) -> list[str]:
    projects = json.loads(stdout)["projects"]
    if not isinstance(projects, list):
        raise RuntimeError("moon printed projects that is not a list")
    sources = [project["source"] for project in projects]
    if not all(isinstance(s, str) for s in sources):
        raise RuntimeError("moon printed a project source that is not a string")
    return [s.strip("/") or "." for s in sources]


class MoonImpactEngine(ToolImpactEngine):
    name = "moon"
    label = "moon"
    default_command = "moon"
    install = INSTALL_HELP

    def _packages(self, cfg: Config, sources: list[str], what: str) -> set[str]:
        by_path = {p.path: p.name for p in cfg.packages.values()}
        unknown = sorted(s for s in sources if s not in by_path)
        if unknown:
            self.log(f"{what}: sources that are not picard packages, ignored: {unknown[:20]}")
        return {by_path[s] for s in sources if s in by_path}

    def known(self, cfg: Config) -> set[str]:
        return self._packages(cfg, _sources(self.run(cfg, ["query", "projects"])), "moon projects")

    def query(self, cfg: Config, files: list[str]) -> set[str]:
        stdin = json.dumps({"files": files, "options": {}})
        stdout = self.run(
            cfg, ["query", "projects", "--affected", "--downstream", "deep"], stdin=stdin
        )
        return self._packages(cfg, _sources(stdout), "moon affected")
