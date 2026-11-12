"""uv workspaces: members and their dependencies, read from uv.lock."""

from __future__ import annotations

import tomllib
from pathlib import Path

from picard.graph.model import Discovered, DiscoveryError, checked
from picard.graph.providers.base import GraphProvider


def _dependency_names(package: dict) -> tuple[set[str], set[str]]:
    """(what the package needs to run, what only its dev groups name).

    Extras are optional runtime dependencies, so they count as runtime. A name in a dev group and
    also in a runtime table is a runtime edge.
    """
    runtime = {d["name"] for d in package.get("dependencies", [])}
    for group in package.get("optional-dependencies", {}).values():
        runtime.update(d["name"] for d in group)
    dev: set[str] = set()
    for group in package.get("dev-dependencies", {}).values():
        dev.update(d["name"] for d in group)
    return runtime, dev - runtime


class UvProvider(GraphProvider):
    name = "uv"

    def discover(self, root: Path) -> list[Discovered]:
        lock = root / "uv.lock"
        if not lock.is_file():
            raise DiscoveryError(f"{lock} not found, run `uv lock` and commit it")
        data = tomllib.loads(lock.read_text(encoding="utf-8"))
        members = set(data.get("manifest", {}).get("members", []))
        paths: dict[str, str] = {}
        for package in data.get("package", []):
            source = package.get("source", {})
            raw_path = source.get("editable") or source.get("virtual")
            path = Path(raw_path).as_posix() if raw_path else "."
            if path == ".":
                continue  # the workspace root itself, not a unit of the graph
            if members and package["name"] not in members:
                continue  # an editable path dependency outside the workspace
            paths[package["name"]] = path
        found = []
        for package in data.get("package", []):
            name = package["name"]
            if name in paths:
                runtime, dev_only = _dependency_names(package)
                others = set(paths) - {name}
                deps = sorted((runtime | dev_only) & others)
                dev = sorted(dev_only & others)
                found.append(Discovered(name, paths[name], tuple(deps), self.name, tuple(dev)))
        return checked(found)
