"""The composition root: build the registries every run uses.

Built-ins and plugins are registered here and nowhere else, so the rest of picard depends on
the interfaces and the registries, never on a concrete provider, engine or executor.
"""

from __future__ import annotations

from pathlib import Path

from picard.plugins import load_plugins
from picard.registry import Registries


def default_registries(
    *,
    plugin_modules: tuple[str, ...] = (),
    plugin_paths: tuple[str, ...] = (),
    root: Path | None = None,
) -> Registries:
    from picard.executors import BUILTIN_EXECUTORS
    from picard.graph.engines import BUILTIN_ENGINES
    from picard.graph.providers import BUILTIN_PROVIDERS

    registries = Registries()
    for provider in BUILTIN_PROVIDERS:
        registries.providers.register(provider.name, provider)
    for engine in BUILTIN_ENGINES:
        registries.engines.register(engine.name, engine)
    for name, executor in BUILTIN_EXECUTORS.items():
        registries.executors.register(name, executor)
    load_plugins(registries, modules=plugin_modules, paths=plugin_paths, root=root)
    return registries
