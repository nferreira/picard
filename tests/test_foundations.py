"""The shared contracts: new config keys, revisions for engines, lockfile placeholder, selection
base, executor options and plan metrics."""

import copy
import json
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import graph, rules
from picard.cli import main
from picard.executors import Executor, Shard, SuiteContext
from picard.graph import Revisions
from picard.graph.impact import NativeImpactEngine
from picard.planner import build_plan, impact_of

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"

PLUGIN = """\
from picard.executors import Executor


class Picky(Executor):
    \"\"\"Retries and selects, like a test runner with both plugins installed.\"\"\"

    name = "picky"
    retryable = True
    selections = ("impacted", "changed-since")

    def command(self, ctx):
        if ctx.suite != "test":
            return None
        return f"picky {ctx.package.path} base={ctx.selection_base}"


def register(registries):
    registries.executors.register("picky", Picky)
"""


def base_raw() -> dict:
    raw = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    raw["paths"]["global"] = [".picard/pipeline.toml"]
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
            "commands": {"test": 'pytest {path} -m "{markers}"'},
        },
        {"name": "web", "path": "apps/web", "commands": {"test": "pnpm --filter {package} test"}},
    ]
    return raw


def parse(raw: dict, root: Path | None = None) -> cfgmod.Config:
    return cfgmod.parse(copy.deepcopy(raw), root=root)


def with_plugin(raw: dict, root: Path) -> dict:
    (root / "picky.py").write_text(PLUGIN, encoding="utf-8")
    raw["plugins"] = {"paths": ["picky.py"]}
    return raw


# ── config keys ────────────────────────────────────────────────────────────────────────


def test_new_keys_default():
    cfg = parse(base_raw())
    assert (cfg.git_unknown_base, cfg.full_label, cfg.narrow_lockfiles) == ("full", "ci:full", ())
    core = cfg.packages["core"]
    assert (core.always, core.shards, core.test_paths) == (False, 1, ())
    assert not any(t.selection for t in cfg.tiers.values())


def test_new_keys_parse():
    raw = base_raw()
    raw["git"] = {"unknown_base": "fail"}
    raw["labels"] = {"full": "run-everything"}
    raw["lockfiles"] = {"narrow": ["uv.lock", "apps/*/package-lock.json", "Cargo.lock"]}
    raw["packages"][0].update(always=True, shards=4, test_paths=["tests/**"])
    raw["tiers"]["1"]["selection"] = True
    cfg = parse(raw)
    assert cfg.git_unknown_base == "fail"
    assert cfg.full_label == "run-everything"
    assert cfg.narrow_lockfiles == ("uv.lock", "apps/*/package-lock.json", "Cargo.lock")
    core = cfg.packages["core"]
    assert (core.always, core.shards, core.test_paths) == (True, 4, ("tests/**",))
    assert cfg.tiers[1].selection and not cfg.tiers[0].selection


def test_empty_full_label_is_allowed():
    raw = base_raw()
    raw["labels"] = {"full": ""}
    assert parse(raw).full_label == ""


def _set(path: str, value):
    """A mutation of the raw config: `section.key` or `packages.0.key`."""

    def mutate(raw: dict) -> None:
        node = raw
        *parents, last = path.split(".")
        for part in parents:
            node = node[int(part)] if isinstance(node, list) else node.setdefault(part, {})
        node[last] = value

    return mutate


LOCKFILE_ERROR = (
    "[lockfiles] narrow: picard cannot parse '{}'; it can narrow Cargo.lock, package-lock.json, "
    "uv.lock"
)
LABEL_ERROR = "[labels] full must be a label name: no commas, at most 50 characters"
SHARDS_ERROR = "package 'core': shards must be an integer from 1 to 256"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_set("git.unknown_base", "maybe"), "[git] unknown_base must be one of full, fail"),
        (_set("labels.full", "ci,full"), LABEL_ERROR),
        (_set("labels.full", "x" * 51), LABEL_ERROR),
        (_set("labels.full", 3), LABEL_ERROR),
        (_set("lockfiles.narrow", "uv.lock"), "[lockfiles] narrow must be a list of strings"),
        (_set("lockfiles.narrow", ["pnpm-lock.yaml"]), LOCKFILE_ERROR.format("pnpm-lock.yaml")),
        (_set("lockfiles.narrow", ["apps/*/yarn.lock"]), LOCKFILE_ERROR.format("yarn.lock")),
        (_set("lockfiles.narrow", ["uv.lock/**"]), LOCKFILE_ERROR.format("**")),
        (_set("packages.0.always", "yes"), "package 'core': always must be true or false"),
        (_set("packages.0.always", 1), "package 'core': always must be true or false"),
        (_set("packages.0.shards", 0), SHARDS_ERROR),
        (_set("packages.0.shards", 257), SHARDS_ERROR),
        (_set("packages.0.shards", True), SHARDS_ERROR),
        (_set("packages.0.shards", "4"), SHARDS_ERROR),
        (
            _set("packages.0.test_paths", "tests"),
            "package core test_paths must be a list of strings",
        ),
        (
            _set("packages.0.test_paths", ["tests/{kind}/**"]),
            "package 'core' test_paths: captures like {name} are not allowed",
        ),
        (_set("tiers.1.selection", "yes"), "[tiers.1] selection must be true or false"),
        (
            _set("tiers.3.selection", True),
            "[tiers.3] selection needs a diff, but compare is 'none'",
        ),
        (_set("executors.pytest", "fast"), "[executors.pytest] must be a table"),
        (_set("executors.pytest", {"launcher": 3}), "[executors.pytest] launcher must be a string"),
        (
            _set("executors.pytest", {"linter": ["ruff"]}),
            "[executors.pytest] linter must be a string",
        ),
        (
            _set("executors.pytest", {"deps_checker": 1}),
            "[executors.pytest] deps_checker must be a string",
        ),
        (
            _set("executors.make", {"retries": 2}),
            "[executors.make] executor 'make' has no retry option",
        ),
        (
            _set("executors.make", {"selection": "impacted"}),
            "[executors.make] executor 'make' has no selection option",
        ),
    ],
)
def test_config_errors(mutate, message):
    raw = base_raw()
    mutate(raw)
    with pytest.raises(cfgmod.ConfigError) as err:
        parse(raw)
    assert str(err.value) == message


def test_unknown_executor_table():
    raw = base_raw()
    raw["executors"] = {"nope": {}}
    with pytest.raises(cfgmod.ConfigError) as err:
        parse(raw)
    assert str(err.value).startswith("[executors.nope] unknown executor 'nope', known: ")
    assert "make" in str(err.value)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"retries": 11}, "retries must be an integer from 0 to 10"),
        ({"retries": -1}, "retries must be an integer from 0 to 10"),
        ({"retries": True}, "retries must be an integer from 0 to 10"),
        ({"retries": "2"}, "retries must be an integer from 0 to 10"),
        ({"selection": "related"}, "selection must be one of: impacted, changed-since"),
    ],
)
def test_executor_option_errors_from_a_plugin(tmp_path, options, message):
    raw = with_plugin(base_raw(), tmp_path)
    raw["executors"] = {"picky": options}
    with pytest.raises(cfgmod.ConfigError) as err:
        parse(raw, root=tmp_path)
    assert str(err.value) == f"[executors.picky] {message}"


def test_executor_options_that_are_valid(tmp_path):
    raw = with_plugin(base_raw(), tmp_path)
    raw["executors"] = {"picky": {"retries": 3, "selection": "impacted"}, "make": {"retries": 0}}
    cfg = parse(raw, root=tmp_path)
    assert cfg.executor_options["picky"] == {"retries": 3, "selection": "impacted"}


def test_executor_base_defaults():
    class Plain(Executor):
        name = "plain"

        def command(self, ctx):
            return None

    assert Plain.shardable == frozenset() and Plain.retryable is False and Plain.selections == ()
    package = cfgmod.Package("p", "p", (), False, {})
    ctx = SuiteContext(package, "test", "pr", "")
    assert ctx.shard is None and ctx.selection_base is None
    assert SuiteContext(package, "test", "pr", "", Shard(2, 4)).shard == Shard(2, 4)


def test_discover_test_paths_are_validated_and_not_provider_options(tmp_path):
    raw = base_raw()
    raw["discover"] = [{"provider": "uv", "test_paths": ["tests/{x}"]}]
    with pytest.raises(cfgmod.ConfigError) as err:
        parse(raw, root=tmp_path)
    assert str(err.value) == "[[discover]] test_paths: captures like {name} are not allowed"
    raw["discover"] = [{"provider": "uv", "test_paths": "tests"}]
    with pytest.raises(cfgmod.ConfigError) as err:
        parse(raw, root=tmp_path)
    assert str(err.value) == "[[discover]] test_paths must be a list of strings"


def write_uv_workspace(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "root"\nversion = "0"\n[tool.uv.workspace]\nmembers = ["libs/*"]\n',
        encoding="utf-8",
    )
    (root / "libs" / "one").mkdir(parents=True)
    (root / "uv.lock").write_text(
        textwrap.dedent(
            """\
            version = 1
            [manifest]
            members = ["one", "root"]

            [[package]]
            name = "one"
            version = "0.1.0"
            source = { editable = "libs/one" }

            [[package]]
            name = "root"
            version = "0"
            source = { virtual = "." }
            """
        ),
        encoding="utf-8",
    )


def test_discover_test_paths_reach_packages_and_packages_entry_replaces_them(tmp_path):
    write_uv_workspace(tmp_path)
    raw = base_raw()
    raw["discover"] = [{"provider": "uv", "test_paths": ["tests/**"]}]
    cfg = parse(raw, root=tmp_path)
    assert cfg.packages["one"].test_paths == ("tests/**",)
    raw["packages"].append({"name": "one", "test_paths": ["spec/**"], "shards": 2})
    cfg = parse(raw, root=tmp_path)
    assert cfg.packages["one"].test_paths == ("spec/**",)
    assert cfg.packages["one"].shards == 2


# ── revisions reach the engines ────────────────────────────────────────────────────────


class Spy(NativeImpactEngine):
    """The native answer, recording what revisions the engine saw while computing."""

    seen: list = []

    def compute(self, cfg, files):
        Spy.seen.append((tuple(files), self.revisions))
        return super().compute(cfg, files)


@pytest.fixture
def spied():
    cfg = parse(base_raw())
    cfg.registries.engines.register("native", Spy, replace=True)
    Spy.seen = []
    return cfg


def test_a_plugin_engine_sees_the_revisions(spied):
    revisions = Revisions("a" * 40, "HEAD", ("apps/web/new.ts",))
    graph.compute_impact(spied, ["apps/web/x.ts"], revisions)
    assert Spy.seen == [(("apps/web/x.ts",), revisions)]


def test_no_git_range_means_no_revisions(spied):
    build_plan(spied, 1, titles=["fix: x (ABC-1)"], changed_files=["apps/web/x.ts"])
    assert Spy.seen and all(rev is None for _, rev in Spy.seen)


def test_the_memo_key_includes_the_revisions(spied):
    first, second = Revisions("a" * 40, "HEAD"), Revisions("b" * 40, "HEAD")
    for revisions in (first, first, second, None, None):
        graph.compute_impact(spied, ["apps/web/x.ts"], revisions)
    assert [rev for _, rev in Spy.seen] == [first, second, None]


def test_the_plan_records_the_revisions():
    cfg = parse(base_raw())
    revisions = Revisions("c" * 40, "feature", ("x.py",))
    plan = build_plan(cfg, 1, titles=["fix: x (ABC-1)"], changed_files=["x"], revisions=revisions)
    assert plan["revisions"] == {"base": "c" * 40, "head": "feature"}
    assert build_plan(cfg, 1, titles=["fix: x (ABC-1)"], changed_files=["x"])["revisions"] is None


# ── lockfile narrowing placeholder (fails closed) ──────────────────────────────────────


def narrow_cfg(**paths) -> cfgmod.Config:
    raw = base_raw()
    raw["lockfiles"] = {"narrow": ["uv.lock", "**/package-lock.json"]}
    raw["paths"].update(paths)
    return parse(raw)


@pytest.mark.parametrize(
    ("files", "decided"),
    [
        (["uv.lock"], {"core": "lockfile", "api": "lockfile", "web": "lockfile"}),
        (
            ["apps/web/package-lock.json"],
            {"core": "lockfile", "api": "lockfile", "web": "lockfile"},
        ),
        (["packages/core/x.py", "uv.lock"], {"core": "native", "api": "native", "web": "lockfile"}),
    ],
)
def test_a_narrow_lockfile_widens_to_everything_until_it_can_be_attributed(files, decided):
    impact = graph.compute_impact(narrow_cfg(), files)
    assert impact.everything
    assert impact.decided_by == decided
    lockfiles = [f for f in files if f.endswith((".lock", ".json"))]
    assert impact.global_files == tuple(lockfiles)
    assert impact.notes == tuple(
        f"{path} changed and cannot be attributed to packages yet: running everything"
        for path in lockfiles
    )


def test_narrow_wins_over_global_for_the_files_it_lists():
    cfg = narrow_cfg(**{"global": ["uv.lock"]})
    assert set(graph.compute_impact(cfg, ["uv.lock"]).decided_by.values()) == {"lockfile"}
    plan = build_plan(cfg, 1, titles=["fix: x (ABC-1)"], changed_files=["uv.lock"])
    assert plan["packages"] == ["core", "api", "web"]
    assert any("cannot be attributed" in w for w in plan["warnings"])


def test_a_global_file_and_a_narrow_lockfile_both_count():
    cfg = narrow_cfg(**{"global": ["Makefile"]})
    impact = graph.compute_impact(cfg, ["Makefile", "uv.lock"])
    assert impact.everything and impact.global_files == ("Makefile", "uv.lock")
    assert set(impact.decided_by.values()) == {"global file"}


def test_a_narrow_lockfile_is_not_unmapped():
    raw = base_raw()
    raw["rules_policy"] = {"unmapped": "full"}
    assert rules.resolve(parse(raw), ["uv.lock"]).unmapped == ["uv.lock"]
    raw["lockfiles"] = {"narrow": ["uv.lock"]}
    resolution = rules.resolve(parse(raw), ["uv.lock"])
    assert resolution.unmapped == [] and not resolution.full


# ── test selection base ────────────────────────────────────────────────────────────────


@pytest.fixture
def selecting(tmp_path):
    raw = with_plugin(base_raw(), tmp_path)
    raw["packages"][2] = {"name": "web", "path": "apps/web", "executor": "picky"}
    raw["tiers"]["1"]["selection"] = True
    return parse(raw, root=tmp_path)


NO_SELECTION = (
    "test selection needs a committed git range (--base, or --local with no uncommitted "
    "files): whole suites run"
)


@pytest.mark.parametrize(
    ("revisions", "base"),
    [
        (Revisions("d" * 40, "HEAD"), "d" * 40),
        (Revisions("d" * 40, "HEAD", ("apps/web/wip.ts",)), None),  # fail closed: uncommitted
        (None, None),  # fail closed: no git range
    ],
)
def test_selection_base_only_with_a_committed_range(selecting, revisions, base):
    plan = build_plan(
        selecting,
        1,
        titles=["fix: x (ABC-1)"],
        changed_files=["apps/web/a.ts"],
        revisions=revisions,
    )
    assert [s["command"] for s in plan["suites"]] == [f"picky apps/web base={base}"]
    assert (NO_SELECTION in plan["warnings"]) is (base is None)
    assert plan["warnings"].count(NO_SELECTION) <= 1


@pytest.mark.parametrize(
    "forcing",
    [{"labels": ["ci:full"]}, {"full_reasons": ["base abc could not be fetched"]}],
)
def test_a_forced_full_run_never_selects(selecting, forcing):
    """Fail closed: forced full means whole suites, even with a committed range."""
    plan = build_plan(
        selecting,
        1,
        titles=["fix: x (ABC-1)"],
        changed_files=["apps/web/a.ts"],
        revisions=Revisions("d" * 40, "HEAD"),
        **forcing,
    )
    assert plan["forced_full"]
    assert [s["command"] for s in plan["suites"] if s["package"] == "web"] == [
        "picky apps/web base=None"
    ]
    assert NO_SELECTION not in plan["warnings"]


def test_a_tier_without_selection_never_passes_a_base(selecting):
    plan = build_plan(
        selecting,
        0,
        titles=["fix: x (ABC-1)"],
        changed_files=["apps/web/a.ts"],
        revisions=Revisions("d" * 40, "HEAD"),
    )
    assert [s["command"] for s in plan["suites"]] == ["picky apps/web base=None"]
    assert NO_SELECTION not in plan["warnings"]


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
        + list(args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


SELECTING_CONFIG = """
[plugins]
paths = ["picky.py"]

[[packages]]
name = "web"
path = "apps/web"
executor = "picky"
"""


@pytest.mark.integration
def test_local_with_uncommitted_files_selects_nothing(tmp_path, monkeypatch, capsys):
    """Fail closed: branch-mode selection ignores uncommitted work, so no base is passed."""
    monkeypatch.chdir(tmp_path)
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index('[[packages]]\nname = "app"')
    end = text.index("# A monorepo, packages listed by hand")
    text = text[:start] + SELECTING_CONFIG + "\n" + text[end:]
    text = text.replace('name = "pr-fast-gate"\n', 'name = "pr-fast-gate"\nselection = true\n')
    (tmp_path / ".picard").mkdir()
    (tmp_path / ".picard" / "pipeline.toml").write_text(text, encoding="utf-8")
    (tmp_path / "picky.py").write_text(PLUGIN, encoding="utf-8")
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")  # the plugin's
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "chore: init (ABC-0)")
    git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "a.ts").write_text("1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "fix: a (ABC-1)")
    base = git(tmp_path, "merge-base", "main", "HEAD")

    def plan_json(*extra):
        args = ["plan", "--tier", "1", "--local", "--against", "main", *extra]
        assert main(args) == 0
        return json.loads(capsys.readouterr().out)

    committed = plan_json()
    assert [s["command"] for s in committed["suites"]] == [f"picky apps/web base={base}"]
    assert committed["revisions"] == {"base": base, "head": "HEAD"}

    (tmp_path / "apps" / "web" / "wip.ts").write_text("2\n", encoding="utf-8")
    dirty = plan_json()
    assert [s["command"] for s in dirty["suites"]] == ["picky apps/web base=None"]
    assert NO_SELECTION in dirty["warnings"]

    base_plan = plan_json("--base", "main")
    assert base_plan["revisions"] == {"base": base, "head": "HEAD"}


# ── metrics ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tier", "title", "files", "metrics"),
    [
        # core:lint, core:test, api:test, web:test exist; core changed, so api is affected.
        (
            1,
            "fix: x (ABC-1)",
            ["packages/core/x.py"],
            {
                "packages_total": 3,
                "packages_selected": 2,
                "suites_total": 4,
                "suites_selected": 3,
                "jobs": 3,
                "selected_pct": 75,
            },
        ),
        # A docs change: the docs profile does not run tier 2.
        (
            2,
            "docs: x (ABC-1)",
            ["README.md"],
            {
                "packages_total": 3,
                "packages_selected": 0,
                "suites_total": 3,
                "suites_selected": 0,
                "jobs": 0,
                "selected_pct": 0,
            },
        ),
        # The merge queue runs everything.
        (
            3,
            "fix: x (ABC-1)",
            [],
            {
                "packages_total": 3,
                "packages_selected": 3,
                "suites_total": 4,
                "suites_selected": 4,
                "jobs": 4,
                "selected_pct": 100,
            },
        ),
    ],
)
def test_metrics(tier, title, files, metrics):
    plan = build_plan(parse(base_raw()), tier, titles=[title], changed_files=files)
    assert plan["metrics"] == metrics


def test_metrics_with_no_suites_at_all():
    raw = base_raw()
    for package in raw["packages"]:
        package["commands"] = {}
    plan = build_plan(parse(raw), 1, titles=["fix: x (ABC-1)"], changed_files=["apps/web/a"])
    assert plan["metrics"]["suites_total"] == 0 and plan["metrics"]["selected_pct"] == 0


def test_metrics_leave_rule_actions_out_of_suites():
    raw = base_raw()
    raw["actions"] = {"check-schema": {"run": "check-schema"}}
    raw["rules"] = [{"name": "schema", "match": ["schema/**"], "actions": ["run:check-schema"]}]
    plan = build_plan(parse(raw), 1, titles=["fix: x (ABC-1)"], changed_files=["schema/a.json"])
    assert [s["id"] for s in plan["suites"]] == ["rule:check-schema"]
    assert plan["metrics"]["suites_selected"] == 0 and plan["metrics"]["jobs"] == 1


def test_impact_of_is_what_an_affected_tier_runs():
    cfg = parse(base_raw())
    for files in (["packages/core/x.py"], ["apps/web/x.ts"], [".picard/pipeline.toml"]):
        plan = build_plan(cfg, 1, titles=["fix: x (ABC-1)"], changed_files=files)
        assert plan["packages"] == list(impact_of(cfg, files).affected)
