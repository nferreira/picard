"""simulate / graph / --files / --staged / --working against a small uv + pnpm monorepo."""

import json
import subprocess
from pathlib import Path

import pytest

from picard.cli import main

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"

UV_LOCK = """\
[manifest]
members = ["component-abc", "component-def", "some-api"]

[[package]]
name = "component-abc"
source = { editable = "packages/component-abc" }

[[package]]
name = "component-def"
source = { editable = "packages/component-def" }
dependencies = [{ name = "component-abc" }]

[[package]]
name = "some-api"
source = { editable = "apps/some-api" }
dependencies = [{ name = "component-def" }]
"""

ENCODER = "packages/component-abc/src/component_abc/encoder.py"


def git(repo, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@e.com",
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


def config_text() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index('[[packages]]\nname = "app"')
    end = text.index("# A monorepo, packages listed by hand:")
    discover = (
        '[[discover]]\nprovider = "uv"\n[discover.commands]\n'
        'lint = "echo lint {package}"\ntest = \'echo test {package} -m "{markers}"\'\n\n'
        '[[discover]]\nprovider = "npm"\n[discover.commands]\ntest = "echo js {package}"\n\n'
        '[[packages]]\nname = "some-api"\nbaseline = true\n\n'
    )
    text = text[:start] + discover + text[end:]
    return text.replace(
        'global = [".pipeline/pipeline.toml", ".github/workflows/**"]',
        'global = [".pipeline/pipeline.toml", ".github/workflows/**", "uv.lock"]',
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-b", "main")
    files = {
        ".pipeline/pipeline.toml": config_text(),
        "uv.lock": UV_LOCK,
        "package.json": json.dumps({"name": "root", "private": True}),
        "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n  - 'apps/*'\n",
        "packages/ui-kit/package.json": json.dumps({"name": "@demo/ui-kit"}),
        "apps/admin/package.json": json.dumps(
            {"name": "@demo/admin", "dependencies": {"@demo/ui-kit": "workspace:*"}}
        ),
        ENCODER: "x = 1\n",
        "README.md": "# demo\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "chore: init (ABC-0)")
    return tmp_path


def out(capsys) -> str:
    return capsys.readouterr().out


# ── --files: simulate a change, no git needed ──────────────────────────────────────────


def test_plan_with_files_follows_the_graph_and_the_paths_when_there_is_no_title(repo, capsys):
    assert main(["plan", "--tier", "1", "--files", ENCODER, "--format", "text"]) == 0
    text = out(capsys)
    assert "packages: component-abc, component-def, some-api" in text
    assert "some-api is affected through some-api -> component-def -> component-abc" in text
    assert "@demo/admin" not in text  # other ecosystem untouched
    assert "profile: full" in text  # a source file demands full, with no title to lower it
    assert "follows the changed paths alone" in text


def test_plan_with_files_accepts_commas_and_a_real_title(repo, capsys):
    main(
        [
            "plan",
            "--tier",
            "1",
            "--files",
            f"{ENCODER},README.md",
            "--title",
            "feat(abc): x (ABC-1)",
        ]
    )
    plan = json.loads(out(capsys))
    assert plan["release"] == "minor"
    assert set(plan["changed_files"]) == {ENCODER, "README.md"}


def test_tier_2_baseline_adds_the_flagged_package(repo, capsys):
    main(
        ["plan", "--tier", "2", "--files", "packages/ui-kit/index.js", "--title", "fix: x (ABC-1)"]
    )
    plan = json.loads(out(capsys))
    assert "some-api" in plan["packages"]  # baseline = true, although untouched
    assert any("baseline also runs some-api" in r for r in plan["reasons"])


def test_global_file_runs_everything(repo, capsys):
    main(
        [
            "plan",
            "--tier",
            "1",
            "--files",
            "uv.lock",
            "--title",
            "chore(deps): bump x",
            "--format",
            "text",
        ]
    )
    text = out(capsys)
    assert "everything is affected: uv.lock is a global file" in text
    assert "@demo/admin" in text


# ── --staged / --working ───────────────────────────────────────────────────────────────


def test_staged_sees_only_the_index(repo, capsys):
    (repo / ENCODER).write_text("x = 2\n")
    (repo / "packages/ui-kit/index.js").write_text("export {}\n")
    git(repo, "add", ENCODER)
    main(["plan", "--tier", "1", "--staged", "--title", "fix: x (ABC-1)"])
    assert json.loads(out(capsys))["changed_files"] == [ENCODER]


def test_working_sees_unstaged_and_untracked_files(repo, capsys):
    (repo / ENCODER).write_text("x = 2\n")
    (repo / "packages/ui-kit/index.js").write_text("export {}\n")
    main(["plan", "--tier", "1", "--working", "--title", "fix: x (ABC-1)"])
    assert sorted(json.loads(out(capsys))["changed_files"]) == [ENCODER, "packages/ui-kit/index.js"]


# ── simulate ───────────────────────────────────────────────────────────────────────────


def test_simulate_shows_graph_why_and_every_tier(repo, capsys):
    assert main(["simulate", "--files", ENCODER, "--title", "fix(abc): empty input (ABC-1)"]) == 0
    text = out(capsys)
    assert "● component-abc" in text and "◐ component-def" in text and "◐ some-api" in text
    assert "○ @demo/admin" in text
    assert "some-api is affected through some-api -> component-def -> component-abc" in text
    for tier_name in ("pre-push", "pr-fast-gate", "pr-baseline", "merge-queue", "nightly"):
        assert tier_name in text
    assert "verdict: profile full, release patch" in text
    assert "paths alone" not in text


def test_simulate_without_a_title_follows_the_paths_alone(repo, capsys):
    main(["simulate", "--files", "README.md"])
    text = out(capsys)
    assert "follows the changed paths alone" in text and "verdict: profile docs" in text
    assert "skipped" in text  # docs-only: the baseline tier has nothing to do


def test_simulate_commands_lists_what_would_run(repo, capsys):
    main(
        ["simulate", "--files", ENCODER, "--title", "fix: x (ABC-1)", "--tiers", "1", "--commands"]
    )
    text = out(capsys)
    assert (
        "component-abc:test" in text and 'echo test component-abc -m "unit or integration"' in text
    )


def test_simulate_tiers_filter(repo, capsys):
    main(["simulate", "--files", ENCODER, "--title", "fix: x (ABC-1)", "--tiers", "3"])
    text = out(capsys)
    table = text.split("TIERS")[1]
    assert "merge-queue" in table and "pr-fast-gate" not in table


def test_simulate_json_and_mermaid(repo, capsys):
    main(["simulate", "--files", ENCODER, "--title", "fix: x (ABC-1)", "--format", "json"])
    data = json.loads(out(capsys))
    assert set(data["plans"]) == {"0", "1", "2", "3", "4"}
    main(["simulate", "--files", ENCODER, "--title", "fix: x (ABC-1)", "--format", "mermaid"])
    mermaid = out(capsys)
    assert mermaid.startswith("flowchart TD") and "class n0 changed" in mermaid


def test_bare_simulate_uses_the_current_branch(repo, capsys):
    git(repo, "checkout", "-b", "feature")
    (repo / ENCODER).write_text("x = 3\n")
    git(repo, "commit", "-am", "fix(abc): change (ABC-1)")
    main(["simulate", "--base", "main", "--tiers", "1"])
    assert "● component-abc" in out(capsys)


def test_simulate_run_executes_tiers_in_order_and_stops_at_a_failure(repo, capsys):
    config = repo / ".pipeline" / "pipeline.toml"
    config.write_text(
        config.read_text().replace("echo test {package} -m", "echo ran {package} -m", 1)
    )
    assert (
        main(
            ["simulate", "--files", ENCODER, "--title", "fix: x (ABC-1)", "--tiers", "0,1", "--run"]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert err.index("tier 0") < err.index("tier 1")
    assert "== component-abc:test" in err


# ── run --dry-run, graph ───────────────────────────────────────────────────────────────


def test_run_dry_run_prints_but_does_not_execute(repo, capsys, tmp_path):
    marker = tmp_path / "executed"
    config = repo / ".pipeline" / "pipeline.toml"
    config.write_text(config.read_text().replace("echo test {package} -m", f"touch {marker} #", 1))
    assert (
        main(["run", "--tier", "1", "--files", ENCODER, "--title", "fix: x (ABC-1)", "--dry-run"])
        == 0
    )
    assert not marker.exists()
    assert f"touch {marker}" in capsys.readouterr().err


def test_graph_text_with_and_without_an_impact(repo, capsys):
    main(["graph"])
    plain = out(capsys).split("\n\n")[0]
    assert "●" not in plain and "some-api" in plain
    main(["graph", "--files", "packages/ui-kit/index.js"])
    text = out(capsys)
    assert "● @demo/ui-kit" in text and "◐ @demo/admin" in text and "○ some-api" in text


def test_graph_json_lists_packages_edges_and_providers(repo, capsys):
    main(["graph", "--format", "json", "--files", ENCODER])
    data = json.loads(out(capsys))
    assert data["packages"]["component-def"] == {
        "path": "packages/component-def",
        "depends_on": ["component-abc"],
        "provider": "uv",
    }
    assert data["impact"]["changed"] == ["component-abc"]


def test_validate_config_reports_packages_per_provider(repo, capsys):
    assert main(["validate-config"]) == 0
    assert "5 package(s) (npm: 2, uv: 3)" in out(capsys)


def test_nothing_to_diff_is_a_clear_error(repo, capsys):
    assert main(["plan", "--tier", "1"]) == 2
    assert "pass what changed" in capsys.readouterr().err
