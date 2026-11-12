"""`[rules_policy] unmapped` defaults to `warn`: a file nothing covers is reported, not ignored.

`ignore` hid gaps (a change to a file no rule, package or global glob covers planned nothing and
said nothing). `warn` changes what is said, never what runs, and no gate reads warnings, so it
cannot fail a pipeline. `ignore` and `full` stay one line away.
"""

import json
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard.cli import main
from picard.planner import build_plan
from picard.rules import resolve

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def config(policy=None):
    data = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["packages"] = [{"name": "a", "path": "a", "commands": {"test": "true"}}]
    if policy:
        data["rules_policy"] = {"unmapped": policy}
    return cfgmod.parse(data)


def plan(cfg, *files):
    return build_plan(cfg, 1, titles=["fix: x (ABC-1)"], changed_files=list(files))


def test_the_default_is_warn():
    assert config().unmapped == "warn"
    assert cfgmod.Config.__dataclass_fields__["unmapped"].default == "warn"


def test_an_unmapped_file_is_reported_and_runs_nothing_it_did_not_before():
    result = plan(config(), "stray/file.txt")
    assert any("match no rule and no package: stray/file.txt" in w for w in result["warnings"])
    assert result["packages"] == []  # the plan itself is unchanged
    assert result["unmapped"] == ["stray/file.txt"]


def test_warn_never_forces_a_full_run():
    assert not resolve(config(), ["stray/file.txt"]).full


def test_ignore_is_still_silent():
    result = plan(config("ignore"), "stray/file.txt")
    assert not any("match no rule" in w for w in result["warnings"])
    assert result["unmapped"] == []


def test_full_still_runs_everything():
    result = plan(config("full"), "stray/file.txt")
    assert result["packages"] == ["a"]
    assert any("running everything" in w for w in result["warnings"])


def test_a_covered_file_is_not_reported():
    result = plan(config(), "a/x.py")
    assert result["unmapped"] == []
    assert not any("match no rule" in w for w in result["warnings"])


def test_a_warning_does_not_change_the_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".picard").mkdir()
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index('[[packages]]\nname = "app"')
    end = text.index("# A monorepo, packages listed by hand:")
    block = '[[packages]]\nname = "a"\npath = "a"\n[packages.commands]\ntest = "true"\n\n'
    (tmp_path / ".picard" / "pipeline.toml").write_text(text[:start] + block + text[end:])
    code = main(["plan", "--tier", "1", "--files", "stray/file.txt", "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["unmapped"] == ["stray/file.txt"]
