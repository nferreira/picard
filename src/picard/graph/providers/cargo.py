"""Rust: Cargo workspace members and the path/workspace dependencies between them."""

from __future__ import annotations

import tomllib
from pathlib import Path

from picard.graph.model import Discovered, DiscoveryError, checked
from picard.graph.providers.base import GraphProvider

_RUNTIME_TABLES = ("dependencies", "build-dependencies")
_DEV_TABLES = ("dev-dependencies",)


def _names(manifest: dict, kinds: tuple[str, ...]) -> set[str]:
    tables = [manifest.get(t, {}) for t in kinds]
    for target in manifest.get("target", {}).values():
        tables.extend(target.get(t, {}) for t in kinds)
    names: set[str] = set()
    for table in tables:
        for key, spec in table.items():
            names.add(spec.get("package", key) if isinstance(spec, dict) else key)
    return names


def _dependency_names(manifest: dict) -> tuple[set[str], set[str]]:
    """(runtime, dev-only) crate names, honouring `package = "real-name"` renames."""
    runtime = _names(manifest, _RUNTIME_TABLES)
    return runtime, _names(manifest, _DEV_TABLES) - runtime


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
            runtime, dev_only = _dependency_names(data)
            others = set(crates) - {crate}
            found.append(
                Discovered(
                    crate,
                    path,
                    tuple(sorted((runtime | dev_only) & others)),
                    self.name,
                    tuple(sorted(dev_only & others)),
                )
            )
        return checked(found)
