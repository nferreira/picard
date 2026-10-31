"""Delegating executors: pants, turbo and nx run a package's suite through the tool's own cache.

None of these tools runs here: the executors only build strings, so the tests check the exact
commands, and the planner tests check that an explicit command still wins and shards expand.
"""

import copy
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard.bootstrap import default_registries
from picard.config import ConfigError, Package
from picard.executors import BUILTIN_EXECUTORS, Shard, SuiteContext
from picard.executors.builtin import BUILTIN_EXECUTORS as RUNNERS
from picard.executors.tools import TOOL_EXECUTORS, NxExecutor, PantsExecutor, TurboExecutor
from picard.planner import build_plan

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def pkg(name="ui-kit", path="packages/ui-kit") -> Package:
    return Package(name, path, (), False, {})


def ctx(suite="test", markers="", package=None, shard=None) -> SuiteContext:
    return SuiteContext(package or pkg(), suite, "pr", markers, shard=shard)


def test_registered_with_the_built_ins():
    assert set(TOOL_EXECUTORS) == {"pants", "turbo", "nx"}
    assert BUILTIN_EXECUTORS == {**RUNNERS, **TOOL_EXECUTORS}
    assert {"pants", "turbo", "nx", "pytest"} <= set(default_registries().executors)


def test_capabilities():
    assert PantsExecutor.shardable == frozenset({"test"}) and PantsExecutor.retryable
    for cls in (TurboExecutor, NxExecutor):
        assert cls.shardable == frozenset() and not cls.retryable
    for cls in TOOL_EXECUTORS.values():
        assert cls.selections == ()


@pytest.mark.parametrize(
    ("cls", "suite", "markers", "expected"),
    [
        (PantsExecutor, "lint", "unit", "pants lint packages/ui-kit::"),
        (PantsExecutor, "test", "", "pants test packages/ui-kit::"),
        (PantsExecutor, "test", "unit", "pants test packages/ui-kit::"),  # markers ignored
        (PantsExecutor, "deps", "unit", None),
        (TurboExecutor, "lint", "unit", "turbo run lint --filter=ui-kit"),
        (TurboExecutor, "test", "unit", "turbo run test --filter=ui-kit"),  # markers ignored
        (TurboExecutor, "build", "", None),
        (NxExecutor, "lint", "", "nx run ui-kit:lint"),
        (NxExecutor, "test", "baseline", "nx run ui-kit:test"),  # markers ignored
        (NxExecutor, "deps", "", None),
    ],
)
def test_commands(cls, suite, markers, expected):
    assert cls().command(ctx(suite, markers)) == expected


@pytest.mark.parametrize(
    ("suite", "markers", "expected"),
    [
        ("test", "unit", 'pants test packages/ui-kit:: -- -m "unit"'),
        (
            "test",
            "not slow and not e2e",
            'pants test packages/ui-kit:: -- -m "not slow and not e2e"',
        ),
        ("test", "", "pants test packages/ui-kit::"),  # no filter, nothing to pass
        ("lint", "unit", "pants lint packages/ui-kit::"),
    ],
)
def test_pants_passes_markers_only_when_asked(suite, markers, expected):
    assert PantsExecutor({"markers": True}).command(ctx(suite, markers)) == expected
    if suite == "test" and markers:
        assert PantsExecutor({"markers": False}).command(ctx(suite, markers)) == (
            "pants test packages/ui-kit::"
        )


@pytest.mark.parametrize("path", [".", ""])
def test_pants_at_the_repo_root_uses_the_bare_spec(path):
    executor = PantsExecutor()
    root = pkg(path=path)
    assert executor.command(ctx("lint", package=root)) == "pants lint ::"
    assert executor.command(ctx("test", "unit", package=root)) == "pants test ::"
    assert PantsExecutor({"markers": True}).command(ctx("test", "unit", package=root)) == (
        'pants test :: -- -m "unit"'
    )


def test_launcher_option():
    assert PantsExecutor({"launcher": "./pants"}).command(ctx()) == "./pants test packages/ui-kit::"
    assert TurboExecutor({"launcher": "npx turbo"}).command(ctx()) == (
        "npx turbo run test --filter=ui-kit"
    )
    assert NxExecutor({"launcher": "pnpm nx"}).command(ctx()) == "pnpm nx run ui-kit:test"


def test_names_and_paths_are_quoted():
    odd = pkg(name="@demo/ui kit", path="packages/ui kit")
    assert PantsExecutor().command(ctx("lint", package=odd)) == "pants lint 'packages/ui kit::'"
    assert TurboExecutor().command(ctx(package=odd)) == "turbo run test '--filter=@demo/ui kit'"
    assert NxExecutor().command(ctx(package=odd)) == "nx run '@demo/ui kit:test'"
    scoped = pkg(name="@demo/ui-kit")
    assert TurboExecutor().command(ctx(package=scoped)) == "turbo run test --filter=@demo/ui-kit"


def test_pants_shard_is_zero_based_and_comes_right_after_test():
    executor = PantsExecutor()
    assert executor.command(ctx("test", "unit", shard=Shard(1, 3))) == (
        "pants test --test-shard=0/3 packages/ui-kit::"
    )
    assert executor.command(ctx("test", shard=Shard(3, 3))) == (
        "pants test --test-shard=2/3 packages/ui-kit::"
    )


def test_pants_retries_count_attempts():
    assert PantsExecutor({"retries": 2}).command(ctx()) == (
        "pants test --test-attempts-default=3 packages/ui-kit::"
    )
    assert PantsExecutor({"retries": 0}).command(ctx()) == "pants test packages/ui-kit::"


def test_pants_shard_and_retries_together_on_test_only():
    executor = PantsExecutor({"retries": 1, "markers": True})
    assert executor.command(ctx("test", "unit", shard=Shard(2, 4))) == (
        'pants test --test-shard=1/4 --test-attempts-default=2 packages/ui-kit:: -- -m "unit"'
    )
    assert executor.command(ctx("lint", "unit", shard=Shard(2, 4))) == (
        "pants lint packages/ui-kit::"
    )


@pytest.mark.parametrize(
    ("name", "options", "message"),
    [
        ("turbo", {"retries": 1}, "executor 'turbo' has no retry option"),
        ("nx", {"retries": 2}, "executor 'nx' has no retry option"),
        ("pants", {"retries": 11}, "retries must be an integer from 0 to 10"),
        ("pants", {"selection": "impacted"}, "executor 'pants' has no selection option"),
        ("nx", {"launcher": ["nx"]}, "launcher must be a string"),
        ("pants", {"markers": "yes"}, "markers must be true or false"),
        ("pants", {"markers": 1}, "markers must be true or false"),
    ],
)
def test_option_errors_name_the_table(tmp_path, name, options, message):
    with pytest.raises(ConfigError, match=rf"\[executors\.{name}\] {message}"):
        make_cfg(
            tmp_path, {"name": "ui-kit", "path": "ui-kit", "executor": name}, **{name: options}
        )


# ── Through the planner ─────────────────────────────────────────────────────────────────────


def make_cfg(root: Path, *packages: dict, **executors: dict) -> cfgmod.Config:
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["packages"] = list(packages)
    if executors:
        raw["executors"] = executors
    return cfgmod.parse(copy.deepcopy(raw), root=root)


def commands(cfg, files, tier=1):
    plan = build_plan(cfg, tier, titles=["fix: x (ABC-1)"], changed_files=list(files))
    return plan, {s["id"]: s["command"] for s in plan["suites"]}


@pytest.mark.parametrize(
    ("executor", "lint", "test"),
    [
        ("pants", "pants lint ui-kit::", "pants test ui-kit::"),
        ("turbo", "turbo run lint --filter=ui-kit", "turbo run test --filter=ui-kit"),
        ("nx", "nx run ui-kit:lint", "nx run ui-kit:test"),
    ],
)
def test_planner_uses_the_tool(tmp_path, executor, lint, test):
    cfg = make_cfg(tmp_path, {"name": "ui-kit", "path": "ui-kit", "executor": executor})
    _, got = commands(cfg, ["ui-kit/src/index.js"])
    assert got == {"ui-kit:lint": lint, "ui-kit:test": test}


def test_an_explicit_command_still_wins(tmp_path):
    cfg = make_cfg(
        tmp_path,
        {
            "name": "ui-kit",
            "path": "ui-kit",
            "executor": "turbo",
            "commands": {"test": "make test-ui"},
        },
    )
    _, got = commands(cfg, ["ui-kit/src/index.js"])
    assert got == {"ui-kit:lint": "turbo run lint --filter=ui-kit", "ui-kit:test": "make test-ui"}


def test_planner_shards_and_retries_through_pants(tmp_path):
    cfg = make_cfg(
        tmp_path,
        {"name": "big", "path": "big", "executor": "pants", "shards": 2},
        pants={"launcher": "./pants", "retries": 1, "markers": True},
    )
    plan, got = commands(cfg, ["big/x.py"])
    pass_markers = ' -- -m "unit or integration"'  # tier 1's markers reach pytest
    assert got == {
        "big:lint": "./pants lint big::",
        "big:test#1/2": f"./pants test --test-shard=0/2 --test-attempts-default=2 big::{pass_markers}",
        "big:test#2/2": f"./pants test --test-shard=1/2 --test-attempts-default=2 big::{pass_markers}",
    }
    assert plan["warnings"] == []


@pytest.mark.parametrize("executor", ["turbo", "nx"])
def test_turbo_and_nx_cannot_shard_and_say_so(tmp_path, executor):
    cfg = make_cfg(tmp_path, {"name": "big", "path": "big", "executor": executor, "shards": 3})
    plan, got = commands(cfg, ["big/x.py"])
    assert set(got) == {"big:lint", "big:test"}
    assert plan["warnings"] == [
        f"package 'big' has shards = 3 but executor '{executor}' cannot shard 'test': "
        "it runs as one job"
    ]
