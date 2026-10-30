"""Go: modules from go.work (or every go.mod), and the requires/replaces between them."""

from __future__ import annotations

import re
from pathlib import Path

from picard.graph.model import Discovered, DiscoveryError, checked
from picard.graph.providers.base import GraphProvider

_SKIP = {"vendor", "node_modules", ".git"}
_MODULE = re.compile(r"^\s*module\s+(\S+)", re.M)


def _strip_comments(text: str) -> str:
    return re.sub(r"//.*", "", text)


def _directive_targets(text: str, directive: str) -> list[str]:
    """Words after `directive` in both the single-line and the parenthesised block form."""
    found: list[str] = []
    for block in re.finditer(rf"^\s*{directive}\s*\((.*?)^\s*\)", text, re.M | re.S):
        found += [line.split()[0] for line in block.group(1).splitlines() if line.strip()]
    for single in re.finditer(rf"^\s*{directive}\s+([^\s(][^\n]*)", text, re.M):
        found.append(single.group(1).split()[0])
    return found


def _replace_sources(text: str) -> list[str]:
    """Module paths on the left of every `replace old => new`."""
    found: list[str] = []
    for line in re.findall(r"^\s*(?:replace\s+)?(\S+)(?:\s+\S+)?\s*=>", text, re.M):
        found.append(line)
    return found


class GoProvider(GraphProvider):
    name = "go"

    def _module_dirs(self, root: Path) -> list[Path]:
        work = root / "go.work"
        if work.is_file():
            uses = _directive_targets(_strip_comments(work.read_text(encoding="utf-8")), "use")
            return [(root / u).resolve() for u in uses]
        return sorted(
            m.parent.resolve()
            for m in root.rglob("go.mod")
            if not any(
                part in _SKIP or part.startswith(".") for part in m.relative_to(root).parts[:-1]
            )
        )

    def discover(self, root: Path) -> list[Discovered]:
        modules: dict[str, tuple[str, str]] = {}
        for directory in self._module_dirs(root):
            manifest = directory / "go.mod"
            if not manifest.is_file():
                raise DiscoveryError(f"{manifest} not found (listed in go.work)")
            text = _strip_comments(manifest.read_text(encoding="utf-8"))
            match = _MODULE.search(text)
            if not match:
                raise DiscoveryError(f"{manifest} has no module line")
            modules[match.group(1).strip('"')] = (
                directory.relative_to(root.resolve()).as_posix() or ".",
                text,
            )
        if not modules:
            raise DiscoveryError("no go.mod files found")
        found = []
        for module, (path, text) in modules.items():
            required = set(_directive_targets(text, "require")) | set(_replace_sources(text))
            deps = sorted((required & set(modules)) - {module})
            found.append(Discovered(module, path, tuple(deps), self.name))
        return checked(found)
