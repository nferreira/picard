"""Shards: one suite of a big package runs as several matrix jobs in CI, and once locally."""

import copy
import json
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard.cli import main
from picard.planner import build_plan

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"

PLUGIN = """\
from picard.executors import Executor


class Split(Executor):
    \"\"\"A runner with a split flag, like pytest-split or jest --shard.\"\"\"

    name = "split"
    shardable = frozenset({"test"})

    def command(self, ctx):
        if ctx.suite == "lint":
            return f"split-lint {ctx.package.path}"
        part = f" --part {ctx.shard.index}/{ctx.shard.total}" if ctx.shard else ""
        return f"split-test {ctx.package.path}{part}"


class Whole(Executor):
    \"\"\"A runner that cannot split.\"\"\"

    name = "whole"

    def command(self, ctx):
        return f"whole-{ctx.suite} {ctx.package.path}"


def register(registries):
    registries.executors.register("split", Split)
    registries.executors.register("whole", Whole)
"""


def make_cfg(root: Path, *packages: dict) -> cfgmod.Config:
    (root / "split.py").write_text(PLUGIN, encoding="utf-8")
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["plugins"] = {"paths": ["split.py"]}
    raw["packages"] = list(packages)
    return cfgmod.parse(copy.deepcopy(raw), root=root)


def suites(cfg, files=("big/x.py",), tier=1, **kw):
    plan = build_plan(cfg, tier, titles=["fix: x (ABC-1)"], changed_files=list(files), **kw)
    return plan, [(s["id"], s["command"], s.get("shard"), s.get("shards")) for s in plan["suites"]]


def test_a_shardable_executor_expands_the_suite(tmp_path):
    cfg = make_cfg(tmp_path, {"name": "big", "path": "big", "executor": "split", "shards": 3})
    plan, got = suites(cfg)
    assert got == [
        ("big:lint", "split-lint big", None, None),  # lint is not shardable: one job, no warning
        ("big:test#1/3", "split-test big --part 1/3", 1, 3),
        ("big:test#2/3", "split-test big --part 2/3", 2, 3),
        ("big:test#3/3", "split-test big --part 3/3", 3, 3),
    ]
    assert plan["warnings"] == []
    assert plan["metrics"]["suites_selected"] == 2 and plan["metrics"]["jobs"] == 4
    assert plan["metrics"]["suites_total"] == 2


def test_an_explicit_command_with_a_shard_placeholder(tmp_path):
    test = "run {path} --shard={shard}/{shards}"
    cfg = make_cfg(
        tmp_path, {"name": "big", "path": "big", "shards": 2, "commands": {"test": test}}
    )
    plan, got = suites(cfg)
    assert got == [
        ("big:test#1/2", "run big --shard=1/2", 1, 2),
        ("big:test#2/2", "run big --shard=2/2", 2, 2),
    ]
    assert plan["warnings"] == []


@pytest.mark.parametrize(
    ("package", "warning"),
    [
        (
            {"executor": "whole"},
            "package 'big' has shards = 4 but executor 'whole' cannot shard 'test': "
            "it runs as one job",
        ),
        (
            {"commands": {"test": "run {path}"}},
            "package 'big' has shards = 4 but its 'test' command has no {shard} placeholder: "
            "it runs as one job",
        ),
    ],
)
def test_a_suite_that_cannot_shard_runs_as_one_job_and_says_so(tmp_path, package, warning):
    cfg = make_cfg(tmp_path, {"name": "big", "path": "big", "shards": 4, **package})
    plan, got = suites(cfg)
    assert [i for i, *_ in got if i.startswith("big:test")] == ["big:test"]
    assert plan["warnings"].count(warning) == 1


def test_lint_never_warns_about_shards(tmp_path):
    cfg = make_cfg(
        tmp_path,
        {"name": "big", "path": "big", "shards": 4, "commands": {"lint": "lint {path}"}},
    )
    plan, got = suites(cfg)
    assert got == [("big:lint", "lint big", None, None)] and plan["warnings"] == []


@pytest.mark.parametrize(
    ("package", "command"),
    [
        ({"executor": "split", "shards": 3}, "split-test big"),
        ({"commands": {"test": "run --shard={shard}/{shards}"}, "shards": 3}, "run --shard=1/1"),
        ({"commands": {"test": "run --shard={shard}/{shards}"}}, "run --shard=1/1"),  # shards = 1
    ],
)
def test_unexpanded_suites_run_whole(tmp_path, package, command):
    cfg = make_cfg(tmp_path, {"name": "big", "path": "big", **package})
    expand = package.get("shards", 1) == 1  # shards = 1 never expands, whatever the flag
    plan, got = suites(cfg, expand_shards=expand)
    assert [(i, c) for i, c, *_ in got if i.startswith("big:test")] == [("big:test", command)]
    assert plan["warnings"] == []


def test_over_the_matrix_limit_warns_and_does_not_trim(tmp_path):
    cfg = make_cfg(
        tmp_path,
        {"name": "a", "path": "a", "shards": 200, "commands": {"test": "t {shard}"}},
        {"name": "b", "path": "b", "shards": 100, "commands": {"test": "t {shard}"}},
    )
    plan, got = suites(cfg, files=["a/x", "b/x"])
    assert len(got) == 300
    assert (
        "tier 1 has 300 jobs, over GitHub's 256-job matrix limit: the workflow will fail"
        in (plan["warnings"])
    )
    assert plan["metrics"]["jobs"] == 300 and plan["metrics"]["suites_selected"] == 2


def test_exactly_the_limit_does_not_warn(tmp_path):
    cfg = make_cfg(
        tmp_path, {"name": "a", "path": "a", "shards": 256, "commands": {"test": "{shard}"}}
    )
    plan, got = suites(cfg, files=["a/x"])
    assert len(got) == 256 and not any("matrix limit" in w for w in plan["warnings"])


# ── the CLI: simulate shows shards, run runs each suite once ───────────────────────────


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "split.py").write_text(PLUGIN, encoding="utf-8")
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index('[[packages]]\nname = "app"')
    end = text.index("# A monorepo, packages listed by hand")
    package = (
        '[plugins]\npaths = ["split.py"]\n\n[[packages]]\nname = "big"\npath = "big"\nshards = 3\n'
        '[packages.commands]\nlint = "echo lint"\ntest = "echo shard {shard} of {shards}"\n\n'
    )
    (tmp_path / ".picard").mkdir()
    (tmp_path / ".picard" / "pipeline.toml").write_text(
        text[:start] + package + text[end:], encoding="utf-8"
    )
    return tmp_path


CHANGE = ["--files", "big/x.py", "--title", "fix: x (ABC-1)"]


def test_plan_expands_shards(project, capsys):
    assert main(["plan", "--tier", "1", *CHANGE]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert [s["id"] for s in plan["suites"]] == [
        "big:lint",
        "big:test#1/3",
        "big:test#2/3",
        "big:test#3/3",
    ]


def test_run_runs_one_command_per_suite(project, capfd):
    assert main(["run", "--tier", "1", *CHANGE]) == 0
    captured = capfd.readouterr()
    assert captured.out.splitlines() == ["lint", "shard 1 of 1"]
    assert "big:test#" not in captured.err


def test_simulate_shows_shards_and_runs_them_whole(project, capfd):
    assert main(["simulate", "--tiers", "1", "--commands", "--run", *CHANGE]) == 0
    captured = capfd.readouterr()
    assert "big:test#2/3" in captured.out
    assert "echo shard 2 of 3" in captured.out
    assert captured.out.splitlines()[-2:] == ["lint", "shard 1 of 1"]
