"""simulate --pr: merged pull requests as a source of changes, with gh faked."""

import json

import pytest

from picard import prs
from picard.cli import main
from test_simulate import ENCODER, repo  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

LISTING = [
    {
        "number": 5,
        "title": "docs: readme (ABC-5)",
        "mergedAt": "2026-10-01T10:00:00Z",
        "baseRefName": "main",
    },
    {
        "number": 9,
        "title": "fix(abc): encoder (ABC-9)",
        "mergedAt": "2026-10-03T10:00:00Z",
        "baseRefName": "main",
    },
]
FILES = {5: ["README.md"], 9: [ENCODER]}


@pytest.fixture
def fake_gh(monkeypatch):
    def gh(*args):
        if args[:2] == ("pr", "list"):
            return json.dumps(LISTING)
        if args[:2] == ("pr", "view"):
            n = int(args[2])
            title = next(r["title"] for r in LISTING if r["number"] == n)
            return json.dumps({"title": title, "body": "", "state": "MERGED"})
        if args[0] == "api":
            n = int(args[2].split("/")[-2])
            return "\n".join(FILES[n]) + "\n"
        raise AssertionError(args)

    monkeypatch.setattr(prs, "_gh", gh)


def test_named_pr_is_planned_from_its_files_and_title(repo, fake_gh, capsys):  # noqa: F811
    assert main(["simulate", "--pr", "9", "--tiers", "1", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["source"] == "merged PR #9"
    assert data["files"] == [ENCODER]
    assert data["plans"]["1"]["release"] == "patch"
    assert "some-api" in data["plans"]["1"]["packages"]


def test_docs_pr_runs_no_suites(repo, fake_gh, capsys):  # noqa: F811
    main(["simulate", "--pr", "5", "--tiers", "1", "--format", "json"])
    plan = json.loads(capsys.readouterr().out)["plans"]["1"]
    assert plan["suites"] == []


def test_prompt_lists_newest_first_and_accepts_several(repo, fake_gh, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr("builtins.input", lambda _prompt: "5, #9")
    assert main(["simulate", "--pr", "--tiers", "1", "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert captured.err.index("#9") < captured.err.index("#5")
    data = json.loads(captured.out)
    assert data["source"] == "merged PR #5, #9"
    assert set(data["files"]) == {"README.md", ENCODER}


def test_blank_choice_cancels(repo, fake_gh, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert main(["simulate", "--pr"]) == 2
    assert "no pull request selected" in capsys.readouterr().err


def test_a_number_outside_the_list_is_refused(repo, fake_gh, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr("builtins.input", lambda _prompt: "42")
    assert main(["simulate", "--pr"]) == 2
    assert "#42 is not in the list" in capsys.readouterr().err


def test_explicit_title_still_wins(repo, fake_gh, capsys):  # noqa: F811
    main(
        [
            "simulate",
            "--pr",
            "9",
            "--tiers",
            "1",
            "--title",
            "feat(abc): x (ABC-1)",
            "--format",
            "json",
        ]
    )
    assert json.loads(capsys.readouterr().out)["plans"]["1"]["release"] == "minor"
