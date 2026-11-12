"""Tiers run in one go: a command that an earlier tier already ran is skipped, never run twice.

A suite is a repeat only when its id and its command line are both identical, so a test command
whose markers differ from tier to tier is different work and always runs.
"""

import json

import pytest

from picard.cli import main
from test_simulate import ENCODER, repo  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

TITLE = ["--title", "fix(abc): x (ABC-1)"]


def add(repo, text):  # noqa: F811
    config = repo / ".picard" / "pipeline.toml"
    config.write_text(config.read_text() + "\n" + text)


def once_rule(command):
    return (
        f'[actions.once]\nrun = "{command}"\n\n'
        '[[rules]]\nname = "encoder"\nmatch = ["packages/component-abc/**"]\n'
        'actions = ["run:once"]\n'
    )


def simulate_json(capsys, *extra):
    assert main(["simulate", "--files", ENCODER, *TITLE, "--format", "json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


# ── what is shown ──────────────────────────────────────────────────────────────────────


def test_a_repeated_lint_is_marked_and_a_test_with_other_markers_is_not(repo, capsys):  # noqa: F811
    assert main(["simulate", "--files", ENCODER, *TITLE, "--tiers", "0,1", "--commands"]) == 0
    text = capsys.readouterr().out
    tier0, tier1 = text.split("tier 0 (pre-push) commands:")[1].split("tier 1 (pr-fast-gate)")
    assert "skip" not in tier0
    assert "skip, already ran in tier 0: echo lint component-abc" in tier1
    assert 'echo test component-abc -m "unit or integration"' in tier1
    assert "already ran" not in tier1.split("echo test component-abc")[0].split("lint")[-1]


def test_the_tier_table_counts_only_the_new_work(repo, capsys):  # noqa: F811
    add(repo, once_rule("echo once"))
    assert main(["simulate", "--files", ENCODER, *TITLE, "--tiers", "0,1,2"]) == 0
    rows = {
        line.split()[0]: line
        for line in capsys.readouterr().out.splitlines()
        if line.split()[:1] in (["0"], ["1"], ["2"])
    }
    assert "new)" not in rows["0"]  # the first tier has nothing to repeat
    assert "1 suite(s) (0 new)" in rows["1"]
    assert "(1 new)" in rows["2"]  # the baseline package is new work, the rule action is not


def test_a_rule_action_is_listed_once_across_three_tiers(repo, capsys):  # noqa: F811
    add(repo, once_rule("echo once"))
    data = simulate_json(capsys, "--tiers", "0,1,2")
    assert data["repeats"]["1"]["rule:once"] == 0
    assert data["repeats"]["2"]["rule:once"] == 0  # the first tier that ran it, not the last
    assert "0" not in data["repeats"]


def test_text_output_says_which_tier_ran_it_first(repo, capsys):  # noqa: F811
    add(repo, once_rule("echo once"))
    assert main(["simulate", "--files", ENCODER, *TITLE, "--tiers", "0,1,2", "--commands"]) == 0
    text = capsys.readouterr().out
    assert text.count("skip, already ran in tier 0: echo once") == 2


def test_a_single_tier_has_no_repeats(repo, capsys):  # noqa: F811
    add(repo, once_rule("echo once"))
    assert simulate_json(capsys, "--tiers", "1")["repeats"] == {}


# ── what is run ────────────────────────────────────────────────────────────────────────


def test_run_executes_each_distinct_command_once(repo, tmp_path, capsys):  # noqa: F811
    hits = tmp_path / "hits"
    add(repo, once_rule(f"echo hit >> {hits}"))
    code = main(["simulate", "--files", ENCODER, *TITLE, "--tiers", "0,1,2", "--run"])
    assert code == 0
    assert hits.read_text().splitlines() == ["hit"]
    err = capsys.readouterr().err
    assert err.count("rule:once: skipped, already ran in tier 0") == 2


def test_run_still_runs_a_command_whose_markers_differ_between_tiers(repo, tmp_path, capsys):  # noqa: F811
    log = tmp_path / "log"
    config = repo / ".picard" / "pipeline.toml"
    config.write_text(
        config.read_text().replace(
            "test = 'echo test {package} -m \"{markers}\"'",
            f"test = 'echo \"{{package}} {{markers}}\" >> {log}'",
            1,
        )
    )
    assert main(["simulate", "--files", ENCODER, *TITLE, "--tiers", "0,1", "--run"]) == 0
    capsys.readouterr()
    lines = log.read_text().splitlines()
    assert len(lines) == 6  # three affected packages, once per tier: none was skipped
    assert "component-abc unit" in lines
    assert "component-abc unit or integration" in lines


def test_a_failed_command_stops_the_run_and_is_not_recorded(repo, capsys):  # noqa: F811
    add(repo, once_rule("false"))
    code = main(["simulate", "--files", ENCODER, *TITLE, "--tiers", "0,1", "--run"])
    assert code != 0
    err = capsys.readouterr().err
    assert "failed with exit code" in err
    assert "skipped" not in err


def test_a_dry_run_of_one_tier_is_unchanged(repo, capsys):  # noqa: F811
    add(repo, once_rule("echo once"))
    assert main(["run", "--tier", "1", "--files", ENCODER, *TITLE, "--dry-run"]) == 0
    err = capsys.readouterr().err
    assert "== rule:once: echo once" in err
    assert "skip" not in err
