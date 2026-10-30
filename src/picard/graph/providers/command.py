"""Any other ecosystem: run a command that prints the graph as JSON.

    [[discover]]
    provider = "command"
    command  = "python3 tools/print_graph.py"

The command runs from the repository root and must print
    {"packages": [{"name": "a", "path": "libs/a", "depends_on": ["b"]}, ...]}
This is the escape hatch for Bazel, Gradle, Maven, Nx or anything else that can describe its
workspace, without writing a plugin.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from picard.graph.model import Discovered, DiscoveryError, checked
from picard.graph.providers.base import GraphProvider

TIMEOUT_SECONDS = 120


class CommandProvider(GraphProvider):
    name = "command"

    def discover(self, root: Path) -> list[Discovered]:
        command = self.options.get("command")
        if not command:
            raise DiscoveryError("the command provider needs `command = ...`")
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        try:
            result = subprocess.run(  # noqa: S603
                argv, cwd=root, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiscoveryError(f"cannot run {argv[0]!r}: {exc}") from exc
        if result.returncode != 0:
            raise DiscoveryError(
                f"{argv[0]!r} exited {result.returncode}: {result.stderr.strip()[:300]}"
            )
        try:
            packages = json.loads(result.stdout)["packages"]
            found = [
                Discovered(
                    str(p["name"]),
                    str(p["path"]).strip("/") or ".",
                    tuple(p.get("depends_on", [])),
                    self.name,
                )
                for p in packages
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise DiscoveryError(f"{argv[0]!r} did not print the expected JSON: {exc}") from exc
        names = {d.name for d in found}
        for item in found:
            unknown = set(item.depends_on) - names
            if unknown:
                raise DiscoveryError(
                    f"{item.name} depends on unknown package(s): {', '.join(sorted(unknown))}"
                )
        return checked(found)
