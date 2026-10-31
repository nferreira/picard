"""Built-in executor flags: shards, retries, test selection and the `deps` suite.

The tools (pytest-split, pytest-rerunfailures, pytest-impacted, deptry, knip, Jest) are never run
here: the tests pin the exact commands picard hands them.
"""

import copy
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard.config import Package
from picard.executors import Shard, SuiteContext
from picard.executors.builtin import BUILTIN_EXECUTORS
from picard.graph import Revisions
from picard.planner import build_plan

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"
BASE = "d" * 40


def pkg(name="svc", path="svc") -> Package:
    return Package(name, path, (), False, {})


def ctx(package=None, suite="test", markers="", shard=None, base=None) -> SuiteContext:
    return SuiteContext(package or pkg(), suite, "pr", markers, shard, base)


def build(name, **options):
    return BUILTIN_EXECUTORS[name](options)


# ── what each executor can do ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "shardable", "retryable", "selections"),
    [
        ("pytest", {"test"}, True, ("impacted",)),
        ("uv-pytest", {"test"}, True, ("impacted",)),
        ("pnpm", {"test"}, False, ("changed-since",)),
        ("npm", {"test"}, False, ("changed-since",)),
        ("go-test", set(), False, ()),
        ("cargo-test", set(), False, ()),
        ("make", set(), False, ()),
    ],
)
def test_capabilities(name, shardable, retryable, selections):
    cls = BUILTIN_EXECUTORS[name]
    assert (cls.shardable, cls.retryable, cls.selections) == (
        frozenset(shardable),
        retryable,
        selections,
    )


# ── pytest and uv-pytest ───────────────────────────────────────────────────────────────

IMPACTED = (
    f" --impacted --impacted-module=svc --impacted-git-mode=branch --impacted-base-branch={BASE}"
)


@pytest.mark.parametrize(
    ("options", "shard", "base", "expected"),
    [
        ({}, None, None, 'pytest svc -m "unit"'),
        ({"retries": 2}, None, None, 'pytest svc -m "unit" --reruns 2'),
        ({"retries": 0}, None, None, 'pytest svc -m "unit"'),
        ({}, Shard(2, 4), None, 'pytest svc -m "unit" --splits 4 --group 2'),
        ({"selection": "impacted"}, None, BASE, 'pytest svc -m "unit"' + IMPACTED),
        (
            {"retries": 3, "selection": "impacted"},
            Shard(1, 2),
            BASE,
            'pytest svc -m "unit" --reruns 3 --splits 2 --group 1' + IMPACTED,
        ),
        # Fail closed: selection without a base, or a base without the option, adds nothing.
        ({"selection": "impacted"}, None, None, 'pytest svc -m "unit"'),
        ({}, None, BASE, 'pytest svc -m "unit"'),
    ],
)
def test_pytest_test_flags(options, shard, base, expected):
    assert build("pytest", **options).command(ctx(markers="unit", shard=shard, base=base)) == (
        expected
    )


def test_uv_pytest_keeps_its_launcher_with_every_flag():
    ex = build("uv-pytest", retries=1, selection="impacted")
    assert ex.command(ctx(markers="unit", shard=Shard(3, 3), base=BASE)) == (
        'uv run --no-sync pytest svc -m "unit" --reruns 1 --splits 3 --group 3' + IMPACTED
    )


@pytest.mark.parametrize("suite", ["lint", "deps"])
def test_pytest_flags_apply_to_the_test_suite_only(suite):
    ex = build("pytest", retries=2, selection="impacted")
    got = ex.command(ctx(suite=suite, shard=Shard(1, 2), base=BASE))
    assert "--reruns" not in got and "--splits" not in got and "--impacted" not in got


def test_pytest_quotes_the_module_the_path_and_the_base():
    ex = build("pytest", selection="impacted")
    got = ex.command(ctx(pkg("my.pkg-x", "apps/a b"), base="feature; rm -rf /"))
    assert got == (
        "pytest 'apps/a b' --impacted --impacted-module=my_pkg_x --impacted-git-mode=branch"
        " --impacted-base-branch='feature; rm -rf /'"
    )


@pytest.mark.parametrize(
    ("name", "options", "expected"),
    [
        ("pytest", {}, "deptry svc"),
        ("uv-pytest", {}, "uv run --no-sync deptry svc"),
        ("pytest", {"launcher": "poetry run"}, "poetry run deptry svc"),
        ("uv-pytest", {"deps_checker": "deptry --strict"}, "uv run --no-sync deptry --strict svc"),
    ],
)
def test_pytest_deps_suite(name, options, expected):
    assert build(name, **options).command(ctx(suite="deps")) == expected


def test_pytest_deps_quotes_the_path():
    assert build("pytest").command(ctx(pkg(path="a b"), suite="deps")) == "deptry 'a b'"


# ── pnpm and npm ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "options", "markers", "shard", "base", "expected"),
    [
        ("pnpm", {}, "", None, None, "pnpm --filter svc run test"),
        ("pnpm", {}, "", Shard(2, 3), None, "pnpm --filter svc run test -- --shard=2/3"),
        (
            "pnpm",
            {"selection": "changed-since"},
            "",
            None,
            BASE,
            f"pnpm --filter svc run test -- --changedSince={BASE}",
        ),
        (
            "pnpm",
            {"selection": "changed-since"},
            "",
            Shard(1, 2),
            BASE,
            f"pnpm --filter svc run test -- --shard=1/2 --changedSince={BASE}",
        ),
        (
            "pnpm",
            {},
            "baseline",
            Shard(1, 2),
            None,
            "pnpm --filter svc run test:baseline -- --shard=1/2",
        ),
        ("pnpm", {"selection": "changed-since"}, "", None, None, "pnpm --filter svc run test"),
        ("pnpm", {}, "", None, BASE, "pnpm --filter svc run test"),
        ("npm", {}, "", None, None, "npm run test --workspace svc"),
        ("npm", {}, "", Shard(4, 4), None, "npm run test --workspace svc -- --shard=4/4"),
        (
            "npm",
            {"selection": "changed-since"},
            "",
            Shard(1, 2),
            BASE,
            f"npm run test --workspace svc -- --shard=1/2 --changedSince={BASE}",
        ),
        ("npm", {"selection": "changed-since"}, "", None, None, "npm run test --workspace svc"),
    ],
)
def test_js_test_flags(name, options, markers, shard, base, expected):
    got = build(name, **options).command(ctx(markers=markers, shard=shard, base=base))
    assert got == expected
    assert got.count(" -- ") <= 1


@pytest.mark.parametrize("name", ["pnpm", "npm"])
def test_js_flags_apply_to_the_test_suite_only(name):
    ex = build(name, selection="changed-since")
    for suite in ("lint", "deps"):
        got = ex.command(ctx(suite=suite, shard=Shard(1, 2), base=BASE))
        assert "--shard" not in got and "--changedSince" not in got


def test_js_quotes_the_name_and_the_base():
    ex = build("pnpm", selection="changed-since")
    got = ex.command(ctx(pkg("@demo/ui kit", "packages/ui"), base="a b"))
    assert got == "pnpm --filter '@demo/ui kit' run test -- --changedSince='a b'"


@pytest.mark.parametrize(
    ("name", "options", "expected"),
    [
        ("pnpm", {}, "pnpm exec knip --include dependencies,unlisted --workspace 'apps/a b'"),
        ("pnpm", {"deps_checker": "knip"}, "pnpm exec knip --workspace 'apps/a b'"),
        ("npm", {}, "npx --no-install knip --include dependencies,unlisted --workspace 'apps/a b'"),
    ],
)
def test_js_deps_suite(name, options, expected):
    assert build(name, **options).command(ctx(pkg(path="apps/a b"), suite="deps")) == expected


@pytest.mark.parametrize("name", ["go-test", "cargo-test"])
def test_executors_without_a_deps_checker(name):
    assert build(name).command(ctx(suite="deps")) is None


# ── option errors, through the config ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "options", "message"),
    [
        ("pytest", {"retries": 11}, "retries must be an integer from 0 to 10"),
        ("uv-pytest", {"retries": True}, "retries must be an integer from 0 to 10"),
        ("pytest", {"selection": "changed-since"}, "selection must be one of: impacted"),
        ("pnpm", {"retries": 2}, "executor 'pnpm' has no retry option"),
        ("npm", {"retries": 1}, "executor 'npm' has no retry option"),
        ("pnpm", {"selection": "related"}, "selection must be one of: changed-since"),
        ("npm", {"selection": "impacted"}, "selection must be one of: changed-since"),
        ("go-test", {"selection": "impacted"}, "executor 'go-test' has no selection option"),
        ("cargo-test", {"retries": 3}, "executor 'cargo-test' has no retry option"),
        ("pnpm", {"deps_checker": ["knip"]}, "deps_checker must be a string"),
    ],
)
def test_option_errors(name, options, message):
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["executors"] = {name: options}
    with pytest.raises(cfgmod.ConfigError) as err:
        cfgmod.parse(raw)
    assert str(err.value) == f"[executors.{name}] {message}"


def test_valid_options_parse():
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["executors"] = {
        "uv-pytest": {"retries": 0, "selection": "impacted", "deps_checker": "deptry"},
        "pnpm": {"selection": "changed-since"},
    }
    cfg = cfgmod.parse(raw)
    assert cfg.executor_options["pnpm"] == {"selection": "changed-since"}


# ── through the planner: selection only with a committed range ─────────────────────────


@pytest.fixture
def cfg():
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["packages"] = [
        {"name": "some-api", "path": "apps/some-api", "executor": "uv-pytest", "shards": 2},
        {"name": "@demo/ui-kit", "path": "packages/ui-kit", "executor": "pnpm"},
    ]
    raw["executors"] = {
        "uv-pytest": {"retries": 2, "selection": "impacted"},
        "pnpm": {"selection": "changed-since"},
    }
    raw["tiers"]["0"]["selection"] = True
    raw["tiers"]["1"]["selection"] = True
    raw["tiers"]["1"]["run"] = ["lint", "test", "deps"]
    return cfgmod.parse(copy.deepcopy(raw))


FILES = ["apps/some-api/src/some_api/handlers.py", "packages/ui-kit/src/button.ts"]


def commands(cfg, tier=1, **kw):
    plan = build_plan(cfg, tier, titles=["fix: x (ABC-1)"], changed_files=FILES, **kw)
    return plan, {s["id"]: s["command"] for s in plan["suites"]}


def test_a_committed_range_selects_tests(cfg):
    plan, got = commands(cfg, revisions=Revisions(BASE, "HEAD"))
    impacted = (
        " --impacted --impacted-module=some_api --impacted-git-mode=branch"
        f" --impacted-base-branch={BASE}"
    )
    assert got == {
        "some-api:lint": "uv run --no-sync ruff check apps/some-api",
        "some-api:test#1/2": 'uv run --no-sync pytest apps/some-api -m "unit or integration"'
        " --reruns 2 --splits 2 --group 1" + impacted,
        "some-api:test#2/2": 'uv run --no-sync pytest apps/some-api -m "unit or integration"'
        " --reruns 2 --splits 2 --group 2" + impacted,
        "some-api:deps": "uv run --no-sync deptry apps/some-api",
        "@demo/ui-kit:lint": "pnpm --filter @demo/ui-kit run lint",
        "@demo/ui-kit:test": f"pnpm --filter @demo/ui-kit run test -- --changedSince={BASE}",
        "@demo/ui-kit:deps": (
            "pnpm exec knip --include dependencies,unlisted --workspace packages/ui-kit"
        ),
    }
    assert plan["warnings"] == []


@pytest.mark.parametrize(
    "kw",
    [
        pytest.param({}, id="no-git-range"),
        pytest.param(
            {"revisions": Revisions(BASE, "HEAD", ("apps/some-api/wip.py",))}, id="uncommitted"
        ),
        pytest.param({"revisions": Revisions(BASE, "HEAD"), "labels": ["ci:full"]}, id="label"),
        pytest.param(
            {"revisions": Revisions(BASE, "HEAD"), "full_reasons": ["base unknown"]},
            id="full-reason",
        ),
    ],
)
def test_without_a_committed_range_whole_suites_run(cfg, kw):
    """Fail closed: selection narrows inside a suite, so with no trustworthy base it is off."""
    _, got = commands(cfg, **kw)
    joined = "\n".join(got.values())
    assert "--impacted" not in joined and "--changedSince" not in joined
    assert got["@demo/ui-kit:test"] == "pnpm --filter @demo/ui-kit run test"


def test_a_tier_that_does_not_select_runs_whole_suites(cfg):
    _, got = commands(cfg, tier=2, revisions=Revisions(BASE, "HEAD"))
    assert not any("--impacted" in c or "--changedSince" in c for c in got.values())


def test_a_local_run_keeps_one_suite_with_retries(cfg):
    _, got = commands(cfg, tier=0, expand_shards=False)
    assert got["some-api:test"] == ('uv run --no-sync pytest apps/some-api -m "unit" --reruns 2')
