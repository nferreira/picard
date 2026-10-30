"""Built-in graph providers. Add your own with a plugin: registries.providers.register(...)."""

from picard.graph.providers.base import GraphProvider
from picard.graph.providers.cargo import CargoProvider
from picard.graph.providers.command import CommandProvider
from picard.graph.providers.golang import GoProvider
from picard.graph.providers.npm import NpmProvider
from picard.graph.providers.uv import UvProvider

BUILTIN_PROVIDERS = (UvProvider, NpmProvider, CargoProvider, GoProvider, CommandProvider)

__all__ = ["BUILTIN_PROVIDERS", "GraphProvider"]
