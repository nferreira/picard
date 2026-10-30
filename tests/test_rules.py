"""[[rules]]: what must run when files change that the code graph cannot see."""

import copy
import io
import json
import time
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import globs
from picard.cli import main
from picard.planner import build_plan

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"

INVENTORY = """\
[[model]]
directory = "churn_model"
id = "churn-model"
status = "trainable"

[[model]]
directory = "draft_model"
id = "draft-model"
status = "needs-pipeline"
"""

RULES = """
[rules_policy]
unmapped = "full"

[inventory.model]
file = "models/inventory.toml"
entries = "model"
runnable = ["trainable"]

[actions.test-model]
run = 'pytest models/{model_dir} -m "{markers}"'
[actions.train-model]
run = "python -m train {model}"
tiers = [2]
needs = ["trainable"]
[actions.schema-check]
run = "check-jsonschema --check-metaschema schemas/record.schema.json"
[actions.baseline-all]
run = "python tools/baseline.py --all"

[[rules]]
name = "model data"
match = ["models/{model}/data/**"]
actions = ["run:test-model", "run:train-model", "require-review"]

[[rules]]
name = "schemas"
match = ["schemas/**"]
actions = ["run:schema-check"]

[[rules]]
name = "core code"
match = ["packages/core/src/**"]
actions = ["package", "run:baseline-all"]

[[rules]]
name = "api config"
match = ["apps/api/config/**"]
actions = ["own"]

[[rules]]
name = "docs"
match = ["docs/**", "**/*.md"]
actions = ["none"]
profile = "docs"

[[rules]]
name = "automation"
match = [".github/**"]
actions = ["full"]
"""


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "inventory.toml").write_text(INVENTORY, encoding="utf-8")
    return cfgmod.parse(_raw(), root=tmp_path)


def _raw(extra: str = "") -> dict:
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["packages"] = [
        {"name": "core", "path": "packages/core", "commands": {"test": "pytest packages/core"}},
        {
            "name": "api",
            "path": "apps/api",
            "depends_on": ["core"],
            "commands": {"test": "pytest apps/api"},
        },
        {
            "name": "model",
            "path": "models/churn_model",
            "commands": {"test": "pytest models/churn_model"},
        },
    ]
    raw.update(tomllib.loads(RULES + extra))
    return copy.deepcopy(raw)


def plan(cfg, files, tier=1, title="fix: x (A-1)"):
    return build_plan(cfg, tier, titles=[title], changed_files=files)


def ids(p):
    return [s["id"] for s in p["suites"]]


# ── captures ───────────────────────────────────────────────────────────────────────────


def test_captures_are_named_segments():
    assert globs.captures("models/{model}/data/**") == ("model",)
    assert globs.match_captures("models/churn_model/data/t.csv", ["models/{model}/data/**"]) == {
        "model": "churn_model"
    }
    assert globs.match_captures("models/a/b/data/t.csv", ["models/{model}/data/**"]) is None
    assert globs.match_captures("README.md", ["**/*.md"]) == {}


# ── the three cases: schema, database-like config, a model's dataset ───────────────────


def test_a_model_dataset_runs_that_models_tests_and_nothing_from_the_graph(cfg):
    p = plan(cfg, ["models/churn_model/data/training.csv"])
    assert p["packages"] == []  # no unit suites of any package
    assert ids(p) == ["rule:test-model[model=churn-model]"]
    assert p["suites"][0]["command"] == 'pytest models/churn_model -m "unit or integration"'
    assert p["review"] == [{"rule": "model data", "files": 1}]


def test_the_training_pipeline_runs_only_at_the_baseline_tier(cfg):
    files = ["models/churn_model/data/training.csv"]
    assert ids(plan(cfg, files, tier=2)) == [
        "rule:test-model[model=churn-model]",
        "rule:train-model[model=churn-model]",
    ]
    assert plan(cfg, files, tier=2)["suites"][1]["command"] == "python -m train churn-model"


def test_a_schema_change_runs_the_schema_tool_only(cfg):
    p = plan(cfg, ["schemas/record.schema.json"])
    assert p["packages"] == [] and ids(p) == ["rule:schema-check"]


# ── resolution ─────────────────────────────────────────────────────────────────────────


def test_code_goes_through_the_graph_not_the_rules(cfg):
    p = plan(cfg, ["packages/core/src/x.py"])
    assert p["packages"] == ["core", "api"]  # package action reaches the dependents
    assert ids(p) == ["core:test", "api:test"] or "rule:baseline-all" in ids(p)
    assert "rule:baseline-all" in ids(p)


def test_unmatched_package_files_use_the_graph(cfg):
    assert plan(cfg, ["apps/api/src/app.py"])["packages"] == ["api"]


def test_own_pins_the_owner_without_its_dependents(cfg):
    p = plan(cfg, ["apps/api/config/limits.toml"])
    assert p["packages"] == ["api"]
    p2 = build_plan(cfg, 1, titles=["fix: x (A-1)"], changed_files=["packages/core/src/x.py"])
    assert "api" in p2["packages"]  # `package` does reach dependents


def test_first_matching_rule_wins(cfg):
    p = plan(cfg, ["models/churn_model/data/README.md"])
    assert [r["name"] for r in p["rules"]] == ["model data"]  # not "docs"


def test_results_of_several_files_are_unioned_and_deduplicated(cfg):
    p = plan(
        cfg, ["models/churn_model/data/a.csv", "models/churn_model/data/b.csv", "schemas/x.json"]
    )
    assert ids(p) == ["rule:test-model[model=churn-model]", "rule:schema-check"]
    assert p["rules"][0]["files"] == 2


def test_docs_rule_runs_nothing_and_lowers_the_profile(cfg):
    p = plan(cfg, ["docs/guide.md"], title="docs: guide (A-1)")
    assert p["profile"] == "docs" and p["suites"] == [] and p["rules"][0]["actions"] == ["none"]


def test_full_absorbs_everything(cfg):
    p = plan(cfg, [".github/workflows/x.yml", "docs/a.md"])
    assert set(p["packages"]) == {"core", "api", "model"}
    assert any("rule 'automation' says full" in w for w in p["impact"]["why"])


def test_unmapped_files_run_everything_under_the_full_policy(cfg):
    p = plan(cfg, ["scripts/deploy.sh"])
    assert set(p["packages"]) == {"core", "api", "model"} and p["unmapped"] == ["scripts/deploy.sh"]
    assert any("match no rule and no package" in w for w in p["warnings"])


@pytest.mark.parametrize(
    ("policy", "packages", "warned"), [("ignore", [], False), ("warn", [], True)]
)
def test_unmapped_policy_ignore_and_warn(tmp_path, policy, packages, warned):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "inventory.toml").write_text(INVENTORY, encoding="utf-8")
    raw = _raw()
    raw["rules_policy"] = {"unmapped": policy}
    p = plan(cfgmod.parse(raw, root=tmp_path), ["scripts/deploy.sh"])
    assert p["packages"] == packages
    assert any("match no rule" in w for w in p["warnings"]) is warned


# ── the inventory ──────────────────────────────────────────────────────────────────────


def test_the_inventory_maps_a_directory_to_an_id(cfg):
    p = plan(cfg, ["models/churn_model/data/t.csv"], tier=2)
    assert "python -m train churn-model" in [s["command"] for s in p["suites"]]
    assert p["suites"][0]["command"].startswith(
        "pytest models/churn_model "
    )  # {model_dir}: the directory


def test_a_model_without_a_runnable_status_is_reported_not_skipped_silently(cfg):
    p = plan(cfg, ["models/draft_model/data/t.csv"], tier=2)
    assert any("draft-model" in w and "needs-pipeline" in w for w in p["warnings"])
    assert ids(p) == []  # test-model needs the default runnable status, which draft lacks too


def test_an_action_can_need_a_different_status(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "inventory.toml").write_text(INVENTORY, encoding="utf-8")
    raw = _raw()
    raw["actions"]["test-model"]["needs"] = ["trainable", "needs-pipeline"]
    p = plan(cfgmod.parse(raw, root=tmp_path), ["models/draft_model/data/t.csv"], tier=2)
    assert ids(p) == ["rule:test-model[model=draft-model]"]  # tests run, training does not
    assert any("cannot run 'train-model'" in w for w in p["warnings"])


def test_a_directory_missing_from_the_inventory_runs_everything(cfg):
    p = plan(cfg, ["models/mystery/data/t.csv"])
    assert set(p["packages"]) == {"core", "api", "model"}
    assert any("not in models/inventory.toml" in w for w in p["warnings"])


def test_an_unreadable_inventory_fails_safe(tmp_path):
    p = plan(cfgmod.parse(_raw(), root=tmp_path), ["models/churn_model/data/t.csv"])
    assert set(p["packages"]) == {"core", "api", "model"}
    assert any("cannot read inventory" in w for w in p["warnings"])


@pytest.mark.parametrize("name", ["bad;rm -rf", "a b", "$(id)", "x`y`"])
def test_unsafe_names_never_reach_a_command(tmp_path, name):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "inventory.toml").write_text(
        f'[[model]]\ndirectory = "{name}"\nid = "ok"\nstatus = "trainable"\n', encoding="utf-8"
    )
    p = plan(cfgmod.parse(_raw(), root=tmp_path), [f"models/{name}/data/t.csv"])
    assert not any(name in s["command"] for s in p["suites"])
    assert set(p["packages"]) == {"core", "api", "model"}  # fail-safe: everything


# ── tiers and review ───────────────────────────────────────────────────────────────────


def test_rule_actions_do_not_run_at_tiers_that_ignore_the_diff(cfg):
    assert not any(
        s["id"].startswith("rule:") for s in plan(cfg, ["schemas/x.json"], tier=3)["suites"]
    )


def test_rule_tiers_limit_where_an_action_runs(tmp_path):
    raw = _raw()
    raw["rules"][1]["tiers"] = [2]  # schemas
    cfg = cfgmod.parse(raw, root=tmp_path)
    assert ids(plan(cfg, ["schemas/x.json"], tier=1)) == []
    assert ids(plan(cfg, ["schemas/x.json"], tier=2)) == ["rule:schema-check"]


def test_a_profile_that_skips_a_tier_skips_its_rule_actions(cfg):
    p = plan(cfg, ["docs/a.md"], tier=2, title="docs: a (A-1)")  # docs profile does not run tier 2
    assert p["skip"] and p["suites"] == []


# ── large pull requests ────────────────────────────────────────────────────────────────


def test_a_2400_file_pr_plans_fast_and_stays_under_the_env_var_limit(cfg):
    files = [
        f"models/churn_model/data/batch-{i // 200:02d}/record-{i:05d}-with-a-long-descriptive-name.jsonl"
        for i in range(2400)
    ]
    start = time.time()
    p = plan(cfg, files)
    assert time.time() - start < 2
    assert ids(p) == ["rule:test-model[model=churn-model]"]  # one baseline for 2,400 files
    assert p["changed_files_total"] == 2400 and len(p["changed_files"]) == 200
    assert p["rules"][0]["files"] == 2400 and len(p["rules"][0]["sample"]) == 5
    assert (
        len(json.dumps(p, separators=(",", ":"))) < 100_000
    )  # a single value over 128 KiB fails exec


# ── validation ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda r: r["rules"][0].update(actions=["explode"]), "unknown action 'explode'"),
        (lambda r: r["rules"][0].update(actions=["run:nope"]), "define \\[actions.nope\\]"),
        (
            lambda r: r["rules"][1].update(actions=["run:test-model"]),
            "no match pattern captures it",
        ),
        (lambda r: r["rules"][1].update(name="model data"), "duplicate rule name"),
        (lambda r: r["rules"][0].update(tiers=[9]), "not defined"),
        (lambda r: r["rules"][0].update(profile="nope"), "unknown profile"),
        (lambda r: r["rules"][0].update(match=["models/{a b}/**"]), "bad match pattern"),
        (lambda r: r["rules_policy"].update(unmapped="sometimes"), "unmapped must be one of"),
        (
            lambda r: r["actions"]["test-model"].update(run="pytest {path}"),
            "belong to package commands",
        ),
        (lambda r: r["actions"].update(none={"run": "x"}), "clashes with a built-in"),
        (lambda r: r["actions"]["train-model"].update(tiers=[9]), "tiers are not all defined"),
    ],
)
def test_invalid_rules_fail_loudly(tmp_path, mutate, fragment):
    raw = _raw()
    mutate(raw)
    with pytest.raises(cfgmod.ConfigError, match=fragment):
        cfgmod.parse(raw, root=tmp_path)


# ── --files-from and the RULES section ─────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".pipeline").mkdir()
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "inventory.toml").write_text(INVENTORY, encoding="utf-8")
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index('[[packages]]\nname = "app"')
    end = text.index("# A monorepo, packages listed by hand:")
    text = (
        text[:start]
        + '[[packages]]\nname = "app"\npath = "app"\n[packages.commands]\ntest = "echo t"\n\n'
        + text[end:]
    )
    (tmp_path / ".pipeline" / "pipeline.toml").write_text(text + RULES, encoding="utf-8")
    return tmp_path


def test_files_from_accepts_nul_separated_names_with_spaces_and_commas(repo, capsys):
    (repo / "list").write_bytes(b"models/churn_model/data/a, b.csv\0schemas/x y.json\0")
    assert main(["plan", "--tier", "1", "--files-from", "list", "--title", "fix: x (A-1)"]) == 0
    plan_ = json.loads(capsys.readouterr().out)
    assert set(plan_["changed_files"]) == {"models/churn_model/data/a, b.csv", "schemas/x y.json"}
    assert [s["id"] for s in plan_["suites"]] == [
        "rule:test-model[model=churn-model]",
        "rule:schema-check",
    ]


def test_files_from_stdin_with_newlines(repo, capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.stdin", type("S", (), {"buffer": io.BytesIO(b"schemas/a.json\nschemas/b.json\n")})()
    )
    main(["plan", "--tier", "1", "--files-from", "-", "--title", "fix: x (A-1)"])
    assert json.loads(capsys.readouterr().out)["changed_files_total"] == 2


def test_simulate_shows_the_rules_and_the_review_requirement(repo, capsys):
    main(
        [
            "simulate",
            "--files",
            "models/churn_model/data/t.csv",
            "--title",
            "fix: x (A-1)",
            "--tiers",
            "1,2",
        ]
    )
    text = capsys.readouterr().out
    assert "RULES" in text and "model data: 1 file(s)" in text
    assert "run:test-model, run:train-model, require-review" in text
    assert "REVIEW REQUIRED: model data" in text
