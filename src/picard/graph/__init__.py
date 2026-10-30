"""The dependency graph: discovery, impact and rendering.

Three small, separate responsibilities live in this package:

  providers/  read which packages exist and who depends on whom (uv, npm, cargo, go, command)
  engines/    decide what a change affects (native graph walk, Pants, any command)
  render.py   draw it

This module is the stable facade the rest of picard (and its tests) imports from.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from picard.graph.impact import (
    Impact,
    ImpactEngine,
    NativeImpactEngine,
    everything,
    owner,
    owns,
)
from picard.graph.model import NAME_OK, DependencyGraph, Discovered, DiscoveryError
from picard.graph.render import render_mermaid, render_tree, render_why, split_files

if TYPE_CHECKING:
    from picard.config import Config
    from picard.registry import Registries

__all__ = [
    "NAME_OK",
    "DependencyGraph",
    "Discovered",
    "DiscoveryError",
    "Impact",
    "ImpactEngine",
    "NativeImpactEngine",
    "compute_impact",
    "discover",
    "everything",
    "owner",
    "owns",
    "render_mermaid",
    "render_tree",
    "render_why",
    "split_files",
]


def discover(
    provider: str,
    root: Path,
    *,
    options: Mapping[str, Any] | None = None,
    registries: Registries | None = None,
) -> list[Discovered]:
    """Run one graph provider by name."""
    from picard.bootstrap import default_registries
    from picard.registry import UnknownPluginError

    registries = registries or default_registries()
    try:
        instance = registries.providers.get(provider)(options)
    except UnknownPluginError as exc:
        raise DiscoveryError(str(exc)) from exc
    try:
        return instance.discover(root)
    except DiscoveryError as exc:
        raise DiscoveryError(f"{provider} provider: {exc}") from exc
    except (OSError, ValueError, KeyError) as exc:
        raise DiscoveryError(f"{provider} provider: {exc}") from exc


def compute_impact(cfg: Config, files: Iterable[str]) -> Impact:
    """What `files` affect, using the impact engine the configuration names."""
    files = list(files)
    key = ("impact", cfg.impact.engine, tuple(files))
    memo = cfg.registries.memo  # one plan asks the same question several times; ask the engine once
    if key not in memo:
        engine = cfg.registries.engines.get(cfg.impact.engine)(cfg.impact.options, cfg.registries)
        memo[key] = engine.impact(cfg, files)
    return memo[key]
