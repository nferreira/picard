"""The plan as a PR comment (`--comment`) and the metrics file (`--metrics`)."""

import json
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import report
from picard.cli import main
from picard.planner import build_plan, render_markdown

pytestmark = pytest.mark.unit

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"
ZWSP = "\u200b"


@pytest.fixture(scope="module")
def cfg():
    return cfgmod.load(TEMPLATE)


def test_comment_is_the_markdown_summary(cfg):
    plan = build_plan(cfg, 1, titles=["fix: x (ABC-1)"], changed_files=["src/a.py"])
    assert report.render_comment(plan) == render_markdown(plan)  # nothing to escape


def test_every_at_sign_is_defused():
    plan = {
        "tier": 1,
        "name": "pr-fast-gate",
        "skip": True,
        "skip_reason": "ask @someone and @org/team",
    }
    comment = report.render_comment(plan)
    assert "@someone" not in comment and "@org/team" not in comment
    assert f"@{ZWSP}someone" in comment and f"@{ZWSP}org/team" in comment
    assert comment.replace(ZWSP, "") == render_markdown(plan)
    assert "<!-- picard-plan -->" not in comment  # the workflow adds the sticky marker


def test_metrics_shape(cfg):
    plan = build_plan(cfg, 1, titles=["fix: x (ABC-1)"], changed_files=["src/a.py"])
    data = report.metrics(plan)
    assert list(data) == [
        "schema",
        "tier",
        "packages_total",
        "packages_selected",
        "suites_total",
        "suites_selected",
        "jobs",
        "selected_pct",
    ]
    assert data["schema"] == 1 and data["tier"] == 1
    assert all(isinstance(v, int) for v in data.values())
    assert json.loads(report.render_metrics(plan)) == data
    assert " " not in report.render_metrics(plan)


def test_metrics_of_a_skipped_tier_keep_the_shape(cfg):
    plan = build_plan(cfg, 2, titles=["docs: x (ABC-1)"], changed_files=["README.md"])
    assert plan["skip"]
    data = report.metrics(plan)
    assert data["packages_selected"] == 0 and data["jobs"] == 0


def test_plan_writes_comment_and_metrics(tmp_path, capsys):
    comment = tmp_path / "plan.md"
    metrics = tmp_path / "metrics.json"
    comment.write_text("earlier\n", encoding="utf-8")
    metrics.write_text("stale", encoding="utf-8")
    args = ["plan", "--config", str(TEMPLATE), "--files", "src/a.py", "--title", "fix: @x (A-1)"]
    for tier in ("1", "2"):
        assert (
            main([*args, "--tier", tier, "--comment", str(comment), "--metrics", str(metrics)]) == 0
        )
    capsys.readouterr()
    text = comment.read_text(encoding="utf-8")
    assert text.startswith("earlier\n")  # appended, one section per tier
    assert "tier 1 · pr-fast-gate" in text and "tier 2 · pr-baseline" in text
    data = json.loads(metrics.read_text(encoding="utf-8"))  # overwritten, the last tier
    assert data["tier"] == 2 and data["schema"] == 1
