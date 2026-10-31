"""The interface every executor implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from picard.config import Package

MAX_RETRIES = 10
STRING_OPTIONS = ("launcher", "linter", "deps_checker")


@dataclass(frozen=True)
class Shard:
    """One slice of a suite that runs as several matrix jobs."""

    index: int  # 1-based
    total: int


@dataclass(frozen=True)
class SuiteContext:
    """What an executor needs to know to build one command."""

    package: Package
    suite: str  # "lint", "test", or any name a tier lists in `run`
    tier_name: str
    markers: str  # the tier's selection expression, empty for no filter
    shard: Shard | None = None  # set only when the plan splits this suite into jobs
    selection_base: str | None = None  # set only when the tier selects tests and a base is known


class Executor(ABC):
    """Builds the command that runs one suite of one package with one toolchain.

    An executor answers a single question: "which command runs `suite` for this package?". It
    does not execute anything, choose packages or know about tiers beyond their markers. The
    planner asks it for a command only when the package gives no explicit one. Options come from
    the `[executors.<name>]` table. Return None for a suite the toolchain has no command for.

    The class attributes say what the toolchain can do, so the planner and the config check
    never guess: `shardable` lists the suites it can split (it reads `ctx.shard`), `retryable`
    means it accepts `retries`, `selections` the values `selection` may take.
    """

    name: ClassVar[str]
    shardable: ClassVar[frozenset[str]] = frozenset()  # suites it can split into shards
    retryable: ClassVar[bool] = False  # accepts the `retries` option
    selections: ClassVar[tuple[str, ...]] = ()  # values the `selection` option may take

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options: dict[str, Any] = dict(options or {})
        self._check_options()

    def _check_options(self) -> None:
        """Validate the options every executor shares. Raises ValueError (a config error)."""
        if "retries" in self.options:
            retries = self.options["retries"]
            if (
                isinstance(retries, bool)
                or not isinstance(retries, int)
                or not 0 <= retries <= MAX_RETRIES
            ):
                raise ValueError(f"retries must be an integer from 0 to {MAX_RETRIES}")
            if retries > 0 and not self.retryable:
                raise ValueError(f"executor '{self.name}' has no retry option")
        if "selection" in self.options and self.options["selection"] not in self.selections:
            if not self.selections:
                raise ValueError(f"executor '{self.name}' has no selection option")
            raise ValueError(f"selection must be one of: {', '.join(self.selections)}")
        for key in STRING_OPTIONS:
            if key in self.options and not isinstance(self.options[key], str):
                raise ValueError(f"{key} must be a string")

    @abstractmethod
    def command(self, ctx: SuiteContext) -> str | None: ...
