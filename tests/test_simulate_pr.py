"""picard simulate --pr: plan a pull request from what CI would see, read with `gh`."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from picard import pr as prmod
from picard.cli import main
from picard.pr import PullRequest, PullRequestError

pytestmark = [pytest.mark.integration, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"
PACKAGES_BLOCK = re.compile(r"\[\[packages\]\]\nname = \"app\".*?\ntest = .*?\n", re.DOTALL)
TWO_PACKAGES = (
    '[[packages]]\nname = "a"\npath = "a"\n[packages.commands]\ntest = "true"\n\n'
    '[[packages]]\nname = "b"\npath = "b"\ndepends_on = ["a"]\n[packages.commands]\ntest = "true"\n'
)
SHA = "0123456789abcdef0123456789abcdef01234567"


def git(repo, *args):
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def commit(repo, path, text, message):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    config = PACKAGES_BLOCK.sub(lambda _: TWO_PACKAGES, TEMPLATE.read_text("utf-8"), count=1)
    commit(tmp_path, ".picard/pipeline.toml", config, "chore: config (T-1)")
    commit(tmp_path, "a/x.py", "x = 1\n", "feat: a (T-2)")
    commit(tmp_path, "b/y.py", "y = 1\n", "feat: b (T-3)")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def pull_request(repo, *, title="fix(a): guard the input (T-9)", labels=(), body="", files=("a",)):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    head = base
    for name in files:
        head = commit(repo, f"{name}/change.py", "z = 2\n", "wip")
    return PullRequest(7, title, body, tuple(labels), "main", base, "feature", head, "u", "OPEN")


@pytest.fixture
def served(monkeypatch):
    """Make `gh` answer with the given pull request, and remember how it was asked."""
    asked = []

    def serve(found):
        def fetch(spec=""):
            asked.append(spec)
            return found

        monkeypatch.setattr(prmod, "fetch", fetch)
        return asked

    return serve


def simulate(capsys, *extra):
    code = main(["simulate", "--pr", "7", "--format", "json", "--tiers", "1", *extra])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip().startswith("{") else None), err


# ── a pull request is planned from its own title, labels and commits ──────────────────


def test_the_plan_comes_from_the_pull_requests_title_and_commits(repo, served, capsys):
    served(pull_request(repo))
    code, data, _ = simulate(capsys)
    assert code == 0
    assert data["source"] == "PR #7 main...feature (open)"
    assert data["titles"] == ["fix(a): guard the input (T-9)"]
    assert data["files"] == ["a/change.py"]
    assert data["plans"]["1"]["packages"] == ["a", "b"]  # b depends on a


def test_a_docs_title_cannot_lower_what_the_paths_demand(repo, served, capsys):
    served(pull_request(repo, title="docs: notes (T-9)"))
    _, data, _ = simulate(capsys)
    assert data["plans"]["1"]["profile"] == "full"


def test_the_pull_requests_labels_count(repo, served, capsys):
    served(pull_request(repo, labels=["ci:full"], files=("b",)))
    _, data, _ = simulate(capsys)
    assert data["plans"]["1"]["packages"] == ["a", "b"]  # a change to b alone would run b only


def test_label_adds_to_the_pull_requests_labels(repo, served, capsys):
    served(pull_request(repo, files=("b",)))
    _, plain, _ = simulate(capsys)
    assert plain["plans"]["1"]["packages"] == ["b"]
    _, forced, _ = simulate(capsys, "--label", "ci:full")
    assert forced["plans"]["1"]["packages"] == ["a", "b"]


def test_title_replaces_the_pull_requests_title(repo, served, capsys):
    served(pull_request(repo, title="docs: notes (T-9)"))
    _, data, _ = simulate(capsys, "--title", "feat(a): new thing (T-10)")
    assert data["titles"] == ["feat(a): new thing (T-10)"]


# ── without a number: list the open pull requests and choose ──────────────────────────


def summary(number, title, branch, *, author="dev", draft=False, state="OPEN"):
    return prmod.PullRequestSummary(number, title, branch, author, draft, state)


@pytest.fixture
def open_prs(monkeypatch):
    """Three pull requests of every state; the checked-out branch's (`feature`) is second."""
    items = [
        summary(41, "feat(b): other work (T-4)", "other"),
        summary(7, "fix(a): guard the input (T-9)", "feature"),
        summary(40, "chore: bump (T-5)", "bump", draft=True, state="MERGED"),
        summary(39, "fix: old (T-3)", "old", state="CLOSED"),
    ]
    monkeypatch.setattr(prmod, "list_recent", lambda limit=prmod.LIST_LIMIT: items)
    monkeypatch.setattr(prmod, "interactive", lambda: True)
    return items


def answers(monkeypatch, *given):
    queue = list(given)
    monkeypatch.setattr("builtins.input", lambda: queue.pop(0))
    return queue


def bare(capsys, *extra):
    code = main(["simulate", "--pr", "--format", "json", "--tiers", "1", *extra])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip().startswith("{") else None), err


def test_without_a_number_it_lists_pull_requests_and_runs_the_chosen_one(
    repo, served, open_prs, monkeypatch, capsys
):
    asked = served(pull_request(repo))
    answers(monkeypatch, "3")
    code, data, err = bare(capsys)
    assert code == 0
    assert asked == ["40"]  # the third line of the list, a merged pull request
    assert data["plans"]["1"]["packages"] == ["a", "b"]  # stdout is still clean JSON
    assert "#41" in err
    assert "(draft)" in err
    assert "merged" in err
    assert "closed" in err
    assert "Pull request to simulate [1-4, Enter for 2, q to quit]" in err


def test_enter_picks_the_pull_request_of_the_current_branch(
    repo, served, open_prs, monkeypatch, capsys
):
    asked = served(pull_request(repo))
    answers(monkeypatch, "")
    code, _, err = bare(capsys)
    assert code == 0
    assert asked == ["7"]
    assert "★" in err


def test_a_wrong_answer_asks_again(repo, served, open_prs, monkeypatch, capsys):
    asked = served(pull_request(repo))
    answers(monkeypatch, "x", "9", "1")
    code, _, err = bare(capsys)
    assert code == 0
    assert asked == ["41"]
    assert err.count("not a choice") == 2


def test_three_wrong_answers_give_up(repo, served, open_prs, monkeypatch, capsys):
    served(pull_request(repo))
    answers(monkeypatch, "x", "y", "z")
    code, _, err = bare(capsys)
    assert code != 0
    assert "no pull request selected" in err


def test_q_cancels(repo, served, open_prs, monkeypatch, capsys):
    asked = served(pull_request(repo))
    answers(monkeypatch, "q")
    code, _, err = bare(capsys)
    assert code != 0
    assert asked == []
    assert "no pull request selected" in err


def test_end_of_input_cancels(repo, served, open_prs, monkeypatch, capsys):
    served(pull_request(repo))

    def eof():
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    code, _, err = bare(capsys)
    assert code != 0
    assert "no pull request selected" in err


def test_with_no_terminal_it_prints_the_list_and_asks_for_a_number(
    repo, served, open_prs, monkeypatch, capsys
):
    asked = served(pull_request(repo))
    monkeypatch.setattr(prmod, "interactive", lambda: False)
    code, _, err = bare(capsys)
    assert code != 0
    assert asked == []
    assert "#41" in err
    assert "no terminal to choose from" in err
    assert "picard simulate --pr 41" in err


def test_no_open_pull_requests_is_an_error(repo, served, monkeypatch, capsys):
    served(pull_request(repo))
    monkeypatch.setattr(prmod, "list_recent", lambda limit=prmod.LIST_LIMIT: [])
    code, _, err = bare(capsys)
    assert code != 0
    assert "no pull requests" in err


def test_a_failing_list_stops_with_ghs_own_words(repo, monkeypatch, capsys):
    def broken(limit=prmod.LIST_LIMIT):
        raise PullRequestError("gh could not read the pull request: HTTP 401")

    monkeypatch.setattr(prmod, "list_recent", broken)
    code, _, err = bare(capsys)
    assert code != 0
    assert "HTTP 401" in err


def test_a_given_number_never_lists(repo, served, monkeypatch, capsys):
    asked = served(pull_request(repo))

    def boom(limit=prmod.LIST_LIMIT):
        raise AssertionError("listed although a number was given")

    monkeypatch.setattr(prmod, "list_recent", boom)
    code, _, _ = simulate(capsys)
    assert code == 0
    assert asked == ["7"]


# ── what it refuses ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--files", "a/x.py"), ("--staged", None), ("--working", None), ("--local", None)]
    + [("--base", "main")],
)
def test_pr_reads_what_changed_from_the_pull_request(repo, served, capsys, flag, value):
    served(pull_request(repo))
    extra = [flag] if value is None else [flag, value]
    code, _, err = simulate(capsys, *extra)
    assert code != 0
    assert flag in err


def test_a_head_that_is_not_here_falls_back_to_the_platforms_file_list(
    repo, served, monkeypatch, capsys
):
    found = pull_request(repo)
    served(PullRequest(**{**found.__dict__, "head_sha": "f" * 40}))
    monkeypatch.setattr(prmod, "changed_paths", lambda number: ["a/x.py"])
    code, data, err = simulate(capsys)
    assert code == 0
    assert data["files"] == ["a/x.py"]
    assert "not in this clone" in err


def test_a_failure_to_read_the_pull_request_stops_with_what_to_do(repo, monkeypatch, capsys):
    def broken(spec=""):
        raise PullRequestError("--pr needs the GitHub CLI (gh): install it, or pass --title")

    monkeypatch.setattr(prmod, "fetch", broken)
    code, _, err = simulate(capsys)
    assert code != 0
    assert "GitHub CLI" in err


# ── the gh layer ───────────────────────────────────────────────────────────────────────


def gh_answer(**overrides):
    data = {
        "number": 7,
        "title": "feat(a): x (T-1)",
        "body": "BREAKING CHANGE: y",
        "labels": [{"name": "ci:full"}, {"name": "bug"}],
        "baseRefName": "main",
        "baseRefOid": SHA,
        "headRefName": "feature",
        "headRefOid": SHA[::-1],
        "url": "https://github.com/o/r/pull/7",
        "state": "OPEN",
    }
    return json.dumps({**data, **overrides})


def test_fetch_reads_the_fields_the_plan_needs():
    seen = []
    found = prmod.fetch("7", run=lambda args: seen.append(list(args)) or gh_answer())
    assert found.labels == ("ci:full", "bug")
    assert found.body == "BREAKING CHANGE: y"
    assert seen[0][:3] == ["pr", "view", "7"]


def test_fetch_without_a_spec_asks_for_the_current_branch():
    seen = []
    prmod.fetch("", run=lambda args: seen.append(list(args)) or gh_answer())
    assert seen[0][:2] == ["pr", "view"]
    assert seen[0][2] == "--json"


@pytest.mark.parametrize("spec", ["--web", "feature-branch", "7 8", "../x", "https://evil/x"])
def test_fetch_refuses_anything_but_a_number_or_a_pull_request_url(spec):
    with pytest.raises(PullRequestError, match="number or URL"):
        prmod.fetch(spec, run=lambda args: gh_answer())


def test_fetch_accepts_a_pull_request_url():
    url = "https://github.com/o/r/pull/7"
    assert prmod.fetch(url, run=lambda args: gh_answer()).number == 7


def test_fetch_rejects_an_answer_without_commits():
    with pytest.raises(PullRequestError, match="commits"):
        prmod.fetch("7", run=lambda args: gh_answer(baseRefOid="main"))


def test_fetch_rejects_an_answer_it_cannot_read():
    with pytest.raises(PullRequestError, match="cannot read"):
        prmod.fetch("7", run=lambda args: "not json")


def test_a_missing_gh_says_how_to_install_it_or_what_to_pass(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(PullRequestError, match=r"GitHub CLI.*--title, --label and --base"):
        prmod.fetch("7")


def test_a_gh_failure_is_reported_in_its_own_words(monkeypatch):
    done = subprocess.CompletedProcess([], 1, "", "no pull requests found for branch x\nmore")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)
    with pytest.raises(PullRequestError, match="no pull requests found for branch x"):
        prmod.fetch("")


def test_changed_paths_lists_one_file_per_line():
    names = prmod.changed_paths(7, run=lambda args: "a/x.py\nb/y.py\n\n")
    assert names == ["a/x.py", "b/y.py"]


# ── the list ───────────────────────────────────────────────────────────────────────────


def test_list_recent_reads_what_the_list_needs():
    seen = []
    answer = json.dumps(
        [
            {
                "number": 9,
                "title": "feat: x",
                "headRefName": "x",
                "author": {"login": "someone"},
                "isDraft": True,
                "state": "MERGED",
            },
            {"number": 8, "title": "fix: y", "headRefName": "y", "author": None},
        ]
    )
    found = prmod.list_recent(5, run=lambda args: seen.append(list(args)) or answer)
    assert [(item.number, item.author, item.draft, item.state) for item in found] == [
        (9, "someone", True, "MERGED"),
        (8, "", False, "OPEN"),
    ]
    assert seen[0][:6] == ["pr", "list", "--state", "all", "--limit", "5"]


def test_list_recent_rejects_an_answer_it_cannot_read():
    with pytest.raises(PullRequestError, match="cannot read"):
        prmod.list_recent(run=lambda args: '[{"number": 1}]')


def test_render_choices_marks_the_current_branch_and_shortens_long_titles():
    items = [summary(1, "x" * 200, "feature"), summary(2, "short", "other", draft=True)]
    items[1] = summary(2, "short", "other", draft=True, state="CLOSED")
    first, second = prmod.render_choices(items, "feature")
    assert first.startswith("★")
    assert "…" in first
    assert len(first) < 140
    assert not second.startswith("★")
    assert "(draft)" in second
    assert "closed" in second
    assert "open" in first
