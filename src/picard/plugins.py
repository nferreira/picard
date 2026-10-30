"""Loading plugins named in `[plugins]`.

A plugin is a Python module with a `register(registries)` function. It can add graph
providers, impact engines, executors and rule actions. Plugins come from the repository's own
configuration, so they carry the same trust as its workflows: review edits to them the same way.

    [plugins]
    modules = ["acme_picard"]                  # importable from the environment
    paths   = [".pipeline/plugins/bazel.py"]   # loaded from a file in the repository
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterable
from pathlib import Path

from picard.registry import Registries


class PluginError(RuntimeError):
    pass


def _call_register(module: object, label: str, registries: Registries) -> None:
    register = getattr(module, "register", None)
    if not callable(register):
        raise PluginError(f"plugin {label} has no register(registries) function")
    register(registries)


def load_plugins(
    registries: Registries, *, modules: Iterable[str], paths: Iterable[str], root: Path | None
) -> None:
    for name in modules:
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            raise PluginError(f"cannot import plugin module '{name}': {exc}") from exc
        _call_register(module, name, registries)
    for relative in paths:
        base = root or Path.cwd()
        path = (base / relative).resolve()
        if base.resolve() not in path.parents:
            raise PluginError(f"plugin path '{relative}' is outside the repository")
        if not path.is_file():
            raise PluginError(f"plugin file '{relative}' not found")
        spec = importlib.util.spec_from_file_location(f"picard_plugin_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise PluginError(f"cannot load plugin file '{relative}'")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # a plugin is arbitrary code; report, do not crash with a trace
            raise PluginError(f"plugin file '{relative}' failed to load: {exc}") from exc
        _call_register(module, relative, registries)
