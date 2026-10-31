"""test_paths: a change to a package's test files alone affects that package, not its dependents.
Anything else, including an error while matching, walks the dependents as before (it widens)."""

import copy
import dataclasses
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import graph
from picard.graph import impact as impact_mod

pytestmark = pytest.mark.unit

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"

TESTS = ["tests/**", "**/test_*.py", "**/conftest.py"]


def make_cfg(*, core_tests=TESTS, api_tests=(), root=False) -> cfgmod.Config:
    """core <- api <- app (app depends on api), plus web on its own."""
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["packages"] = [
        {"name": "core", "path": "packages/core", "test_paths": list(core_tests)},
        {
            "name": "api",
            "path": "apps/api",
            "depends_on": ["core"],
            "test_paths": list(api_tests),
        },
        {"name": "app", "path": "apps/app", "depends_on": ["api"]},
        {"name": "web", "path": "apps/web"},
    ]
    if root:
        raw["packages"].append({"name": "repo", "path": ".", "test_paths": ["tests/**"]})
    return cfgmod.parse(copy.deepcopy(raw))


CORE_TEST = "packages/core/tests/unit/test_formatter.py"
CORE_SRC = "packages/core/src/core/formatter.py"


def test_a_test_only_change_does_not_reach_the_dependents():
    impact = graph.compute_impact(make_cfg(), [CORE_TEST])
    assert impact.changed == ("core",)
    assert impact.affected == {"core": ("core",)}
    assert impact.test_only == ("core",)
    assert impact.decided_by == {"core": "native"}
    assert graph.render_why(impact) == [
        "core owns a changed file (test files only: dependents are not affected) (via native)"
    ]


@pytest.mark.parametrize(
    "files",
    [
        [CORE_TEST, CORE_SRC],  # mixed
        [CORE_SRC],  # source only
        ["packages/core/README.txt"],  # a file test_paths does not match
    ],
)
def test_anything_but_test_files_keeps_todays_answer(files):
    impact = graph.compute_impact(make_cfg(), files)
    assert impact.affected == {
        "core": ("core",),
        "api": ("api", "core"),
        "app": ("app", "api", "core"),
    }
    assert impact.test_only == ()
    assert "core owns a changed file (via native)" in graph.render_why(impact)


def test_a_package_without_test_paths_is_unchanged():
    impact = graph.compute_impact(make_cfg(core_tests=()), [CORE_TEST])
    assert set(impact.affected) == {"core", "api", "app"} and impact.test_only == ()


def test_a_test_only_dependent_is_still_reached_through_another_change():
    impact = graph.compute_impact(make_cfg(api_tests=TESTS), [CORE_SRC, "apps/api/tests/x.py"])
    assert impact.changed == ("core", "api")
    assert impact.affected == {
        "core": ("core",),
        "api": ("api", "core"),
        "app": ("app", "api", "core"),
    }
    assert impact.test_only == ("api",)
    # Its dependents are affected through it anyway, so the line does not say otherwise.
    assert "api owns a changed file (via native)" in graph.render_why(impact)


def test_two_test_only_changes_each_stay_put():
    impact = graph.compute_impact(make_cfg(api_tests=TESTS), [CORE_TEST, "apps/api/conftest.py"])
    assert impact.affected == {"core": ("core",), "api": ("api",)}
    assert impact.test_only == ("core", "api")


def test_globs_are_relative_to_the_package_path():
    # "tests/**" relative to packages/core does not match a repo-level tests/ directory.
    cfg = make_cfg(core_tests=["tests/**"])
    impact = graph.compute_impact(cfg, ["packages/core/src/tests_helper.py"])
    assert impact.test_only == () and "app" in impact.affected


def test_a_root_package_matches_against_the_repo_path():
    cfg = make_cfg(root=True)
    impact = graph.compute_impact(cfg, ["tests/test_smoke.py"])
    assert impact.changed == ("repo",) and impact.test_only == ("repo",)


def test_a_nested_package_file_stops_the_parent_from_being_test_only():
    # The root package owns every file; a source file in core is not one of its tests.
    impact = graph.compute_impact(make_cfg(root=True), ["tests/test_smoke.py", CORE_SRC])
    assert "repo" not in impact.test_only


@pytest.mark.parametrize("root_first", [True, False])
def test_the_root_package_never_outranks_a_one_character_package_path(root_first):
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    packages = [{"name": "repo", "path": "."}, {"name": "b", "path": "b"}]
    raw["packages"] = packages if root_first else packages[::-1]
    cfg = cfgmod.parse(raw)
    assert graph.owner(cfg, "b/x.py") == "b"
    assert graph.owner(cfg, "README.md") == "repo"


def test_an_error_while_matching_widens(monkeypatch):
    def broken(path, patterns):
        raise ValueError("bad glob")

    monkeypatch.setattr(impact_mod, "matches", broken)
    cfg = make_cfg()
    engine = impact_mod.NativeImpactEngine({}, cfg.registries)
    impact = engine.compute(cfg, [CORE_TEST])
    assert set(impact.affected) == {"core", "api", "app"} and impact.test_only == ()


def test_a_glob_that_cannot_compile_widens():
    cfg = make_cfg()
    bad = dataclasses.replace(cfg.packages["core"], test_paths=("tests/{oops",))
    cfg = dataclasses.replace(cfg, packages={**cfg.packages, "core": bad})
    engine = impact_mod.NativeImpactEngine({}, cfg.registries)
    impact = engine.compute(cfg, ["packages/core/tests/{oops"])
    assert set(impact.affected) == {"core", "api", "app"} and impact.test_only == ()


def test_a_lockfile_hit_on_a_test_only_package_drops_the_test_only_wording():
    cfg = make_cfg()
    native = graph.compute_impact(cfg, [CORE_TEST])
    walked = graph.Impact(
        ("core",),
        {"core": ("core",), "api": ("api", "core")},
        False,
        (),
        decided_by={"core": "lockfile", "api": "lockfile"},
    )
    merged = impact_mod.merge(cfg, native, walked)
    assert "core owns a changed file (via native)" in graph.render_why(merged)
