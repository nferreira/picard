"""Executors that hand a package's suite to a build tool: pants, turbo and nx.

Caching task results and ordering tasks between packages is what these tools exist for, and it
needs hermetic tasks and declared inputs that picard does not have. So picard does not grow a
cache or a scheduler: it still decides which packages run at each tier, and the command it emits
asks the tool to run that package's suite, through the tool's own local and remote cache and its
own task ordering.

Each executor takes one option, `launcher`, the program to call (default the tool's name, for
example `./pants` or `npx turbo`). Only Pants can split a suite into shards and retry failing
tests from the command line, so only Pants is `shardable` and `retryable`. turbo and nx run the
package's own `lint` and `test` tasks and ignore the tier's markers, like `pnpm`: a task that
should filter by tier reads its own configuration. Pants ignores them too unless its `markers`
option is true (see `PantsExecutor`).
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from picard.executors.base import Executor, SuiteContext


def _launcher(executor: Executor) -> str:
    return executor.options.get("launcher", executor.name)


class PantsExecutor(Executor):
    """`pants lint|test <path>::`. Options: launcher, retries, markers.

    The tier's markers are ignored by default, which only widens what runs. Pants runs pytest
    once per test file, and a file whose tests `-m` deselects exits 5, which Pants counts as a
    failure. `markers = true` passes `-- -m "<markers>"` for a repo that makes exit 5 a success.
    """

    name = "pants"
    shardable = frozenset({"test"})
    retryable = True

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(options)
        if not isinstance(self.options.get("markers", False), bool):
            raise ValueError("markers must be true or false")

    def command(self, ctx: SuiteContext) -> str | None:
        path = ctx.package.path.strip("/")
        spec = "::" if path in ("", ".") else shlex.quote(f"{path}::")
        launcher = _launcher(self)
        if ctx.suite == "lint":
            return f"{launcher} lint {spec}"
        if ctx.suite != "test":
            return None
        flags = ""
        if ctx.shard is not None:
            # Pants numbers shards from 0: `--test-shard=0/3` is the first of three.
            flags += f" --test-shard={ctx.shard.index - 1}/{ctx.shard.total}"
        retries = self.options.get("retries", 0)
        if retries:
            # Pants counts attempts, not reruns: one run plus `retries` more.
            flags += f" --test-attempts-default={retries + 1}"
        passthrough = ""
        if self.options.get("markers", False) and ctx.markers:
            passthrough = f' -- -m "{ctx.markers}"'
        return f"{launcher} test{flags} {spec}{passthrough}"


class TurboExecutor(Executor):
    """`turbo run <suite> --filter=<package>` for `lint` and `test`. Option: launcher."""

    name = "turbo"

    def command(self, ctx: SuiteContext) -> str | None:
        if ctx.suite not in ("lint", "test"):
            return None
        target = shlex.quote(f"--filter={ctx.package.name}")
        return f"{_launcher(self)} run {ctx.suite} {target}"


class NxExecutor(Executor):
    """`nx run <package>:<suite>` for `lint` and `test`. Option: launcher."""

    name = "nx"

    def command(self, ctx: SuiteContext) -> str | None:
        if ctx.suite not in ("lint", "test"):
            return None
        return f"{_launcher(self)} run {shlex.quote(f'{ctx.package.name}:{ctx.suite}')}"


TOOL_EXECUTORS = {cls.name: cls for cls in (PantsExecutor, TurboExecutor, NxExecutor)}
