"""`picard affected`: the affected set in shapes other steps can use."""

import json
import subprocess
from pathlib import Path

import pytest

from picard.cli import main

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"
DEFAULT_PACKAGE = """[[packages]]
name = "app"
path = "."
baseline = false
[packages.commands]
lint = "ruff check . && ruff format --check ."
test = 'python -m pytest -m "{markers}"'
"""
PACKAGES = """[[packages]]
name = "core"
path = "packages/core"
[packages.commands]
test = "pytest {path}"

[[packages]]
name = "api"
path = "apps/api"
depends_on = ["core"]
[packages.commands]
test = "pytest {path}"

[[packages]]
name = "web"
path = "apps/web"
[packages.commands]
test = "pnpm test"

[[packages]]
name = "db"
path = "db"
always = ALWAYS
[packages.commands]
test = "migrate --check"
"""


def write_config(directory: Path, *, always: bool = False) -> Path:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert DEFAULT_PACKAGE in text
    packages = PACKAGES.replace("ALWAYS", "true" if always else "false")
    path = directory / "pipeline.toml"
    path.write_text(text.replace(DEFAULT_PACKAGE, packages), encoding="utf-8")
    return path


@pytest.fixture
def config(tmp_path):
    return str(write_config(tmp_path))


def affected(capsys, config, *args):
    code = main(["affected", "--config", config, *args])
    out = capsys.readouterr()
    assert code == 0, out.err
    return out.out


def test_names_one_per_line_in_config_order(capsys, config):
    out = affected(capsys, config, "--files", "packages/core/x.py")
    assert out == "core\napi\n"  # core first: config order, not the order the graph walks


def test_paths_one_per_line(capsys, config):
    out = affected(capsys, config, "--files", "apps/api/x.py", "--format", "paths")
    assert out == "apps/api\n"


def test_paths_are_deduplicated(capsys, tmp_path):
    text = TEMPLATE.read_text(encoding="utf-8").replace(
        DEFAULT_PACKAGE,
        DEFAULT_PACKAGE + '\n[[packages]]\nname = "app-docs"\npath = "."\n',
    )
    path = tmp_path / "pipeline.toml"
    path.write_text(text, encoding="utf-8")
    out = affected(capsys, str(path), "--files", "x.py", "--format", "paths")
    assert out == ".\n"


@pytest.mark.parametrize("fmt", ["names", "paths"])
def test_an_empty_answer_prints_nothing(capsys, config, fmt):
    # Not even a blank line: `$(picard affected)` must be empty.
    assert affected(capsys, config, "--files", "docs/readme.md", "--format", fmt) == ""


def test_matrix_is_one_compact_line(capsys, config):
    out = affected(capsys, config, "--files", "packages/core/x.py", "--format", "matrix")
    assert out == (
        '{"include":[{"name":"core","path":"packages/core"},{"name":"api","path":"apps/api"}]}\n'
    )


def test_empty_matrix(capsys, config):
    out = affected(capsys, config, "--files", "docs/readme.md", "--format", "matrix")
    assert out == '{"include":[]}\n'


def test_json_schema(capsys, config):
    out = affected(capsys, config, "--files", "packages/core/x.py", "--format", "json")
    data = json.loads(out)
    assert set(data) == {"schema", "source", "everything", "packages", "notes"}
    assert data["schema"] == 1
    assert data["source"] == "files given on the command line"
    assert data["everything"] is False
    assert data["notes"] == []
    assert data["packages"] == [
        {
            "name": "core",
            "path": "packages/core",
            "provider": "manual",
            "decided_by": "native",
            "chain": ["core"],
        },
        {
            "name": "api",
            "path": "apps/api",
            "provider": "manual",
            "decided_by": "native",
            "chain": ["api", "core"],
        },
    ]


def test_a_global_file_affects_everything(capsys, config):
    args = ["--files", ".picard/pipeline.toml", "--format", "json"]  # in the starter's global
    data = json.loads(affected(capsys, config, *args))
    assert data["everything"] is True
    assert [p["name"] for p in data["packages"]] == ["core", "api", "web", "db"]


def test_always_packages_are_included(capsys, tmp_path):
    config = str(write_config(tmp_path, always=True))
    out = affected(capsys, config, "--files", "apps/web/x.ts", "--format", "json")
    packages = {p["name"]: p["decided_by"] for p in json.loads(out)["packages"]}
    assert packages == {"web": "native", "db": "always"}


def test_the_full_label_widens_to_everything(capsys, config):
    out = affected(capsys, config, "--files", "docs/readme.md", "--label", "ci:full")
    assert out == "core\napi\nweb\ndb\n"
    data = json.loads(
        affected(
            capsys, config, "--files", "docs/readme.md", "--label", "ci:full", "--format", "json"
        )
    )
    assert data["everything"] is True
    assert {p["decided_by"] for p in data["packages"]} == {"forced"}
    assert data["notes"] == ["label ci:full forces a full run"]


@pytest.mark.parametrize("value", ['["other", "ci:full"]', "other, ci:full"])
def test_picard_labels_reaches_affected(capsys, config, monkeypatch, value):
    monkeypatch.setenv("PICARD_LABELS", value)
    assert affected(capsys, config, "--files", "docs/readme.md") == "core\napi\nweb\ndb\n"


def test_a_label_with_other_case_does_not_match(capsys, config):
    assert affected(capsys, config, "--files", "docs/readme.md", "--label", "CI:FULL") == ""


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
        + list(args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_bare_affected_is_the_current_branch(capsys, tmp_path, monkeypatch):
    config = str(write_config(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "chore: init")
    git(repo, "checkout", "-b", "feature")
    (repo / "apps" / "web").mkdir(parents=True)
    (repo / "apps" / "web" / "x.ts").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "feat: web")
    assert affected(capsys, config) == "web\n"
    data = json.loads(affected(capsys, config, "--base", "main", "--format", "json"))
    assert data["source"] == "main...HEAD"
    assert [p["name"] for p in data["packages"]] == ["web"]
