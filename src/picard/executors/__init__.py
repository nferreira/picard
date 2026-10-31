"""Executors build the command for one suite of one package. Add your own with a plugin."""

from picard.executors import builtin, tools
from picard.executors.base import Executor, Shard, SuiteContext

# The toolchains picard runs itself, then the build tools it delegates a suite to.
BUILTIN_EXECUTORS = {**builtin.BUILTIN_EXECUTORS, **tools.TOOL_EXECUTORS}

__all__ = ["BUILTIN_EXECUTORS", "Executor", "Shard", "SuiteContext"]
