"""Built-in impact engines. Add your own with a plugin: registries.engines.register(...)."""

from picard.graph.engines.bazel_diff import BazelDiffImpactEngine
from picard.graph.engines.command import CommandImpactEngine
from picard.graph.engines.moon import MoonImpactEngine
from picard.graph.engines.nx import NxImpactEngine
from picard.graph.engines.pants import PantsImpactEngine
from picard.graph.engines.turbo import TurboImpactEngine
from picard.graph.impact import ImpactEngine, NativeImpactEngine

BUILTIN_ENGINES = (
    NativeImpactEngine,
    PantsImpactEngine,
    CommandImpactEngine,
    NxImpactEngine,
    TurboImpactEngine,
    MoonImpactEngine,
    BazelDiffImpactEngine,
)

__all__ = ["BUILTIN_ENGINES", "ImpactEngine"]
