"""A lockfile picard recognises is global unless it is narrowed or a rule covers it.

The starter config does not list lockfiles under `[paths] global`, so a dependency bump used to
plan nothing: a change picard could not place was silently dropped. It now runs every package,
which is what the documentation always said, and `[lockfiles] narrow` and rules still win.
"""

import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import graph, lockfiles
from picard.rules import resolve

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def config(**extra):
    data = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["packages"] = [
        {"name": "a", "path": "a", "baseline": False, "commands": {"test": "true"}},
        {"name": "b", "path": "b", "depends_on": ["a"], "commands": {"test": "true"}},
    ]
    data["paths"] = {**data.get("paths", {}), **extra.pop("paths", {})}
    data.update(extra)
    return cfgmod.parse(data)


def impact(cfg, *files):
    return graph.compute_impact(cfg, list(files))


# ── the default ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "uv.lock",
        "Cargo.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "Pipfile.lock",
        "go.sum",
        "apps/web/package-lock.json",
        "services/api/uv.lock",
    ],
)
def test_an_unlisted_lockfile_affects_every_package(path):
    result = impact(config(), path)
    assert result.everything
    assert result.global_files == (path,)
    assert set(result.affected) == {"a", "b"}


def test_the_starter_template_does_not_list_lockfiles_so_this_is_the_default():
    assert not any("lock" in g for g in config().global_globs)


@pytest.mark.parametrize(
    "path", ["uv.lock.bak", "docs/uv.lock.md", "notes/package-lock.txt", "lock"]
)
def test_a_file_that_only_looks_like_a_lockfile_is_not_global(path):
    assert not lockfiles.is_lockfile(path)
    assert not impact(config(), path).everything


# ── what still wins ────────────────────────────────────────────────────────────────────


def test_a_narrowed_lockfile_is_not_global():
    cfg = config(lockfiles={"narrow": ["uv.lock"]})
    result = impact(cfg, "uv.lock")
    # no commits to diff here, so it cannot be attributed: the narrowing path fails closed itself
    assert result.everything
    assert any("cannot be attributed" in note for note in result.notes)


def test_a_rule_that_matches_the_lockfile_takes_it_before_the_graph():
    cfg = config(
        actions={"check": {"run": "echo check"}},
        rules=[{"name": "js lock", "match": ["pnpm-lock.yaml"], "actions": ["run:check"]}],
    )
    resolution = resolve(cfg, ["pnpm-lock.yaml"])
    assert resolution.graph_files == []
    assert resolution.hits == {"js lock": ["pnpm-lock.yaml"]}


def test_a_lockfile_a_rule_sends_to_the_graph_is_still_global():
    cfg = config(
        actions={"check": {"run": "echo check"}},
        rules=[{"name": "elsewhere", "match": ["other/**"], "actions": ["run:check"]}],
    )
    resolution = resolve(cfg, ["uv.lock"])
    assert resolution.graph_files == ["uv.lock"]
    assert impact(cfg, "uv.lock").everything


def test_listing_it_under_global_changes_nothing():
    result = impact(config(paths={"global": ["uv.lock"]}), "uv.lock")
    assert result.everything
    assert result.global_files == ("uv.lock",)


# ── the unmapped report is unchanged ───────────────────────────────────────────────────


def test_an_unlisted_lockfile_is_still_reported_as_unmapped_as_well_as_global():
    # Existing behaviour, kept: the report says no rule or package covers it, and it now also
    # runs every package. With `warn` that is a redundant note; with `full` it is the same answer.
    cfg = config(rules_policy={"unmapped": "warn"})
    assert resolve(cfg, ["uv.lock"]).unmapped == ["uv.lock"]
    assert impact(cfg, "uv.lock").everything


# ── a mixed change ─────────────────────────────────────────────────────────────────────


def test_a_source_change_and_a_lockfile_run_everything_and_name_the_lockfile():
    result = impact(config(), "a/x.py", "uv.lock")
    assert result.everything
    assert result.global_files == ("uv.lock",)
