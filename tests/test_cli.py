"""End to end: real git repos and the real CLI."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from picard.cli import main

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"
REPO_ROOT = Path(__file__).parent.parent


def git(repo, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
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


def app_config():
    """The shipped config, with the default tooling package swapped for a root `app` package."""
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace(
        'name = "app"\npath = "."\nbaseline = false',
        'name = "app"\npath = "."\nbaseline = true',
    )
    text = re.sub(r'(?m)^lint = ".*"$', 'lint = "echo lint"', text, count=1)
    return re.sub(r"(?m)^test = '.*'$", 'test = "echo test {markers}"', text, count=1)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # picard, like CI, runs git in the current directory.
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-b", "main")
    (tmp_path / ".pipeline").mkdir()
    (tmp_path / ".pipeline" / "pipeline.toml").write_text(app_config(), encoding="utf-8")
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "chore: init (ABC-0)")
    git(tmp_path, "checkout", "-b", "feature")
    return tmp_path


def commit(repo, path, subject):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(f"{subject}\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", subject)


def test_plan_json_and_github_outputs(repo, capsys, tmp_path):
    commit(repo, "src.py", "feat: change source (ABC-1)")
    out_file = tmp_path / "gh_output"
    code = main(
        [
            "plan",
            "--config",
            str(repo / ".pipeline" / "pipeline.toml"),
            "--tier",
            "1",
            "--base",
            "main",
            "--head",
            "feature",
            "--title",
            "feat: change source (ABC-1)",
            "--github-output",
            str(out_file),
        ]
    )
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["profile"] == "full" and plan["changed_files"] == ["src.py"]
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines())
    assert outputs["skip"] == "false" and outputs["has_suites"] == "true"
    assert json.loads(outputs["matrix"])["include"][0]["id"] == "app:lint"
    assert json.loads(outputs["plan"])["release"] == "minor"


def test_title_and_body_come_from_environment(repo, capsys, monkeypatch):
    commit(repo, "src.py", "fix: change source (ABC-1)")
    monkeypatch.setenv("PICARD_TITLE", "fix: change source (ABC-1)")
    monkeypatch.setenv("PICARD_BODY", "BREAKING CHANGE: yes")
    main(
        [
            "plan",
            "--config",
            str(repo / ".pipeline" / "pipeline.toml"),
            "--tier",
            "2",
            "--base",
            "main",
            "--head",
            "feature",
        ]
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["release"] == "major"


def test_local_mode_uses_merge_base_and_commit_subjects(repo, capsys, monkeypatch):
    commit(repo, "docs/guide.md", "docs: add guide (ABC-1)")
    monkeypatch.chdir(repo)
    main(["plan", "--tier", "0", "--local", "--format", "text"])
    text = capsys.readouterr().out
    assert "profile: docs" in text and "tier 0 (pre-push)" in text


def test_run_executes_commands_and_stops_on_failure(repo, monkeypatch, capsys):
    config = repo / ".pipeline" / "pipeline.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'test = "echo test {markers}"', 'test = "exit 3"'
        ),
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "chore: tune config (ABC-1)")
    commit(repo, "src.py", "fix: change (ABC-1)")
    monkeypatch.chdir(repo)
    code = main(["run", "--tier", "0", "--local"])
    assert code == 3
    assert "app:test failed with exit code 3" in capsys.readouterr().err


def test_tier_needing_a_base_without_one_is_a_config_error(repo, capsys):
    assert main(["plan", "--config", str(repo / ".pipeline" / "pipeline.toml"), "--tier", "1"]) == 2
    assert "pass what changed" in capsys.readouterr().err


def test_unknown_tier_and_missing_config_exit_2(repo, capsys):
    assert (
        main(
            [
                "plan",
                "--config",
                str(repo / ".pipeline" / "pipeline.toml"),
                "--tier",
                "9",
                "--base",
                "main",
            ]
        )
        == 2
    )
    assert main(["validate-config", "--config", str(repo / "missing.toml")]) == 2


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["feat(x): y (ABC-1)"], 0),
        (["feat(x): Y (ABC-1)"], 1),
        (["feat(x): y"], 1),
        (["feat(x): y", "--no-ticket"], 0),
    ],
)
def test_check_title_exit_codes(repo, args, code):
    assert (
        main(["check-title", "--config", str(repo / ".pipeline" / "pipeline.toml"), *args]) == code
    )


def test_check_title_reads_commit_msg_file(repo, tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text("fix: handle nil\n\nlong body\n", encoding="utf-8")
    assert (
        main(
            [
                "check-title",
                "--config",
                str(repo / ".pipeline" / "pipeline.toml"),
                "--file",
                str(msg),
                "--no-ticket",
            ]
        )
        == 0
    )
    empty = tmp_path / "EMPTY"
    empty.write_text("", encoding="utf-8")
    assert (
        main(
            [
                "check-title",
                "--config",
                str(repo / ".pipeline" / "pipeline.toml"),
                "--file",
                str(empty),
            ]
        )
        == 1
    )


def test_summary_file_is_appended(repo, tmp_path):
    commit(repo, "src.py", "fix: x (ABC-1)")
    summary = tmp_path / "summary.md"
    main(
        [
            "plan",
            "--config",
            str(repo / ".pipeline" / "pipeline.toml"),
            "--tier",
            "1",
            "--base",
            "main",
            "--head",
            "feature",
            "--title",
            "fix: x (ABC-1)",
            "--summary",
            str(summary),
        ]
    )
    assert "Pipeline plan, tier 1" in summary.read_text()


def _picard(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run `python -m picard` the way the installed `picard` script does."""
    full_env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src"), **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "picard", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
    )


def test_the_module_entry_point_runs_the_cli():
    result = _picard("validate-config", "--config", str(TEMPLATE))
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ok:")


def test_the_old_smartplan_environment_variables_still_work():
    """One release of compatibility after the rename to picard."""
    result = _picard(
        "check-title", "--config", str(TEMPLATE), env={"SMARTPLAN_TITLE": "fix: handle nil (ABC-1)"}
    )
    assert result.returncode == 0 and result.stdout.startswith("ok: fix"), result.stderr


def test_debug_flag_turns_on_engine_tracing(monkeypatch, capsys):
    monkeypatch.setenv("PICARD_DEBUG", "0")  # registers the restore after the test
    assert main(["validate-config", "--config", str(TEMPLATE)]) == 0
    assert os.environ["PICARD_DEBUG"] == "0"
    assert main(["validate-config", "--config", str(TEMPLATE), "--debug"]) == 0
    assert os.environ["PICARD_DEBUG"] == "1"


def test_version_flag_prints_the_version(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.startswith("picard ")
