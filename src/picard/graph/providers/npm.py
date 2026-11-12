"""npm, pnpm and yarn workspaces: members and `workspace:` dependencies, read from package.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

from picard.graph.model import Discovered, DiscoveryError, checked
from picard.graph.providers.base import GraphProvider

_YAML_ITEM = re.compile(r"""^\s*-\s*['"]?([^'"#]+?)['"]?\s*(?:#.*)?$""")
_RUNTIME_FIELDS = ("dependencies", "peerDependencies", "optionalDependencies")
_DEV_FIELDS = ("devDependencies",)


def _workspace_patterns(root: Path) -> list[str]:
    patterns: list[str] = []
    package_json = root / "package.json"
    if package_json.is_file():
        workspaces = json.loads(package_json.read_text(encoding="utf-8")).get("workspaces", [])
        patterns += workspaces.get("packages", []) if isinstance(workspaces, dict) else workspaces
    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        in_packages = False
        for line in pnpm.read_text(encoding="utf-8").splitlines():
            if re.match(r"^packages\s*:", line):
                in_packages = True
            elif in_packages and (item := _YAML_ITEM.match(line)):
                patterns.append(item.group(1))
            elif in_packages and line.strip() and not line.lstrip().startswith("#"):
                in_packages = False
    return [p for p in patterns if not p.startswith("!")]


class NpmProvider(GraphProvider):
    name = "npm"

    def discover(self, root: Path) -> list[Discovered]:
        patterns = _workspace_patterns(root)
        if not patterns:
            raise DiscoveryError("no workspaces found in package.json or pnpm-workspace.yaml")
        manifests: dict[str, tuple[str, dict]] = {}
        for pattern in patterns:
            for directory in sorted(root.glob(pattern)):
                manifest = directory / "package.json"
                if "node_modules" in directory.parts or not manifest.is_file():
                    continue
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if "name" not in data:
                    raise DiscoveryError(f"{manifest} has no name")
                manifests[data["name"]] = (directory.relative_to(root).as_posix(), data)
        found = []
        for name, (path, data) in manifests.items():
            runtime: set[str] = set()
            for key in _RUNTIME_FIELDS:
                runtime.update(data.get(key, {}))
            dev: set[str] = set()
            for key in _DEV_FIELDS:
                dev.update(data.get(key, {}))
            others = set(manifests) - {name}
            found.append(
                Discovered(
                    name,
                    path,
                    tuple(sorted((runtime | dev) & others)),
                    self.name,
                    tuple(sorted((dev - runtime) & others)),
                )
            )
        return checked(found)
