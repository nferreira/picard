"""Rust: Cargo workspace members and the path/workspace dependencies between them."""

from __future__ import annotations

import tomllib
from pathlib import Path

from picard.graph.model import Discovered, DiscoveryError, checked
from picard.graph.providers.base import GraphProvider

_DEPENDENCY_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")


def _dependency_names(manifest: dict) -> set[str]:
    """Crate names a manifest depends on, honouring `package = "real-name"` renames."""
    tables = [manifest.get(t, {}) for t in _DEPENDENCY_TABLES]
    for target in manifest.get("target", {}).values():
        tables.extend(target.get(t, {}) for t in _DEPENDENCY_TABLES)
    names: set[str] = set()
    for table in tables:
        for key, spec in table.items():
            names.add(spec.get("package", key) if isinstance(spec, dict) else key)
    return names


class CargoProvider(GraphProvider):
    name = "cargo"

    def discover(self, root: Path) -> list[Discovered]:
        top = root / "Cargo.toml"
        if not top.is_file():
            raise DiscoveryError(f"{top} not found")
        workspace = tomllib.loads(top.read_text(encoding="utf-8")).get("workspace")
        if workspace is None:
            raise DiscoveryError(f"{top} has no [workspace] table")
        excluded = {(root / e).resolve() for e in workspace.get("exclude", [])}
        crates: dict[str, tuple[str, dict]] = {}
        for pattern in workspace.get("members", []):
            for directory in sorted(root.glob(pattern)):
                manifest = directory / "Cargo.toml"
                if directory.resolve() in excluded or not manifest.is_file():
                    continue
                data = tomllib.loads(manifest.read_text(encoding="utf-8"))
                crate = data.get("package", {}).get("name")
                if crate is None:
                    raise DiscoveryError(f"{manifest} has no [package] name")
                crates[crate] = (directory.relative_to(root).as_posix() or ".", data)
        found = []
        for crate, (path, data) in crates.items():
            deps = sorted(_dependency_names(data) & set(crates) - {crate})
            found.append(Discovered(crate, path, tuple(deps), self.name))
        return checked(found)
