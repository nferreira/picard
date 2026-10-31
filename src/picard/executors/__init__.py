"""Executors build the command for one suite of one package. Add your own with a plugin."""

from picard.executors.base import Executor, Shard, SuiteContext
from picard.executors.builtin import BUILTIN_EXECUTORS

__all__ = ["BUILTIN_EXECUTORS", "Executor", "Shard", "SuiteContext"]
