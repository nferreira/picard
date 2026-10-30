"""The interface every executor implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from picard.config import Package


@dataclass(frozen=True)
class SuiteContext:
    """What an executor needs to know to build one command."""

    package: Package
    suite: str  # "lint", "test", or any name a tier lists in `run`
    tier_name: str
    markers: str  # the tier's selection expression, empty for no filter


class Executor(ABC):
    """Builds the command that runs one suite of one package with one toolchain.

    An executor answers a single question: "which command runs `suite` for this package?". It
    does not execute anything, choose packages or know about tiers beyond their markers. The
    planner asks it for a command only when the package gives no explicit one. Options come from
    the `[executors.<name>]` table. Return None for a suite the toolchain has no command for.
    """

    name: ClassVar[str]

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options: dict[str, Any] = dict(options or {})

    @abstractmethod
    def command(self, ctx: SuiteContext) -> str | None: ...
