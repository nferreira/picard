"""A --base the clone does not have: run everything (`full`, the default) or stop (`fail`).

Real depth-1 clones, real git. Whatever happens, the answer is never a smaller diff: it widens.
"""

import json
import subprocess
from pathlib import Path

import pytest

from picard.cli import main

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"
MISSING_SHA = "0123456789abcdef0123456789abcdef01234567"
DEFAULT_PACKAGE = """[[packages]]
name = "app"
path = "."
baseline = false
"""
PACKAGES = """[[packages]]
name = "api"
path = "apps/api"
baseline = false
[packages.commands]
test = "pytest {path}"

[[packages]]
name = "web"
path = "apps/web"
baseline = false
"""
FALLBACK = (
    'picard: warning: running every package instead (set [git] unknown_base = "fail" to stop here)'
)


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
        + list(args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(repo, path, message):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{message}\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write_config(directory: Path, unknown_base: str | None = None) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert DEFAULT_PACKAGE in text
    text = text.replace(DEFAULT_PACKAGE, PACKAGES, 1)
    if unknown_base is not None:
        text += f'\n[git]\nunknown_base = "{unknown_base}"\n'
    path = directory / f"pipeline-{unknown_base}.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture
def shallow(tmp_path, monkeypatch):
    """A depth-1 clone of a repo whose last commit touches only apps/web; returns (clone, base)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    commit(origin, "apps/api/a.py", "chore: api")
    base = commit(origin, "README.md", "chore: readme")
    commit(origin, "apps/web/x.ts", "feat: web")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", "--depth", "1", origin.as_uri(), str(clone))
    monkeypatch.chdir(clone)  # picard, like CI, runs git in the current directory
    return clone, base


def plan(capsys, config, base, *extra):
    code = main(["plan", "--config", config, "--tier", "1", "--base", base, *extra])
    out = capsys.readouterr()
    return code, out


def test_full_runs_every_package_with_both_warnings(shallow, tmp_path, capsys):
    config = write_config(tmp_path)  # unknown_base defaults to "full"
    code, out = plan(capsys, config, MISSING_SHA, "--title", "fix: x (ABC-1)")
    assert code == 0
    reason = f"base {MISSING_SHA} is not in this shallow clone and could not be fetched"
    assert out.err.splitlines()[-2:] == [f"picard: warning: {reason}", FALLBACK]
    data = json.loads(out.out)
    assert data["packages"] == ["api", "web"]  # widened: everything, not the empty diff
    assert data["forced_full"] == [reason]
    assert data["revisions"] is None
    assert not data["skip"]


def test_full_widens_even_a_docs_title(shallow, tmp_path, capsys):
    code, out = plan(
        capsys, write_config(tmp_path, "full"), MISSING_SHA, "--title", "docs: x (A-1)"
    )
    assert code == 0
    data = json.loads(out.out)
    assert data["profile"] == "full" and data["packages"] == ["api", "web"]


def test_fail_exits_2(shallow, tmp_path, capsys):
    code, out = plan(capsys, write_config(tmp_path, "fail"), MISSING_SHA)
    assert code == 2
    assert out.out == ""
    assert f"picard: base {MISSING_SHA} is not in this shallow clone" in out.err
    assert "warning" not in out.err


def test_a_bad_head_stops_even_under_full(shallow, tmp_path, capsys):
    _, base = shallow
    code, out = plan(capsys, write_config(tmp_path, "full"), base, "--head", "no-such-ref")
    assert code == 2
    assert "--head 'no-such-ref' is not a commit" in out.err
    assert "warning" not in out.err


def test_an_abbreviated_sha_cannot_be_fetched_and_widens(shallow, tmp_path, capsys):
    _, base = shallow
    code, out = plan(capsys, write_config(tmp_path), base[:7], "--title", "fix: x (ABC-1)")
    assert code == 0
    assert FALLBACK in out.err
    assert json.loads(out.out)["packages"] == ["api", "web"]


def test_a_full_sha_is_fetched_and_the_diff_narrows(shallow, tmp_path, capsys):
    _, base = shallow
    code, out = plan(capsys, write_config(tmp_path), base, "--title", "fix: x (ABC-1)")
    assert code == 0, out.err
    assert "warning" not in out.err
    data = json.loads(out.out)
    assert data["packages"] == ["web"]
    assert data["revisions"]["base"] == base and data["forced_full"] == []


def test_affected_widens_too(shallow, tmp_path, capsys):
    code = main(["affected", "--config", write_config(tmp_path), "--base", MISSING_SHA])
    out = capsys.readouterr()
    assert code == 0
    assert out.out == "api\nweb\n"
    assert FALLBACK in out.err


def test_affected_json_names_the_reason(shallow, tmp_path, capsys):
    args = ["affected", "--config", write_config(tmp_path), "--base", MISSING_SHA]
    assert main([*args, "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["everything"] is True
    assert data["notes"] == [
        f"base {MISSING_SHA} is not in this shallow clone and could not be fetched"
    ]


def test_tiers_that_ignore_the_diff_never_look_at_the_base(shallow, tmp_path, capsys):
    code = main(["plan", "--config", write_config(tmp_path), "--tier", "3", "--base", MISSING_SHA])
    out = capsys.readouterr()
    assert code == 0 and "warning" not in out.err
