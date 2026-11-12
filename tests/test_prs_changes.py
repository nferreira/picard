"""--pr on every command that takes a change: labels, commits, states, several PRs, no terminal.

tests/test_prs.py covers the basics of the merged-PR flow; this adds what CI also sees (labels), the
commits a single pull request can bring for lockfile narrowing, pull requests that are open or
closed, and the commands beyond simulate. `gh` is always faked.
"""

import json
import subprocess

import pytest

from picard import prs
from picard.cli import main
from test_simulate import ENCODER, repo  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

SHA_A = "a" * 40


def sha(path, ref="HEAD"):
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", ref], capture_output=True, text=True, check=True
    ).stdout.strip()


class FakeGh:
    """Answers the three `gh` calls picard makes, from a table of pull requests."""

    def __init__(self):
        self.calls = []
        self.table = {}

    def add(self, number, **kw):
        row = {
            "title": f"fix(abc): change {number} (ABC-{number})",
            "body": "",
            "labels": [],
            "state": "MERGED",
            "files": [ENCODER],
            "base": "",
            "head": "",
            "branch": f"b{number}",
            "date": f"2026-10-{number:02d}T10:00:00Z",
            "draft": False,
        }
        self.table[number] = {**row, **kw}
        return self

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps(
                [
                    {
                        "number": n,
                        "title": r["title"],
                        "state": r["state"],
                        "isDraft": r["draft"],
                        "author": {"login": "dev"},
                        "headRefName": r["branch"],
                        "mergedAt": r["date"] if r["state"] == "MERGED" else None,
                        "closedAt": r["date"] if r["state"] == "CLOSED" else None,
                        "createdAt": r["date"],
                    }
                    for n, r in self.table.items()
                ]
            )
        if args[:2] == ("pr", "view"):
            r = self.table[int(args[2])]
            return json.dumps(
                {
                    "title": r["title"],
                    "body": r["body"],
                    "state": r["state"],
                    "labels": [{"name": name} for name in r["labels"]],
                    "baseRefOid": r["base"],
                    "headRefOid": r["head"],
                }
            )
        if args[0] == "api":
            return "\n".join(self.table[int(args[2].split("/")[-2])]["files"]) + "\n"
        raise AssertionError(args)


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(prs, "_gh", fake)
    return fake


def run_json(capsys, *argv):
    code = main([*argv, "--format", "json"])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip().startswith("{") else None), err


def tier1(capsys, *extra):
    code, data, err = run_json(capsys, "simulate", "--tiers", "1", *extra)
    return code, (data["plans"]["1"] if data else None), err


# ── what CI also sees: labels, state ───────────────────────────────────────────────────


def test_the_pull_requests_labels_reach_the_plan(repo, gh, capsys):  # noqa: F811
    gh.add(9, labels=["ci:full", "bug"])
    _, plan, _ = tier1(capsys, "--pr", "9")
    assert plan["labels"] == ["ci:full", "bug"]
    assert plan["forced_full"]


def test_label_adds_to_the_pull_requests_labels(repo, gh, capsys):  # noqa: F811
    gh.add(9, labels=["bug"])
    _, plan, _ = tier1(capsys, "--pr", "9", "--label", "ci:full")
    assert plan["labels"] == ["bug", "ci:full"]
    assert plan["forced_full"]


def test_the_source_names_the_state_of_the_pull_request(repo, gh, capsys):  # noqa: F811
    gh.add(9, state="OPEN").add(8, state="CLOSED")
    _, open_one, _ = run_json(capsys, "simulate", "--pr", "9")
    _, closed, _ = run_json(capsys, "simulate", "--pr", "8")
    assert open_one["source"] == "open PR #9"
    assert closed["source"] == "closed PR #8"


def test_pull_requests_in_different_states_are_just_called_pull_requests(repo, gh, capsys):  # noqa: F811
    gh.add(9, state="OPEN").add(8, state="CLOSED")
    _, data, _ = run_json(capsys, "simulate", "--pr", "8", "9")
    assert data["source"] == "PR #8, #9"


def test_a_hash_before_the_number_is_accepted(repo, gh, capsys):  # noqa: F811
    gh.add(9)
    code, data, _ = run_json(capsys, "simulate", "--pr", "#9")
    assert code == 0
    assert data["source"] == "merged PR #9"


# ── every command that takes a change ──────────────────────────────────────────────────


def test_affected_takes_pr(repo, gh, capsys):  # noqa: F811
    gh.add(9)
    assert main(["affected", "--pr", "9", "--format", "names"]) == 0
    names = capsys.readouterr().out.split()
    assert "component-abc" in names
    assert "some-api" in names  # depends on it


def test_plan_takes_pr_and_uses_its_title(repo, gh, capsys):  # noqa: F811
    gh.add(9, title="feat(abc): new thing (ABC-9)")
    code, data, _ = run_json(capsys, "plan", "--tier", "1", "--pr", "9")
    assert code == 0
    assert data["release"] == "minor"
    assert ENCODER in data["changed_files"]


def test_graph_draws_the_pull_request(repo, gh, capsys):  # noqa: F811
    gh.add(9)
    code, data, _ = run_json(capsys, "graph", "--pr", "9")
    assert code == 0
    assert "component-abc" in data["impact"]["changed"]


@pytest.mark.parametrize(
    "extra", [["--files", ENCODER], ["--staged"], ["--working"], ["--local"], ["--base", "main"]]
)
def test_pr_cannot_be_combined_with_another_source_of_changes(repo, gh, capsys, extra):  # noqa: F811
    gh.add(9)
    code = main(["simulate", "--pr", "9", *extra])
    assert code == 2
    assert extra[0] in capsys.readouterr().err


# ── commits: a single pull request can narrow a lockfile, only when they add up ───────


def branch_with_change(path):
    base = sha(path)
    subprocess.run(["git", "-C", str(path), "checkout", "-q", "-b", "feature"], check=True)
    (path / ENCODER).write_text("x = 2\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e.x",
            "commit",
            "-qm",
            "w",
        ],
        check=True,
    )
    return base, sha(path)


def test_a_single_pull_request_carries_its_commits_when_they_match_the_file_list(
    repo,  # noqa: F811
    gh,
    capsys,
):
    base, head = branch_with_change(repo)
    gh.add(9, base=base, head=head, files=[ENCODER])
    _, plan, _ = tier1(capsys, "--pr", "9")
    assert plan["revisions"]["head"] == head


def test_commits_that_do_not_match_the_file_list_are_not_used(repo, gh, capsys):  # noqa: F811
    base, head = branch_with_change(repo)
    gh.add(9, base=base, head=head, files=[ENCODER, "README.md"])
    _, plan, _ = tier1(capsys, "--pr", "9")
    assert plan["revisions"] is None
    assert set(plan["changed_files"]) == {ENCODER, "README.md"}  # the platform's list is the truth


def test_a_head_that_is_not_in_the_clone_falls_back_to_the_file_list(repo, gh, capsys):  # noqa: F811
    gh.add(9, base=SHA_A, head="b" * 40, files=[ENCODER])
    code, plan, _ = tier1(capsys, "--pr", "9")
    assert code == 0
    assert plan["revisions"] is None
    assert plan["changed_files"] == [ENCODER]


def test_several_pull_requests_are_planned_together_without_commits(repo, gh, capsys):  # noqa: F811
    base, head = branch_with_change(repo)
    gh.add(9, base=base, head=head, files=[ENCODER]).add(8, files=["README.md"])
    _, plan, _ = tier1(capsys, "--pr", "9", "8")
    assert plan["revisions"] is None
    assert set(plan["changed_files"]) == {ENCODER, "README.md"}


# ── the list ───────────────────────────────────────────────────────────────────────────


def test_the_list_shows_every_state_newest_first(repo, gh, monkeypatch, capsys):  # noqa: F811
    gh.add(7, state="OPEN", draft=True).add(8, state="CLOSED").add(9, state="MERGED")
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")
    code, _, err = run_json(capsys, "simulate", "--pr")
    assert code == 0
    assert err.index("#9") < err.index("#8") < err.index("#7")
    for word in ("merged", "closed", "open", "(draft)"):
        assert word in err
    assert gh.calls[0][:4] == ("pr", "list", "--state", "all")


def test_pr_limit_reaches_gh(repo, gh, monkeypatch, capsys):  # noqa: F811
    gh.add(9)
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")
    main(["simulate", "--pr", "--pr-limit", "5", "--format", "json"])
    capsys.readouterr()
    assert "--limit" in gh.calls[0]
    assert gh.calls[0][gh.calls[0].index("--limit") + 1] == "5"


def test_enter_takes_the_pull_request_of_the_current_branch(repo, gh, monkeypatch, capsys):  # noqa: F811
    gh.add(8).add(9, branch="main")  # the repo fixture is on main
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    code, data, err = run_json(capsys, "simulate", "--pr")
    assert code == 0
    assert data["source"] == "merged PR #9"
    assert "★" in err
    assert "Enter for #9" in err


def test_the_prompt_goes_to_stderr_so_json_stays_clean(repo, gh, monkeypatch, capsys):  # noqa: F811
    gh.add(9)
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")
    code, data, err = run_json(capsys, "simulate", "--pr")
    assert code == 0
    assert data is not None
    assert "PR number(s) to simulate" in err


def test_with_no_terminal_it_says_to_name_the_pull_request(repo, gh, monkeypatch, capsys):  # noqa: F811
    gh.add(9)

    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    code, _, err = run_json(capsys, "simulate", "--pr")
    assert code == 2
    assert "no terminal to choose from" in err
    assert "picard simulate --pr 9" in err


def test_no_pull_requests_is_an_error(repo, gh, capsys):  # noqa: F811
    code, _, err = run_json(capsys, "simulate", "--pr")
    assert code == 2
    assert "no pull requests found" in err


# ── the gh layer ───────────────────────────────────────────────────────────────────────


def test_fetch_reads_files_with_every_page_and_both_paths_of_a_rename(gh):
    gh.add(9, files=["new.py", "old.py"])
    found = prs.fetch(9)
    assert found.files == ["new.py", "old.py"]
    api = next(call for call in gh.calls if call[0] == "api")
    assert "--paginate" in api
    assert api[2] == "repos/{owner}/{repo}/pulls/9/files"


def test_fetch_keeps_the_commits_only_when_both_are_commit_ids(gh):
    gh.add(9, base=SHA_A, head="b" * 40)
    assert prs.fetch(9).head_sha == "b" * 40
    gh.add(9, base="main", head="b" * 40)
    assert (prs.fetch(9).base_sha, prs.fetch(9).head_sha) == ("", "")


def test_a_missing_gh_says_how_to_describe_the_change_by_hand(repo, monkeypatch, capsys):  # noqa: F811
    def missing(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing)
    code, _, err = run_json(capsys, "simulate", "--pr", "9")
    assert code == 2
    assert "GitHub CLI" in err
    assert "--files, --title and --label" in err


def test_a_gh_failure_is_reported_in_its_own_words(repo, monkeypatch, capsys):  # noqa: F811
    done = subprocess.CalledProcessError(1, ["gh"], "", "no pull requests found\nmore")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(done))
    code, _, err = run_json(capsys, "simulate", "--pr", "9")
    assert code == 2
    assert "no pull requests found" in err
    assert "more" not in err


def test_an_answer_gh_cannot_be_read_is_an_error_not_a_crash(repo, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(prs, "_gh", lambda *args: "not json")
    code, _, err = run_json(capsys, "simulate", "--pr", "9")
    assert code == 2
    assert "cannot read" in err
