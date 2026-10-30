"""The template conftest marks tests by directory and fixture, and can enforce markers."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

PLUGIN = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pytest" / "conftest.py"

PYPROJECT = """\
[tool.pytest.ini_options]
markers = ["unit", "integration", "slow", "baseline"]
addopts = "--strict-markers"
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "conftest.py").write_text(PLUGIN.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    for name in ("unit", "integration", "e2e"):
        (tmp_path / "tests" / name).mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_a.py").write_text("def test_a():\n    pass\n")
    (tmp_path / "tests" / "integration" / "test_b.py").write_text("def test_b():\n    pass\n")
    (tmp_path / "tests" / "e2e" / "test_c.py").write_text("def test_c():\n    pass\n")
    return tmp_path


def pytest_run(project, *args, **env):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=project,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=False,
    )


def collected(project, expr):
    result = pytest_run(project, "--collect-only", "-q", "-m", expr)
    return sorted(line.split("::")[-1] for line in result.stdout.splitlines() if "::" in line)


def test_directories_become_markers(project):
    assert collected(project, "unit") == ["test_a"]
    assert collected(project, "integration") == ["test_b", "test_c"]


def test_e2e_directory_is_slow_so_merge_queue_skips_it(project):
    assert collected(project, "not slow") == ["test_a", "test_b"]
    assert collected(project, "slow") == ["test_c"]


def test_empty_marker_expression_selects_everything(project):
    assert collected(project, "") == ["test_a", "test_b", "test_c"]


def test_fixture_marks_a_test_as_integration(project):
    (project / "tests" / "unit" / "test_d.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef db():\n    return 1\n\ndef test_d(db):\n    pass\n"
    )
    assert "test_d" in collected(project, "integration")


def test_unmarked_tests_pass_by_default_and_fail_when_required(project):
    (project / "tests" / "test_loose.py").write_text("def test_loose():\n    pass\n")
    assert pytest_run(project).returncode == 0
    strict = pytest_run(project, PICARD_REQUIRE_MARKERS="1")
    assert strict.returncode != 0
    assert "no tier marker" in strict.stdout + strict.stderr
    assert "test_loose" in strict.stdout + strict.stderr
