"""Built-in impact engines. Add your own with a plugin: registries.engines.register(...)."""

from picard.graph.engines.command import CommandImpactEngine
from picard.graph.engines.pants import PantsImpactEngine
from picard.graph.impact import ImpactEngine, NativeImpactEngine

BUILTIN_ENGINES = (NativeImpactEngine, PantsImpactEngine, CommandImpactEngine)

__all__ = ["BUILTIN_ENGINES", "ImpactEngine"]
