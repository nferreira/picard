"""Marker plugin: every test gets a tier marker without anyone writing it by hand.

The tiers select tests by marker (`-m "unit"`, `-m "baseline"`, `-m "not slow"`), so a test
with no marker is invisible to the fast tiers. This plugin assigns markers from where a test
lives and which fixtures it uses, and can fail the run when a test is left unmarked.

Copy this file to the repo root (or tests/) and adjust the two maps. Register the markers in
pyproject.toml under [tool.pytest.ini_options] markers, with --strict-markers.

Set PICARD_REQUIRE_MARKERS=1 to fail collection when a test still has no tier marker.
Turn it on once the backlog is marked, so adoption can be gradual.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest

# Directory name (any path segment) -> markers added to every test below it.
DIRECTORY_MARKERS: dict[str, tuple[str, ...]] = {
    "unit": ("unit",),
    "integration": ("integration",),
    "inference": ("integration",),
    "e2e": ("integration", "slow"),
    "slow": ("slow",),
}

# Fixture name -> markers: a test that needs real infrastructure is not a unit test.
FIXTURE_MARKERS: dict[str, tuple[str, ...]] = {
    "db": ("integration",),
    "postgres": ("integration",),
    "redis": ("integration",),
    "docker_services": ("integration", "slow"),
}

TIER_MARKERS = frozenset({"unit", "integration", "slow"})


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    unmarked: list[str] = []
    for item in items:
        path = Path(str(item.fspath))
        # Relative to the rootdir, so a checkout under a folder called `unit` marks nothing.
        with contextlib.suppress(ValueError):
            path = path.relative_to(config.rootpath)
        parts = path.parts
        for segment, markers in DIRECTORY_MARKERS.items():
            if segment in parts:
                for name in markers:
                    item.add_marker(getattr(pytest.mark, name))
        for fixture in getattr(item, "fixturenames", ()):
            for name in FIXTURE_MARKERS.get(fixture, ()):
                item.add_marker(getattr(pytest.mark, name))
        if not TIER_MARKERS.intersection(m.name for m in item.iter_markers()):
            unmarked.append(item.nodeid)

    if (
        unmarked
        and (
            os.environ.get("PICARD_REQUIRE_MARKERS") or os.environ.get("SMARTPLAN_REQUIRE_MARKERS")
        )
        == "1"
    ):
        sample = "\n  ".join(unmarked[:20])
        raise pytest.UsageError(
            f"{len(unmarked)} test(s) have no tier marker (unit, integration or slow). "
            f"Move them under a marked directory or mark them explicitly. First ones:\n  {sample}"
        )
