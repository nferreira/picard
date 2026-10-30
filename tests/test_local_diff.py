"""'Everything that differs locally': the origin branch, the upstream and uncommitted work."""

import json
import subprocess
from pathlib import Path

import pytest

from picard import gitutil
from picard.cli import main

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def git(repo, *args):
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@e.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(repo, path, message):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{message}\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """main with two commits, checked out on a new branch `feature`."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    git(work, "init", "-b", "main")
    commit(work, "base.txt", "chore: base (A-1)")
    commit(work, "more.txt", "chore: more (A-2)")
    return work


@pytest.fixture
def remote(repo, tmp_path):
    """`repo` with an `origin` that has main."""
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-u", "origin", "main")
    return bare


# ── the origin branch ──────────────────────────────────────────────────────────────────


def test_origin_branch_is_where_the_branch_forked(repo):
    git(repo, "checkout", "-b", "feature")
    commit(repo, "a.py", "feat: a (A-3)")
    commit(repo, "b.py", "feat: b (A-4)")
    ref, how = gitutil.origin_branch()
    assert ref == "main" and ("ancestor" in how or "created from" in how)
    diff = gitutil.local_diff()
    assert diff.committed == ("a.py", "b.py")
    assert diff.subjects == ("feat: b (A-4)", "feat: a (A-3)")


def test_a_stacked_branch_compares_with_its_parent_branch_not_main(repo):
    git(repo, "checkout", "-b", "feature-a")
    commit(repo, "a.py", "feat: a (A-3)")
    git(repo, "checkout", "-b", "feature-b")
    commit(repo, "b.py", "feat: b (A-4)")
    assert gitutil.origin_branch()[0] == "feature-a"
    assert gitutil.local_diff().committed == ("b.py",)  # only what feature-b added


def test_the_nearest_branch_wins_over_an_older_sibling(repo):
    git(repo, "branch", "develop")  # same commit as main
    git(repo, "checkout", "-b", "feature")
    commit(repo, "a.py", "feat: a (A-3)")
    assert gitutil.origin_branch()[0] in {"main", "develop"}  # tie, default names preferred
    git(repo, "checkout", "main")
    commit(repo, "main-only.py", "fix: later work on main (A-5)")
    git(repo, "checkout", "feature")
    assert gitutil.local_diff().committed == ("a.py",)  # main moving on does not leak in


def test_origin_branch_uses_the_remote_copy_when_main_has_moved(repo, remote):
    git(repo, "checkout", "-b", "feature")
    commit(repo, "a.py", "feat: a (A-3)")
    git(repo, "checkout", "main")
    commit(repo, "other.py", "fix: someone else (A-5)")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "feature")
    assert gitutil.local_diff().committed == ("a.py",)


def test_a_recorded_merge_base_wins(repo):
    git(repo, "branch", "release")
    git(repo, "checkout", "-b", "feature")
    commit(repo, "a.py", "feat: a (A-3)")
    git(repo, "config", "branch.feature.vscode-merge-base", "release")
    ref, how = gitutil.origin_branch()
    assert (ref, how) == ("release", "recorded by VS Code")


def test_the_reflog_names_the_branch_it_was_created_from(repo):
    git(repo, "branch", "release")
    git(repo, "checkout", "-b", "feature", "release")
    commit(repo, "a.py", "feat: a (A-3)")
    ref, how = gitutil.origin_branch()
    assert ref == "release" and "created from" in how


def test_a_fresh_branch_with_no_commits_has_no_committed_changes(repo):
    git(repo, "checkout", "-b", "feature")
    diff = gitutil.local_diff()
    assert diff.committed == () and diff.subjects == ()


def test_on_main_the_upstream_is_the_fallback(repo, remote):
    commit(repo, "unpushed.py", "feat: not pushed (A-3)")
    ref, how = gitutil.origin_branch()
    assert ref == "origin/main" and "upstream" in how
    assert gitutil.local_diff().committed == ("unpushed.py",)


def test_no_other_branch_and_no_upstream_is_a_clear_error(repo):
    with pytest.raises(gitutil.GitError, match="--against"):
        gitutil.origin_branch()


# ── the upstream ───────────────────────────────────────────────────────────────────────


def test_against_upstream_is_only_what_was_not_pushed(repo, remote):
    git(repo, "checkout", "-b", "feature")
    commit(repo, "pushed.py", "feat: pushed (A-3)")
    git(repo, "push", "-u", "origin", "feature")
    commit(repo, "local.py", "feat: local only (A-4)")
    by_origin = gitutil.local_diff("origin-branch")
    by_upstream = gitutil.local_diff("upstream")
    assert by_origin.committed == ("local.py", "pushed.py")
    assert by_upstream.committed == ("local.py",)
    assert by_upstream.base_ref == "origin/feature"
    assert by_upstream.subjects == ("feat: local only (A-4)",)


def test_against_upstream_without_one_explains_how_to_set_it(repo):
    git(repo, "checkout", "-b", "feature")
    with pytest.raises(gitutil.GitError, match="git push -u"):
        gitutil.local_diff("upstream")


def test_against_any_ref_and_a_bad_ref(repo):
    git(repo, "checkout", "-b", "feature")
    commit(repo, "a.py", "feat: a (A-3)")
    assert gitutil.local_diff("main").committed == ("a.py",)
    assert gitutil.local_diff("HEAD~1").committed == ("a.py",)
    with pytest.raises(gitutil.GitError, match="is not a ref"):
        gitutil.local_diff("nope")


# ── uncommitted work ───────────────────────────────────────────────────────────────────


def test_uncommitted_changes_are_included_by_default(repo):
    git(repo, "checkout", "-b", "feature")
    commit(repo, "committed.py", "feat: c (A-3)")
    (repo / "base.txt").write_text("edited\n")  # unstaged edit of a tracked file
    (repo / "staged.py").write_text("x\n")
    git(repo, "add", "staged.py")
    (repo / "untracked.py").write_text("x\n")
    diff = gitutil.local_diff()
    assert diff.committed == ("committed.py",)
    assert set(diff.uncommitted) == {"base.txt", "staged.py", "untracked.py"}
    assert diff.files == ["base.txt", "committed.py", "staged.py", "untracked.py"]


def test_no_working_ignores_uncommitted_changes(repo):
    git(repo, "checkout", "-b", "feature")
    (repo / "untracked.py").write_text("x\n")
    assert gitutil.local_diff(include_working=False).files == []


def test_ignored_files_do_not_count(repo):
    (repo / ".gitignore").write_text("*.log\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "chore: ignore logs (A-3)")
    git(repo, "checkout", "-b", "feature")
    (repo / "debug.log").write_text("noise\n")
    assert gitutil.local_diff().files == []


# ── through the CLI ────────────────────────────────────────────────────────────────────


@pytest.fixture
def configured(repo):
    """A config whose only package is `app` at the repo root, so every file counts."""
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace(
        'name = "app"\npath = "."\nbaseline = false',
        'name = "app"\npath = "."\nbaseline = true',
    )
    (repo / ".pipeline").mkdir()
    (repo / ".pipeline" / "pipeline.toml").write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "chore: pipeline (A-9)")
    git(repo, "checkout", "-b", "feature")
    return repo


def test_changed_command_lists_commits_committed_and_uncommitted(configured, capsys):
    commit(configured, "a.py", "feat: a (A-3)")
    (configured / "wip.py").write_text("x\n")
    assert main(["changed"]) == 0
    text = capsys.readouterr().out
    assert "feature against main" in text
    assert "commits (1):" in text and "feat: a (A-3)" in text
    assert "committed files (1):\n  a.py" in text
    assert "uncommitted files (staged, unstaged, untracked) (1):\n  wip.py" in text


def test_changed_json(configured, capsys):
    commit(configured, "a.py", "feat: a (A-3)")
    main(["changed", "--format", "json", "--no-working"])
    data = json.loads(capsys.readouterr().out)
    assert data["base_ref"] == "main" and data["files"] == ["a.py"] and data["uncommitted"] == []


def test_local_plan_uses_commit_subjects_and_working_files(configured, capsys):
    commit(configured, "a.py", "feat: a (A-3)")
    (configured / "wip.py").write_text("x\n")
    main(["plan", "--tier", "1", "--local"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["release"] == "minor"  # from the commit subject
    assert set(plan["changed_files"]) == {"a.py", "wip.py"}  # committed + uncommitted


def test_local_with_only_uncommitted_work_follows_the_paths_alone(configured, capsys):
    (configured / "wip.py").write_text("x\n")
    main(["simulate", "--local", "--tiers", "1"])
    text = capsys.readouterr().out
    assert "follows the changed paths alone" in text
    assert "feature against main" in text and "0 commit(s)" in text


def test_local_no_working_and_against_upstream_through_the_cli(configured, remote, capsys):
    git(configured, "push", "-u", "origin", "feature")
    commit(configured, "unpushed.py", "fix: unpushed (A-4)")
    (configured / "wip.py").write_text("x\n")
    main(["plan", "--tier", "1", "--local", "--against", "upstream", "--no-working"])
    assert json.loads(capsys.readouterr().out)["changed_files"] == ["unpushed.py"]


def test_a_git_error_exits_2_with_a_message(configured, capsys):
    assert main(["plan", "--tier", "1", "--local", "--against", "upstream"]) == 2
    assert "no upstream" in capsys.readouterr().err
