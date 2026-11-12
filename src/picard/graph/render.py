"""Rendering the graph and an impact: a tree for terminals, Mermaid for docs, the "why" lines."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from picard.graph.impact import Impact

if TYPE_CHECKING:
    from picard.config import Config

_RESET, _RED, _YELLOW, _DIM = "\033[0m", "\033[31;1m", "\033[33m", "\033[2m"


def _state(impact: Impact | None, name: str) -> str:
    if impact is None:
        return "idle"
    if name in impact.changed:
        return "changed"
    return "affected" if name in impact.affected else "idle"


def render_tree(cfg: Config, impact: Impact | None = None, *, color: bool = False) -> str:
    """The graph as a tree from the apps (nobody depends on them) down to the leaves."""
    symbol = {"changed": "●", "affected": "◐", "idle": "○"}
    paint = {"changed": _RED, "affected": _YELLOW, "idle": _DIM}

    def label(name: str) -> str:
        state = _state(impact, name)
        package = cfg.packages[name]
        text = f"{symbol[state]} {name}  {package.path}"
        if package.provider != "manual":
            text += f"  [{package.provider}]"
        if state != "idle" and impact is not None and name in impact.decided_by:
            text += f"  via {impact.decided_by[name]}"
        return f"{paint[state]}{text}{_RESET}" if color else text

    used = {d for p in cfg.packages.values() for d in p.depends_on}
    roots = [n for n in cfg.packages if n not in used] or list(cfg.packages)
    lines: list[str] = []
    cyclic = False

    def walk(name: str, prefix: str, last: bool, top: bool, above: frozenset[str]) -> None:
        nonlocal cyclic
        branch = "" if top else ("└─ " if last else "├─ ")
        lines.append(f"{prefix}{branch}{label(name)}")
        children = cfg.packages[name].depends_on
        extension = "" if top else ("   " if last else "│  ")
        path = above | {name}
        for i, child in enumerate(children):
            if child in path:
                # A dependency cycle (allowed by [graph] cycles): say so instead of descending.
                cyclic = True
                twig = "└─ " if i == len(children) - 1 else "├─ "
                lines.append(f"{prefix}{extension}{twig}↺ {child}  (cycle)")
                continue
            walk(child, prefix + extension, i == len(children) - 1, False, path)

    for root in roots:
        walk(root, "", True, True, frozenset())

    legend = "● changed   ◐ affected (depends on a change)   ○ untouched   (indent = depends on)"
    if cyclic:
        legend += "   ↺ depends back on a package above (cycle)"
    if impact is not None:
        lines.append("")
        lines.append(
            "[provider] read the package from its manifest   via X: what decided it is affected"
        )
    return "\n".join([*lines, "", legend])


def render_why(impact: Impact) -> list[str]:
    """One line per affected package explaining how the change reaches it."""
    if impact.everything and not impact.global_files:
        return ["everything is affected"]
    if impact.everything and set(impact.decided_by.values()) == {"lockfile"}:
        files = ", ".join(impact.global_files)
        return [f"everything is affected: {files} cannot be attributed to packages"]
    if impact.everything:
        return [f"everything is affected: {', '.join(impact.global_files)} is a global file"]

    def by(name: str) -> str:
        return f" (via {impact.decided_by[name]})" if name in impact.decided_by else ""

    def reached(name: str) -> str:
        if impact.decided_by.get(name) == "always":
            return f"{name} is always affected (always = true)"
        return f"{name} is affected through {impact.chain(name)}{by(name)}"

    def owner(name: str) -> str:
        # Test-only, unless another change reaches the package's dependents through it anyway.
        walked = any(name in chain[1:] for chain in impact.affected.values())
        if name in impact.test_only and not walked:
            return f"{name} owns a changed file (test files only: dependents are not affected){by(name)}"
        return f"{name} owns a changed file{by(name)}"

    return [reached(name) for name in impact.affected if name not in impact.changed] + [
        owner(name) for name in impact.changed
    ]


def render_mermaid(cfg: Config, impact: Impact | None = None) -> str:
    """Mermaid flowchart. An arrow A --> B reads "A depends on B"."""
    ids = {name: f"n{i}" for i, name in enumerate(cfg.packages)}
    lines = ["flowchart TD"]
    for name, package in cfg.packages.items():
        lines.append(f'  {ids[name]}["{name}<br/><small>{package.path}</small>"]')
    for name, package in cfg.packages.items():
        lines.extend(f"  {ids[name]} --> {ids[dep]}" for dep in package.depends_on)
    lines += [
        "  classDef changed fill:#ff6b6b,stroke:#b00020,color:#000",
        "  classDef affected fill:#ffd166,stroke:#b58900,color:#000",
        "  classDef idle fill:#eeeeee,stroke:#999999,color:#555",
    ]
    for state in ("changed", "affected", "idle"):
        members = [ids[n] for n in cfg.packages if _state(impact, n) == state]
        if members:
            lines.append(f"  class {','.join(members)} {state}")
    return "\n".join(lines)


def split_files(values: Sequence[str], *, commas: bool = True) -> list[str]:
    """`--files a b,c` -> [a, b, c]; normalises ./ prefixes and backslashes.

    Names from a NUL/newline list may contain commas, so `commas=False` keeps them whole.
    """
    out: list[str] = []
    for value in values:
        for part in value.split(",") if commas else [value]:
            part = part.strip().replace("\\", "/")
            if part:
                out.append(part[2:] if part.startswith("./") else part)
    return list(dict.fromkeys(out))
