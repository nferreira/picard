"""Ask Turborepo which packages a change affects.

    [impact]
    engine   = "turbo"
    command  = "turbo"       # the launcher, for example "npx turbo" (default "turbo")
    fallback = "native"
    timeout  = 600

Turborepo reads git refs, not a list of files: picard runs `turbo ls --affected --output=json`
with TURBO_SCM_BASE and TURBO_SCM_HEAD set to the change's base and head. Without a git range
(`--files`, `--staged`, `--working`) the fallback answers and the plan says so. Packages are
matched to picard packages by name (`turbo ls --output=json`).

The JSON shape, {"packages": {"items": [{"name": ..., "path": ...}]}}, is not documented
upstream, so anything else is treated as a failure and widens to the fallback. The shared
policy (fallback, dependents, failures) is in `tool.py`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from picard.graph.engines.tool import ToolImpactEngine

if TYPE_CHECKING:
    from picard.config import Config

INSTALL_HELP = """\
Turborepo is not installed (or not on your PATH). It usually lives in the repository's own
devDependencies: install them, then point picard at it, for example command = "npx turbo".
Installing Turborepo: https://turborepo.dev/docs/getting-started/installation

Without Turborepo picard answers with the native engine. To stop looking for it, set
`engine = "native"` under `[impact]`."""


def _names(stdout: str) -> list[str]:
    items = json.loads(stdout)["packages"]["items"]
    if not isinstance(items, list):
        raise RuntimeError("turbo printed packages.items that is not a list")
    names = [item["name"] for item in items]
    if not all(isinstance(n, str) for n in names):
        raise RuntimeError("turbo printed a package name that is not a string")
    return names


class TurboImpactEngine(ToolImpactEngine):
    name = "turbo"
    label = "Turborepo"
    default_command = "turbo"
    install = INSTALL_HELP
    needs_revisions = True

    def known(self, cfg: Config) -> set[str]:
        return self.keep(cfg, _names(self.run(cfg, ["ls", "--output=json"])), "turbo packages")

    def query(self, cfg: Config, files: list[str]) -> set[str]:
        if self.revisions is None:  # compute() already handed this to the fallback
            raise RuntimeError("no git range to ask turbo about")
        env = {"TURBO_SCM_BASE": self.revisions.base, "TURBO_SCM_HEAD": self.revisions.head}
        stdout = self.run(cfg, ["ls", "--affected", "--output=json"], env=env)
        return self.keep(cfg, _names(stdout), "turbo affected")
