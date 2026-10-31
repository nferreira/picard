"""A label that forces a full run, and packages that are always affected."""

import copy
import json
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import graph
from picard.cli import main
from picard.planner import build_plan, impact_of

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"
FORCED = "label ci:full forces a full run"


def raw_config(**labels) -> dict:
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["packages"] = [
        {"name": "core", "path": "packages/core", "commands": {"test": "pytest {path}"}},
        {
            "name": "api",
            "path": "apps/api",
            "depends_on": ["core"],
            "commands": {"test": "pytest {path}"},
        },
        {"name": "web", "path": "apps/web", "commands": {"test": "pnpm test"}},
        {"name": "db", "path": "db", "always": True, "commands": {"test": "migrate --check"}},
    ]
    if labels:
        raw["labels"] = labels
    return raw


@pytest.fixture(scope="module")
def cfg():
    return cfgmod.parse(copy.deepcopy(raw_config()))


def plan(cfg, tier, files, *, title="docs: readme (ABC-1)", **kw):
    return build_plan(cfg, tier, titles=[title], changed_files=files, **kw)


# ── forced full ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tier", [0, 1, 2])
def test_the_full_label_runs_every_package_at_the_strictest_profile(cfg, tier):
    forced = plan(cfg, tier, ["README.md"], labels=["ci:full", "other"])
    assert forced["profile"] == "full"  # rank 4, the strictest in the starter config
    assert not forced["skip"]  # tier 2 included: the docs profile alone would skip it
    assert forced["packages"] == ["core", "api", "web", "db"]
    assert forced["forced_full"] == [FORCED]
    assert FORCED in forced["reasons"]
    assert forced["labels"] == ["ci:full", "other"]
    assert set(forced["impact"]["affected"]) == {"core", "api", "web", "db"}
    assert forced["metrics"]["selected_pct"] == 100


def test_without_the_label_the_docs_change_runs_nothing(cfg):
    unforced = plan(cfg, 1, ["README.md"], labels=["other"])
    assert unforced["profile"] == "docs" and unforced["packages"] == []
    assert unforced["forced_full"] == []
    assert plan(cfg, 2, ["README.md"])["skip"]


def test_the_label_is_not_skipped_with_no_changed_files(cfg):
    assert plan(cfg, 1, [])["skip_reason"] == "no changed files"
    forced = plan(cfg, 1, [], labels=["ci:full"])
    assert not forced["skip"]
    assert forced["packages"] == ["core", "api", "web", "db"]
    assert forced["impact"]["everything"]


def test_forced_packages_are_decided_by_forced(cfg):
    impact = impact_of(cfg, ["apps/web/a.ts"], labels=["ci:full"])
    assert impact.everything
    assert set(impact.decided_by.values()) == {"forced"}
    assert graph.render_why(impact) == ["everything is affected"]


def test_full_reasons_force_a_full_run_too(cfg):
    reason = "base abc is not in this shallow clone and could not be fetched"
    forced = plan(cfg, 1, ["README.md"], full_reasons=[reason])
    assert forced["forced_full"] == [reason]
    assert forced["packages"] == ["core", "api", "web", "db"]
    assert reason in forced["reasons"]


@pytest.mark.parametrize("tier", [3, 4])
def test_a_label_changes_nothing_on_a_tier_that_ignores_the_diff(cfg, tier):
    with_label = plan(cfg, tier, ["README.md"], labels=["ci:full"])
    without = plan(cfg, tier, ["README.md"])
    assert with_label["labels"] == ["ci:full"] and with_label["forced_full"] == []
    assert {**with_label, "labels": []} == without


@pytest.mark.parametrize(
    ("labels", "given", "forced"),
    [
        ({"full": ""}, ["ci:full"], False),  # disabled
        ({"full": ""}, [""], False),  # an empty label never matches a disabled one
        ({"full": "run-all"}, ["ci:full"], False),
        ({"full": "run-all"}, ["run-all"], True),
        ({}, ["CI:FULL"], False),  # label names are matched exactly
    ],
)
def test_the_full_label_setting(labels, given, forced):
    cfg = cfgmod.parse(raw_config(**labels))
    result = plan(cfg, 1, ["README.md"], labels=given)
    assert bool(result["forced_full"]) is forced
    assert (result["packages"] == ["core", "api", "web", "db"]) is forced


# ── always ─────────────────────────────────────────────────────────────────────────────


def test_an_always_package_joins_every_diff_tier(cfg):
    for tier in (0, 1, 2):
        result = plan(cfg, tier, ["apps/web/a.ts"], title="fix: x (ABC-1)")
        assert "db" in result["packages"], tier
        assert result["impact"]["affected"]["db"] == ["db"]
        assert "db is always affected (always = true)" in result["impact"]["why"]


def test_always_is_in_the_affected_set(cfg):
    impact = impact_of(cfg, ["apps/web/a.ts"])
    assert list(impact.affected) == ["web", "db"]
    assert impact.decided_by == {"web": "native", "db": "always"}
    assert impact.changed == ("web",)
    files = ["apps/web/a.ts"]
    assert plan(cfg, 1, files, title="fix: x (ABC-1)")["packages"] == list(
        impact_of(cfg, files).affected
    )


def test_a_change_to_an_always_package_keeps_the_graph_answer(cfg):
    impact = impact_of(cfg, ["db/0001.sql"])
    assert impact.decided_by["db"] == "native"
    assert graph.render_why(impact) == ["db owns a changed file (via native)"]


def test_always_does_not_stop_the_no_changed_files_skip(cfg):
    result = plan(cfg, 1, [], title="fix: x (ABC-1)")
    assert result["skip"] and result["packages"] == []


def test_always_on_a_tier_that_runs_everything(cfg):
    result = plan(cfg, 3, ["apps/web/a.ts"], title="fix: x (ABC-1)")
    assert result["packages"] == ["core", "api", "web", "db"]
    assert result["impact"] is None


def test_always_does_not_override_a_profile_that_runs_no_suites(cfg):
    assert plan(cfg, 1, ["README.md"])["packages"] == []


# ── the CLI: --label and PICARD_LABELS ─────────────────────────────────────────────────


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PICARD_LABELS", raising=False)
    monkeypatch.delenv("SMARTPLAN_LABELS", raising=False)
    path = tmp_path / ".picard" / "pipeline.toml"
    path.parent.mkdir()
    path.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def cli_plan(capsys, *extra) -> dict:
    args = ["plan", "--tier", "1", "--files", "README.md", "--title", "docs: x (ABC-1)", *extra]
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    ("env", "labels"),
    [
        ('["ci:full", "needs review"]', ["ci:full", "needs review"]),  # what toJSON() gives
        ("[]", []),
        ("ci:full, needs review ,,", ["ci:full", "needs review"]),
        ("ci:full", ["ci:full"]),
        ("", []),
        ('{"name": "ci:full"}', ['{"name": "ci:full"}']),  # not a list: read as one name
    ],
)
def test_picard_labels(config_file, capsys, monkeypatch, env, labels):
    monkeypatch.setenv("PICARD_LABELS", env)
    result = cli_plan(capsys)
    assert result["labels"] == labels
    assert bool(result["forced_full"]) is ("ci:full" in labels)


def test_label_arguments_win_over_the_environment(config_file, capsys, monkeypatch):
    monkeypatch.setenv("PICARD_LABELS", '["ci:full"]')
    result = cli_plan(capsys, "--label", "other", "--label", "more")
    assert result["labels"] == ["other", "more"] and result["forced_full"] == []
    assert cli_plan(capsys, "--label", "ci:full")["packages"] == ["app"]


def test_simulate_previews_the_label(config_file, capsys):
    assert main(["simulate", "--files", "README.md", "--label", "ci:full", "--tiers", "1"]) == 0
    out = capsys.readouterr().out
    why = out.split("WHY")[1].split("TIERS")[0]
    assert [line.strip() for line in why.strip().splitlines()] == [
        "everything is affected",
        FORCED,
    ]
    assert "app" in out.split("TIERS")[1]


def test_graph_shows_the_always_package(config_file, capsys):
    text = config_file.read_text(encoding="utf-8")
    text = text.replace('name = "app"\npath = "."\n', 'name = "app"\npath = "app"\nalways = true\n')
    config_file.write_text(text, encoding="utf-8")
    assert main(["graph", "--files", "README.md"]) == 0
    assert "app is always affected (always = true)" in capsys.readouterr().out
