"""Ask Nx which projects a change affects.

    [impact]
    engine   = "nx"
    command  = "nx"          # the launcher, for example "npx nx" (default "nx")
    fallback = "native"
    timeout  = 600

Nx projects are matched to picard packages by name (`nx show projects --json`). With a git range
and nothing uncommitted picard asks `nx show projects --affected --base=<base> --head=<head>
--json`; otherwise (`--files`, `--staged`, `--working`, uncommitted files under `--local`) it
passes the files Nx owns with `--files`, the only engine that puts files on a command line, and
gives up to the fallback when that list would be longer than 100,000 characters. The shared
policy (fallback, dependents, failures) is in `tool.py`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from picard.graph.engines.tool import Skip, ToolImpactEngine

if TYPE_CHECKING:
    from picard.config import Config

FILES_LIMIT = 100_000  # characters in --files, well under any platform's argument limit

INSTALL_HELP = """\
Nx is not installed (or not on your PATH). Nx usually lives in the repository's own
devDependencies: install them, then point picard at it, for example command = "npx nx".
Installing Nx: https://nx.dev/docs/getting-started/installation

Without Nx picard answers with the native engine. To stop looking for Nx, set
`engine = "native"` under `[impact]`."""


def _names(stdout: str) -> list[str]:
    data = json.loads(stdout)
    if not isinstance(data, list) or not all(isinstance(n, str) for n in data):
        raise RuntimeError("nx did not print a JSON list of project names")
    return data


class NxImpactEngine(ToolImpactEngine):
    name = "nx"
    label = "Nx"
    default_command = "nx"
    install = INSTALL_HELP

    def known(self, cfg: Config) -> set[str]:
        return self.keep(cfg, _names(self.run(cfg, ["show", "projects", "--json"])), "nx projects")

    def query(self, cfg: Config, files: list[str]) -> set[str]:
        revisions = self.revisions
        if revisions is not None and not revisions.uncommitted:
            args = [f"--base={revisions.base}", f"--head={revisions.head}"]
        else:
            joined = ",".join(files)
            if len(joined) > FILES_LIMIT:
                raise Skip(
                    f"nx was not asked: --files would be {len(joined)} characters "
                    f"(over {FILES_LIMIT}); the {self.options.get('fallback', 'native')} "
                    "engine answered"
                )
            args = [f"--files={joined}"]
        stdout = self.run(cfg, ["show", "projects", "--affected", *args, "--json"])
        return self.keep(cfg, _names(stdout), "nx affected")
