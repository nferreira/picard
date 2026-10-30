import copy
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard.planner import (
    affected_packages,
    baseline_packages,
    build_plan,
    render_markdown,
    render_text,
)

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def monorepo():
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["paths"]["global"] = ["uv.lock", ".github/workflows/**"]
    raw["packages"] = [
        {
            "name": "core",
            "path": "packages/core",
            "commands": {"lint": "ruff check {path}", "test": 'pytest {path} -m "{markers}"'},
        },
        {
            "name": "api",
            "path": "apps/api",
            "depends_on": ["core"],
            "baseline": True,
            "commands": {"test": 'pytest {path} -m "{markers}"'},
        },
        {"name": "web", "path": "apps/web", "commands": {"test": "pnpm --filter {package} test"}},
        {
            "name": "worker",
            "path": "apps/worker",
            "depends_on": ["core"],
            "commands": {"test": 'pytest {path} -m "{markers}"'},
        },
    ]
    raw["path_rules"] = [{"globs": ["db/migrations/**"], "packages": ["worker"]}]
    return cfgmod.parse(copy.deepcopy(raw))


@pytest.fixture(scope="module")
def cfg():
    return monorepo()


def plan(cfg, tier, title, files, body=""):
    return build_plan(cfg, tier, titles=[title], body=body, changed_files=files)


def ids(p):
    return [s["id"] for s in p["suites"]]


# ── package selection ──────────────────────────────────────────────────────────────────


def test_affected_includes_reverse_dependencies(cfg):
    assert affected_packages(cfg, ["packages/core/x.py"]) == ["core", "api", "worker"]


def test_affected_leaf_package_only(cfg):
    assert affected_packages(cfg, ["apps/web/app.ts"]) == ["web"]


def test_global_file_affects_everything(cfg):
    assert affected_packages(cfg, ["uv.lock"]) == ["core", "api", "web", "worker"]


def test_unrelated_file_affects_nothing(cfg):
    assert affected_packages(cfg, ["scripts/release.sh"]) == []


def test_baseline_adds_flagged_and_path_rule_packages(cfg):
    got = baseline_packages(cfg, ["apps/web/app.ts", "db/migrations/001.sql"])
    assert got == ["api", "web", "worker"]


# ── the tier table: each tier reads the plan its own way ───────────────────────────────


def test_tier_1_fast_gate_runs_affected_only(cfg):
    p = plan(cfg, 1, "feat(web): add panel (ABC-1)", ["apps/web/app.ts"])
    assert p["packages"] == ["web"]
    assert p["markers"] == "unit or integration"
    assert ids(p) == ["web:test"]


def test_tier_1_renders_markers_and_paths(cfg):
    p = plan(cfg, 1, "fix(core): off by one (ABC-2)", ["packages/core/a.py"])
    commands = {s["id"]: s["command"] for s in p["suites"]}
    assert commands["core:lint"] == "ruff check packages/core"
    assert commands["core:test"] == 'pytest packages/core -m "unit or integration"'
    assert commands["api:test"] == 'pytest apps/api -m "unit or integration"'


def test_tier_2_baseline_uses_path_rules_and_baseline_marker(cfg):
    p = plan(cfg, 2, "fix(web): bug (ABC-3)", ["apps/web/app.ts", "db/migrations/001.sql"])
    assert p["packages"] == ["api", "web", "worker"]
    assert p["markers"] == "baseline"
    assert all("lint" not in s["suite"] for s in p["suites"])


@pytest.mark.parametrize(("tier", "markers", "has_lint"), [(3, "not slow", True), (4, "", False)])
def test_tiers_3_and_4_run_everything_and_ignore_the_diff(cfg, tier, markers, has_lint):
    p = build_plan(cfg, tier, titles=["docs: nothing (ABC-1)"], changed_files=["README.md"])
    assert not p["skip"]
    assert p["packages"] == ["core", "api", "web", "worker"]
    assert p["markers"] == markers
    assert any(s["suite"] == "lint" for s in p["suites"]) is has_lint
    assert p["profile"] is None and p["release"] is None


def test_nightly_has_security_extras_and_merge_queue_has_none(cfg):
    assert build_plan(cfg, 3)["extras"] == []
    assert build_plan(cfg, 4)["extras"] == ["secrets", "semgrep"]


# ── Conventional Commits drive the plan ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "profile", "release"),
    [
        ("feat(web): add (ABC-1)", "full", "minor"),
        ("fix(web): bug (ABC-1)", "full", "patch"),
        ("perf(web): faster (ABC-1)", "full", "patch"),
        ("refactor(web): tidy (ABC-1)", "full", "none"),
        ("feat(web)!: drop old api (ABC-1)", "full", "major"),
    ],
)
def test_type_selects_profile_and_release(cfg, title, profile, release):
    p = plan(cfg, 1, title, ["apps/web/app.ts"])
    assert (p["profile"], p["release"]) == (profile, release)


def test_docs_only_pr_skips_suites_and_tier_2(cfg):
    p1 = plan(cfg, 1, "docs: explain tiers (ABC-1)", ["README.md", "docs/a.md"])
    assert p1["profile"] == "docs" and p1["suites"] == []
    assert p1["extras"] == ["secrets", "link-check"]
    p2 = plan(cfg, 2, "docs: explain tiers (ABC-1)", ["README.md"])
    assert p2["skip"] and "does not run tier 2" in p2["skip_reason"]
    assert p2["extras"] == []


def test_docs_title_cannot_hide_a_source_change(cfg):
    p = plan(cfg, 1, "docs: update readme (ABC-1)", ["README.md", "apps/web/app.ts"])
    assert p["profile"] == "full"
    assert p["packages"] == ["web"]
    assert any("stricter profile" in w for w in p["warnings"])
    assert not plan(cfg, 2, "docs: update readme (ABC-1)", ["apps/web/app.ts"])["skip"]


def test_feat_with_only_docs_warns_but_keeps_release_effect(cfg):
    p = plan(cfg, 1, "feat: describe thing (ABC-1)", ["docs/a.md"])
    assert p["release"] == "minor"
    assert any("only low-risk files changed" in w for w in p["warnings"])


def test_ci_change_is_infra_with_workflow_lint(cfg):
    p = plan(cfg, 1, "ci: tweak (ABC-1)", [".github/workflows/ci.yml"])
    assert p["profile"] == "infra"
    assert "workflow-lint" in p["extras"]
    assert p["packages"] == ["core", "api", "web", "worker"]  # workflows are global


def test_test_only_change_is_light_and_skips_baseline(cfg):
    p1 = plan(cfg, 1, "test(web): cover edge (ABC-1)", ["apps/web/test_app.py"])
    assert p1["profile"] == "light"
    assert plan(cfg, 2, "test(web): cover edge (ABC-1)", ["apps/web/test_app.py"])["skip"]


def test_dependabot_title_gets_deps_profile_without_a_ticket(cfg):
    p = plan(cfg, 1, "chore(deps): bump aiohttp", ["pnpm-lock.yaml"])
    assert (p["profile"], p["release"]) == ("deps", "none")
    assert p["commits"][0]["valid"] is True  # ticket-exempt


def test_deps_bump_touching_source_escalates_to_full(cfg):
    p = plan(cfg, 1, "chore(deps): bump aiohttp", ["pnpm-lock.yaml", "apps/web/app.ts"])
    assert p["profile"] == "full"


def test_lockfile_only_chore_gets_dependency_review(cfg):
    p = plan(cfg, 1, "chore: refresh lock (ABC-1)", ["pnpm-lock.yaml"])
    assert p["profile"] == "deps"
    assert "dependency-review" in p["extras"]


def test_breaking_change_forces_baseline_and_extra_checks(cfg):
    p = plan(cfg, 2, "chore(web)!: remove flag (ABC-1)", ["apps/web/app.ts"])
    assert p["profile"] == "full" and p["release"] == "major"
    assert not p["skip"]
    assert "breaking-change-checks" in p["extras"]


def test_breaking_footer_in_body_counts(cfg):
    p = plan(
        cfg, 2, "fix(web): rename (ABC-1)", ["apps/web/app.ts"], body="BREAKING CHANGE: renamed"
    )
    assert p["release"] == "major" and "breaking-change-checks" in p["extras"]


def test_breaking_extras_not_added_at_tier_1(cfg):
    p = plan(cfg, 1, "feat(web)!: x (ABC-1)", ["apps/web/app.ts"])
    assert "breaking-change-checks" not in p["extras"]


@pytest.mark.parametrize("title", ["wip", "", "Feat: Loud"])
def test_unparseable_title_fails_closed_to_strictest_profile(cfg, title):
    p = plan(cfg, 1, title, ["apps/web/app.ts"])
    assert p["profile"] == "full" and p["release"] == "none"
    assert p["warnings"]


def test_missing_ticket_does_not_change_the_plan(cfg):
    # Title lint reports it; the plan still follows the type.
    p = plan(cfg, 1, "feat(web): add panel", ["apps/web/app.ts"])
    assert p["profile"] == "full" and p["release"] == "minor"
    assert p["commits"][0]["valid"] is False


def test_multiple_commits_take_the_strongest(cfg):
    p = build_plan(
        cfg,
        0,
        titles=["docs: a (ABC-1)", "fix(web): b (ABC-1)", "chore: c (ABC-1)"],
        changed_files=["apps/web/app.ts"],
    )
    assert p["profile"] == "full" and p["release"] == "patch"


def test_no_changed_files_skips(cfg):
    p = plan(cfg, 1, "fix: x (ABC-1)", [])
    assert p["skip"] and p["skip_reason"] == "no changed files"


def test_undefined_tier_raises(cfg):
    with pytest.raises(ValueError, match="tier 9"):
        build_plan(cfg, 9)


def test_renderers_mention_profile_and_reasons(cfg):
    p = plan(cfg, 1, "docs: update readme (ABC-1)", ["apps/web/app.ts"])
    text, md = render_text(p), render_markdown(p)
    assert "profile: full" in text and "warning:" in text
    assert "| Profile | full |" in md and "Why:" in md
    skipped = plan(cfg, 2, "docs: x (ABC-1)", ["README.md"])
    assert "skipped" in render_text(skipped).lower()
    assert "Skipped" in render_markdown(skipped)


def test_default_config_plans_the_starter_package():
    default = cfgmod.load(TEMPLATE)
    p = build_plan(
        default,
        1,
        titles=["fix(app): handle x (ABC-1)"],
        changed_files=["src/app.py"],
    )
    assert p["packages"] == ["app"]
    assert [s["suite"] for s in p["suites"]] == ["lint", "test"]
    assert p["suites"][1]["command"] == 'python -m pytest -m "unit or integration"'
    assert p["runtime"]["python_version"] == "3.12"
