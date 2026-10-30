"""`picard init` / `picard update`: what is replaced, what is only added, what is never touched."""

import os
import subprocess

import pytest

from picard import scaffold
from picard.cli import main

pytestmark = pytest.mark.integration

WORKFLOW = ".github/workflows/pipeline-pr.yml"
ACTION = ".github/actions/pipeline-plan/action.yml"
CONFIG = ".pipeline/pipeline.toml"
CODEOWNERS = ".github/CODEOWNERS"


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    return tmp_path


def run(repo, *args):
    return main([*args, "--root", str(repo)])


def test_init_writes_the_templates(repo):
    assert run(repo, "init") == 0
    for path in (WORKFLOW, ACTION, CONFIG, CODEOWNERS, ".github/dependabot.yaml"):
        assert (repo / path).is_file(), path
    assert (repo / ".github/actions/pipeline-setup-picard/action.yml").is_file()
    assert (repo / ".pipeline/templates/pytest/conftest.py").is_file()


def test_the_starter_config_it_writes_is_valid(repo):
    run(repo, "init")
    assert main(["validate-config", "--config", str(repo / CONFIG)]) == 0


def test_hooks_are_executable_and_ask_for_picard_when_it_is_missing(repo):
    run(repo, "init")
    for hook in ("commit-msg", "pre-push"):
        path = repo / ".pipeline/hooks" / hook
        assert os.access(path, os.X_OK)
        text = path.read_text()
        assert "command -v picard" in text and "exec picard " in text
        assert ".pipeline/bin" not in text


def test_a_second_run_changes_nothing(repo, capsys):
    run(repo, "init")
    capsys.readouterr()
    results = scaffold.scaffold(repo, say=lambda _: None)
    assert {r.status for r in results} <= {"unchanged", "kept"}


def test_repo_owned_files_are_never_overwritten(repo):
    run(repo, "init")
    (repo / CONFIG).write_text("# mine\n", encoding="utf-8")
    (repo / CODEOWNERS).write_text("* @me\n", encoding="utf-8")
    run(repo, "update")
    assert (repo / CONFIG).read_text() == "# mine\n"
    assert (repo / CODEOWNERS).read_text() == "* @me\n"


def test_tool_owned_files_are_replaced(repo):
    run(repo, "init")
    (repo / WORKFLOW).write_text("edited\n", encoding="utf-8")
    run(repo, "update")
    assert (repo / WORKFLOW).read_text() != "edited\n"


def test_a_stale_file_in_a_tool_owned_action_is_removed_and_other_files_are_kept(repo):
    run(repo, "init")
    stale = repo / ".github/actions/pipeline-plan/old-helper.sh"
    stale.write_text("x", encoding="utf-8")
    mine = repo / ".github/workflows/my-own.yml"
    mine.write_text("name: mine\n", encoding="utf-8")
    other = repo / ".github/actions/my-action/action.yml"
    other.parent.mkdir(parents=True)
    other.write_text("name: mine\n", encoding="utf-8")
    run(repo, "update")
    assert not stale.exists()
    assert mine.exists() and other.exists()


def test_dry_run_writes_nothing(repo):
    assert run(repo, "init", "--dry-run") == 0
    assert not (repo / ".github").exists() and not (repo / ".pipeline").exists()


def test_it_refuses_to_scaffold_outside_a_git_repository(tmp_path, capsys):
    assert main(["init", "--root", str(tmp_path)]) == 2
    assert "not a git repository" in capsys.readouterr().err


def test_install_hooks_points_git_at_the_scaffolded_hooks(repo, capsys):
    run(repo, "init")
    assert run(repo, "install-hooks") == 0
    configured = subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert configured == ".pipeline/hooks"


def test_install_hooks_needs_init_first(repo, capsys):
    assert run(repo, "install-hooks") == 2
    assert "picard init" in capsys.readouterr().err
