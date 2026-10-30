"""The interface every graph provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from picard.graph.model import Discovered


class GraphProvider(ABC):
    """Reads which packages exist and who depends on whom, from one ecosystem's own metadata.

    A provider does exactly that and nothing else: it does not run tests, decide what is
    affected or know about git. It reads manifests rather than resolving dependencies, so the
    graph has one source of truth. Options come from the `[[discover]]` block that names it.
    """

    name: ClassVar[str]

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options: dict[str, Any] = dict(options or {})

    @abstractmethod
    def discover(self, root: Path) -> list[Discovered]:
        """The workspace's packages. Raise DiscoveryError for anything unusable."""
