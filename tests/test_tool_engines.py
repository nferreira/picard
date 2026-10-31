"""The nx, turbo, moon and bazel-diff impact engines, against fake launchers.

None of these tools is installed here: each test writes a small Python script that plays the
tool, records how it was called (argv, the environment variables picard sets, stdin, cwd) and
prints a canned answer. Every failure must widen to the fallback's answer, never narrow it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from picard.bootstrap import default_registries
from picard.cli import main
from picard.config import Package
from picard.graph import DependencyGraph, Revisions
from picard.graph.engines.nx import FILES_LIMIT

TEMPLATE = Path(__file__).resolve().parents[1] / "src/picard/templates/pipeline.toml"
REVS = Revisions("b" * 40, "HEAD")

FAKE = textwrap.dedent(
    """\
    import json, os, sys, time
    spec = json.load(open({spec!r}))
    args = sys.argv[1:]
    stdin = "" if sys.stdin is None or sys.stdin.closed else sys.stdin.read()
    with open({log!r}, "a") as log:
        log.write(json.dumps({{
            "args": args, "stdin": stdin, "cwd": os.getcwd(),
            "env": {{k: v for k, v in os.environ.items() if k.startswith("TURBO_SCM")}},
        }}) + "\\n")
    time.sleep(spec.get("sleep", 0))
    for rule in spec["rules"]:
        if all(any(a == w or a.startswith(w + "=") for a in args) for w in rule["when"]):
            for flag, text in rule.get("write", {{}}).items():
                path = args[-1] if flag == "last" else args[args.index(flag) + 1]
                open(path, "w").write(text)
            sys.stdout.write(rule.get("out", ""))
            sys.stderr.write(rule.get("err", ""))
            sys.exit(rule.get("code", 0))
    sys.stderr.write("fake: no rule for %r" % args)
    sys.exit(97)
    """
)


def pkg(name: str, path: str | None = None) -> Package:
    return Package(name, path or name, (), False, {})


def fake_cfg(root: Path, names=("a", "b", "c"), edges=None, paths=None) -> SimpleNamespace:
    """a depends on b; c is independent. Only what the engines read."""
    paths = paths or {}
    packages = {n: pkg(n, paths.get(n)) for n in names}
    graph = DependencyGraph({n: (edges or {"a": ("b",)}).get(n, ()) for n in names})
    return SimpleNamespace(
        packages=packages,
        global_globs=("uv.lock",),
        narrow_lockfiles=(),
        root=root,
        dependency_graph=lambda: graph,
    )


class Fake:
    """A fake tool: `argv` runs it, `calls()` says how it was called."""

    def __init__(self, tmp_path: Path, rules: list[dict], sleep: float = 0, name: str = "tool"):
        folder = tmp_path / f"fake-{name}"
        folder.mkdir(exist_ok=True)
        self.spec = folder / "spec.json"
        self.log = folder / "calls.jsonl"
        self.spec.write_text(json.dumps({"rules": rules, "sleep": sleep}))
        self.script = folder / "tool.py"
        self.script.write_text(FAKE.format(spec=str(self.spec), log=str(self.log)))
        self.argv = [sys.executable, str(self.script)]

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def install_as(self, folder: Path, name: str) -> None:
        """Put an executable called `name` in `folder` (for a fake on PATH)."""
        folder.mkdir(exist_ok=True)
        launcher = folder / name
        launcher.write_text(f"#!{sys.executable}\n" + self.script.read_text())
        launcher.chmod(0o755)


def engine(name: str, options=None):
    regs = default_registries()
    return regs.engines.get(name)(options, regs)


def nx_rules(projects=("a", "b", "c"), affected=("b",)) -> list[dict]:
    return [
        {"when": ["--affected"], "out": json.dumps(list(affected))},
        {"when": ["show", "projects"], "out": json.dumps(list(projects))},
    ]


def turbo_out(names) -> str:
    return json.dumps({"packages": {"items": [{"name": n, "path": n} for n in names]}})


def turbo_rules(packages=("a", "b", "c"), affected=("b",)) -> list[dict]:
    return [
        {"when": ["--affected"], "out": turbo_out(affected)},
        {"when": ["ls"], "out": turbo_out(packages)},
    ]


def moon_out(sources) -> str:
    return json.dumps({"projects": [{"id": f"p-{s}", "source": s} for s in sources]})


def moon_rules(sources=("a", "b", "c"), affected=("b",)) -> list[dict]:
    return [
        {"when": ["--affected"], "out": moon_out(affected)},
        {"when": ["query", "projects"], "out": moon_out(sources)},
    ]


# -- a git repository for bazel-diff ---------------------------------------------------------


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def bazel_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "dev@example.com")
    git(root, "config", "user.name", "dev")
    git(root, "config", "commit.gpgsign", "false")
    for name in ("a", "b"):
        (root / name).mkdir()
        (root / name / "BUILD.bazel").write_text("")
    (root / "c").mkdir()
    (root / "c" / "x.py").write_text("")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "b" / "x.py").write_text("changed")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "head")
    return root, base


def bazel_rules(targets="//b:lib\n", code=0) -> list[dict]:
    return [
        {"when": ["generate-hashes"], "write": {"last": "{}"}},
        {"when": ["get-impacted-targets"], "write": {"-o": targets}, "code": code},
    ]


def bazel_engine(fake: Fake, **options):
    return engine("bazel-diff", {"command": fake.argv, "bazel": sys.executable, **options})


# -- happy paths -------------------------------------------------------------------------


class TestHappyPath:
    def test_nx_with_revisions_asks_base_and_head(self, tmp_path):
        fake = Fake(tmp_path, nx_rules())
        impact = engine("nx", {"command": fake.argv}).impact(fake_cfg(tmp_path), ["b/x.ts"], REVS)
        assert impact.changed == ("b",)
        assert set(impact.affected) == {"a", "b"}
        assert impact.decided_by == {"a": "nx", "b": "nx"}
        assert impact.chain("a") == "a -> b"
        asked = fake.calls()[-1]["args"]
        assert asked == [
            "show",
            "projects",
            "--affected",
            f"--base={'b' * 40}",
            "--head=HEAD",
            "--json",
        ]

    def test_nx_without_revisions_passes_owned_files(self, tmp_path):
        fake = Fake(tmp_path, nx_rules())
        engine("nx", {"command": fake.argv}).impact(fake_cfg(tmp_path), ["b/x.ts", "a/y.ts"])
        assert fake.calls()[-1]["args"][3] == "--files=b/x.ts,a/y.ts"

    def test_nx_with_uncommitted_files_passes_files(self, tmp_path):
        fake = Fake(tmp_path, nx_rules())
        revs = Revisions("b" * 40, "HEAD", ("b/x.ts",))
        engine("nx", {"command": fake.argv}).impact(fake_cfg(tmp_path), ["b/x.ts"], revs)
        assert "--files=b/x.ts" in fake.calls()[-1]["args"]

    def test_turbo_sends_the_refs_in_the_environment(self, tmp_path):
        fake = Fake(tmp_path, turbo_rules(affected=("b", "a")))
        impact = engine("turbo", {"command": fake.argv}).impact(
            fake_cfg(tmp_path), ["b/x.ts"], REVS
        )
        assert set(impact.affected) == {"a", "b"}
        assert set(impact.decided_by.values()) == {"turbo"}
        last = fake.calls()[-1]
        assert last["args"] == ["ls", "--affected", "--output=json"]
        assert last["env"] == {"TURBO_SCM_BASE": "b" * 40, "TURBO_SCM_HEAD": "HEAD"}

    def test_moon_reads_files_on_stdin_and_maps_by_source(self, tmp_path):
        fake = Fake(tmp_path, moon_rules(sources=("apps/a", "libs/b"), affected=("libs/b",)))
        cfg = fake_cfg(tmp_path, names=("a", "b"), paths={"a": "apps/a", "b": "libs/b"})
        impact = engine("moon", {"command": fake.argv}).impact(cfg, ["libs/b/x.ts"])
        assert set(impact.affected) == {"a", "b"}
        assert impact.decided_by == {"a": "moon", "b": "moon"}
        last = fake.calls()[-1]
        assert last["args"] == ["query", "projects", "--affected", "--downstream", "deep"]
        assert json.loads(last["stdin"]) == {"files": ["libs/b/x.ts"], "options": {}}

    def test_bazel_diff_hashes_both_revisions(self, tmp_path):
        root, base = bazel_repo(tmp_path)
        fake = Fake(tmp_path, bazel_rules("//b:lib\n@maven//:guava\n//b/sub:t\n"))
        impact = bazel_engine(fake).impact(fake_cfg(root), ["b/x.py"], Revisions(base, "HEAD"))
        assert set(impact.affected) == {"a", "b"}
        assert impact.decided_by == {"a": "bazel-diff", "b": "bazel-diff"}
        calls = fake.calls()
        hashes = [c for c in calls if c["args"][0] == "generate-hashes"]
        assert len(hashes) == 2
        assert hashes[0]["cwd"] != str(root) and hashes[1]["cwd"] == str(root)
        assert calls[-1]["args"][0] == "get-impacted-targets"
        assert "--startingHashes" in calls[-1]["args"]

    def test_bazel_diff_knows_packages_with_a_build_file(self, tmp_path):
        root, _ = bazel_repo(tmp_path)
        assert engine("bazel-diff").known(fake_cfg(root)) == {"a", "b"}


# -- the shared policy -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "rules"),
    [
        ("nx", nx_rules(affected=("b",))),
        ("turbo", turbo_rules(affected=("b",))),
        ("moon", moon_rules(affected=("b",))),
    ],
)
class TestSharedPolicy:
    def test_a_tool_returning_only_the_changed_package_still_yields_its_dependents(
        self, tmp_path, name, rules
    ):
        fake = Fake(tmp_path, rules)
        impact = engine(name, {"command": fake.argv}).impact(fake_cfg(tmp_path), ["b/x"], REVS)
        assert set(impact.affected) == {"a", "b"}
        assert impact.chain("a") == "a -> b"

    def test_files_not_owned_by_the_tool_go_to_the_fallback(self, tmp_path, name, rules):
        known = {
            "nx": nx_rules(("a", "b")),
            "turbo": turbo_rules(("a", "b")),
            "moon": moon_rules(("a", "b")),
        }[name]
        fake = Fake(tmp_path, known)
        impact = engine(name, {"command": fake.argv}).impact(
            fake_cfg(tmp_path), ["b/x", "c/readme.md", "top.md"], REVS
        )
        assert set(impact.affected) == {"a", "b", "c"}
        assert impact.decided_by["c"] == "native"
        assert impact.decided_by["b"] == name
        assert f"2 file(s) are not owned by {name}: answered by the native engine" in impact.notes

    def test_owners_of_tool_files_are_kept_when_the_tool_says_nothing(self, tmp_path, name, rules):
        empty = {
            "nx": nx_rules(affected=()),
            "turbo": turbo_rules(affected=()),
            "moon": moon_rules(affected=()),
        }[name]
        fake = Fake(tmp_path, empty)
        impact = engine(name, {"command": fake.argv}).impact(fake_cfg(tmp_path), ["b/x"], REVS)
        assert set(impact.affected) == {"a", "b"}  # never smaller than the owner plus dependents

    def test_unknown_names_are_ignored_and_logged_in_debug(self, tmp_path, capsys, name, rules):
        ghost = {
            "nx": nx_rules(affected=("b", "ghost")),
            "turbo": turbo_rules(affected=("b", "ghost")),
            "moon": moon_rules(affected=("b", "ghost")),
        }[name]
        fake = Fake(tmp_path, ghost)
        impact = engine(name, {"command": fake.argv, "debug": True}).impact(
            fake_cfg(tmp_path), ["b/x"], REVS
        )
        assert "ghost" not in impact.affected
        assert "ghost" in capsys.readouterr().err

    def test_uncommitted_files_are_also_answered_by_the_fallback(self, tmp_path, name, rules):
        nothing = {
            "nx": nx_rules(affected=()),
            "turbo": turbo_rules(affected=()),
            "moon": moon_rules(affected=()),
        }[name]
        fake = Fake(tmp_path, nothing)
        cfg = fake_cfg(tmp_path, edges={"a": ("b",), "c": ()})
        revs = Revisions("b" * 40, "HEAD", ("c/dirty.py",))
        impact = engine(name, {"command": fake.argv}).impact(cfg, ["b/x", "c/dirty.py"], revs)
        assert "c" in impact.affected
        assert "1 uncommitted file(s): also answered by the native engine" in impact.notes

    def test_changed_files_never_reach_the_command_line(self, tmp_path, name, rules):
        fake = Fake(tmp_path, rules)
        engine(name, {"command": fake.argv}).impact(fake_cfg(tmp_path), ["b/secret.py"], REVS)
        for call in fake.calls():
            assert not any("b/secret.py" in a for a in call["args"])


# -- every failure widens ----------------------------------------------------------------

FAILURES = {
    "exit": lambda rules: [{"when": [], "code": 3, "err": "boom"}],
    "bad json": lambda rules: [{"when": [], "out": "not json"}],
    "wrong shape": lambda rules: [{"when": [], "out": json.dumps({"unexpected": True})}],
    "affected exit": lambda rules: [{"when": ["--affected"], "code": 1}, *rules[1:]],
    "affected wrong shape": lambda rules: [
        {"when": ["--affected"], "out": json.dumps([1, 2])},
        *rules[1:],
    ],
}


@pytest.mark.parametrize(
    ("name", "rules"),
    [("nx", nx_rules()), ("turbo", turbo_rules()), ("moon", moon_rules())],
)
class TestFailuresWiden:
    @pytest.mark.parametrize("kind", sorted(FAILURES))
    def test_a_failing_tool_widens_to_the_fallback(self, tmp_path, name, rules, kind):
        fake = Fake(tmp_path, FAILURES[kind](rules))
        impact = engine(name, {"command": fake.argv}).impact(fake_cfg(tmp_path), ["b/x"], REVS)
        assert set(impact.affected) == {"a", "b"}
        assert set(impact.decided_by.values()) == {"native"}
        assert impact.notes[-1].startswith(f"{name} failed (")
        assert impact.notes[-1].endswith("; the native engine answered")

    def test_a_timeout_widens(self, tmp_path, name, rules):
        fake = Fake(tmp_path, rules, sleep=5)
        impact = engine(name, {"command": fake.argv, "timeout": 0.3}).impact(
            fake_cfg(tmp_path), ["b/x"], REVS
        )
        assert set(impact.affected) == {"a", "b"}
        assert f"{name} failed (" in impact.notes[-1] and "timed out" in impact.notes[-1]

    def test_a_missing_tool_widens_and_warns_once(self, tmp_path, capsys, name, rules):
        e = engine(name, {"command": "definitely-not-a-real-tool-xyz"})
        cfg = fake_cfg(tmp_path)
        impact = e.impact(cfg, ["b/x"], REVS)
        e.impact(cfg, ["a/y"], REVS)
        assert set(impact.affected) == {"a", "b"}
        assert "is not installed" in impact.notes[-1]
        err = capsys.readouterr().err
        assert err.count("is not installed") == 1
        assert "picard doctor --how-to-install" in err

    @pytest.mark.parametrize("timeout", [0, -1, "soon", True])
    def test_a_bad_timeout_widens(self, tmp_path, name, rules, timeout):
        fake = Fake(tmp_path, rules)
        impact = engine(name, {"command": fake.argv, "timeout": timeout}).impact(
            fake_cfg(tmp_path), ["b/x"], REVS
        )
        assert set(impact.affected) == {"a", "b"}
        assert "timeout must be a positive number" in impact.notes[-1]
        assert fake.calls() == []

    def test_a_fallback_answer_is_never_smaller_than_native(self, tmp_path, name, rules):
        native = engine("native").impact(fake_cfg(tmp_path), ["b/x", "c/y"])
        fake = Fake(tmp_path, [{"when": [], "code": 1}])
        impact = engine(name, {"command": fake.argv}).impact(
            fake_cfg(tmp_path), ["b/x", "c/y"], REVS
        )
        assert set(impact.affected) >= set(native.affected)


def test_nx_files_guard_hands_a_long_list_to_the_fallback(tmp_path):
    fake = Fake(tmp_path, nx_rules())
    files = [f"b/{'x' * 200}-{i}.ts" for i in range(FILES_LIMIT // 200 + 5)]
    impact = engine("nx", {"command": fake.argv}).impact(fake_cfg(tmp_path), files)
    assert set(impact.affected) == {"a", "b"}
    assert "over 100000" in impact.notes[-1]
    assert not any("--affected" in c["args"] for c in fake.calls())


def test_nx_files_guard_lets_a_short_list_through(tmp_path):
    fake = Fake(tmp_path, nx_rules())
    engine("nx", {"command": fake.argv}).impact(fake_cfg(tmp_path), ["b/x.ts"])
    assert any("--affected" in c["args"] for c in fake.calls())


@pytest.mark.parametrize("name", ["turbo", "bazel-diff"])
def test_git_based_tools_without_revisions_hand_over_to_the_fallback(tmp_path, name):
    fake = Fake(tmp_path, turbo_rules())
    options = {"command": fake.argv}
    if name == "bazel-diff":
        options["bazel"] = sys.executable
    impact = engine(name, options).impact(fake_cfg(tmp_path), ["b/x"])
    assert set(impact.affected) == {"a", "b"}
    assert impact.notes[-1] == f"{name} answers from git refs only: the native engine answered"
    assert fake.calls() == []


def test_the_fallback_option_names_the_engine_that_answers(tmp_path):
    fake = Fake(tmp_path, [{"when": [], "code": 1}])
    impact = engine("nx", {"command": fake.argv, "fallback": "native"}).impact(
        fake_cfg(tmp_path), ["b/x"]
    )
    assert impact.notes[-1].endswith("the native engine answered")


def test_a_fake_on_path_is_found_with_the_default_command(tmp_path, monkeypatch):
    fake = Fake(tmp_path, nx_rules())
    fake.install_as(tmp_path / "bin", "nx")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    e = engine("nx")
    cfg = fake_cfg(tmp_path)
    assert e.problem(cfg) is None
    impact = e.impact(cfg, ["b/x"], REVS)
    assert impact.decided_by == {"a": "nx", "b": "nx"}


# -- bazel-diff failures and cleanup -----------------------------------------------------


class TestBazelDiff:
    def worktrees(self, root: Path) -> list[str]:
        return [
            line
            for line in git(root, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")
        ]

    @pytest.mark.parametrize(
        "rules",
        [
            bazel_rules(code=2),
            [{"when": ["generate-hashes"], "code": 1, "err": "bazel broke"}],
            bazel_rules("not a label\n"),
        ],
        ids=["impacted exits", "hashing exits", "unexpected output"],
    )
    def test_failure_widens_and_removes_the_worktree(self, tmp_path, rules):
        root, base = bazel_repo(tmp_path)
        fake = Fake(tmp_path, rules)
        impact = bazel_engine(fake).impact(fake_cfg(root), ["b/x.py"], Revisions(base, "HEAD"))
        assert set(impact.affected) == {"a", "b"}
        assert impact.notes[-1].startswith("bazel-diff failed (")
        assert len(self.worktrees(root)) == 1  # only the main one is left
        assert any(c["args"][0] == "generate-hashes" for c in fake.calls())

    def test_success_removes_the_worktree_too(self, tmp_path):
        root, base = bazel_repo(tmp_path)
        fake = Fake(tmp_path, bazel_rules())
        bazel_engine(fake).impact(fake_cfg(root), ["b/x.py"], Revisions(base, "HEAD"))
        assert len(self.worktrees(root)) == 1
        base_tree = Path(next(c["cwd"] for c in fake.calls() if c["cwd"] != str(root)))
        assert not base_tree.exists()

    def test_a_head_that_is_not_checked_out_widens(self, tmp_path):
        root, base = bazel_repo(tmp_path)
        fake = Fake(tmp_path, bazel_rules())
        impact = bazel_engine(fake).impact(fake_cfg(root), ["b/x.py"], Revisions(base, base))
        assert set(impact.affected) == {"a", "b"}
        assert "is not the checked-out HEAD" in impact.notes[-1]
        assert fake.calls() == []

    def test_a_timeout_widens_and_removes_the_worktree(self, tmp_path):
        root, base = bazel_repo(tmp_path)
        fake = Fake(tmp_path, bazel_rules(), sleep=5)
        impact = bazel_engine(fake, timeout=1.0).impact(
            fake_cfg(root), ["b/x.py"], Revisions(base, "HEAD")
        )
        assert set(impact.affected) == {"a", "b"}
        assert impact.notes[-1].startswith("bazel-diff failed (")
        assert "timed out" in impact.notes[-1]
        assert len(self.worktrees(root)) == 1

    def test_a_missing_bazel_diff_widens(self, tmp_path):
        root, base = bazel_repo(tmp_path)
        e = engine(
            "bazel-diff", {"command": "definitely-not-a-real-tool-xyz", "bazel": sys.executable}
        )
        impact = e.impact(fake_cfg(root), ["b/x.py"], Revisions(base, "HEAD"))
        assert set(impact.affected) == {"a", "b"}
        assert "bazel-diff is not installed" in impact.notes[-1]

    def test_an_unknown_base_widens(self, tmp_path):
        root, _ = bazel_repo(tmp_path)
        fake = Fake(tmp_path, bazel_rules())
        impact = bazel_engine(fake).impact(fake_cfg(root), ["b/x.py"], Revisions("0" * 40, "HEAD"))
        assert set(impact.affected) == {"a", "b"}
        assert impact.notes[-1].startswith("bazel-diff failed (")
        assert len(self.worktrees(root)) == 1

    def test_a_missing_bazel_widens(self, tmp_path):
        root, base = bazel_repo(tmp_path)
        fake = Fake(tmp_path, bazel_rules())
        e = engine("bazel-diff", {"command": fake.argv, "bazel": "definitely-not-bazel-xyz"})
        impact = e.impact(fake_cfg(root), ["b/x.py"], Revisions(base, "HEAD"))
        assert "Bazel is not installed" in impact.notes[-1]
        assert fake.calls() == []

    def test_files_outside_build_packages_go_to_the_fallback(self, tmp_path):
        root, base = bazel_repo(tmp_path)
        fake = Fake(tmp_path, bazel_rules())
        impact = bazel_engine(fake).impact(
            fake_cfg(root), ["b/x.py", "c/x.py"], Revisions(base, "HEAD")
        )
        assert impact.decided_by["c"] == "native"
        assert (
            "1 file(s) are not owned by bazel-diff: answered by the native engine" in impact.notes
        )

    def test_root_targets_map_to_a_root_package(self, tmp_path):
        e = engine("bazel-diff")
        cfg = fake_cfg(tmp_path, names=("top", "b"), paths={"top": ".", "b": "libs/b"}, edges={})
        assert e._packages(cfg, ["//:all", "//libs/b:x", "@@rules//:y"], ".") == {"top", "b"}
        assert e._packages(cfg, ["//lib:x"], "ws") == {"top"}  # ws/lib is under the root package


# -- picard doctor -----------------------------------------------------------------------


class TestDoctor:
    def config(self, tmp_path, engine_name, command):
        text = TEMPLATE.read_text(encoding="utf-8")
        (tmp_path / "pipeline.toml").write_text(
            text + f'\n[impact]\nengine = "{engine_name}"\ncommand = "{command}"\n',
            encoding="utf-8",
        )
        return str(tmp_path / "pipeline.toml")

    @pytest.mark.parametrize(
        ("name", "label", "page"),
        [
            ("nx", "Nx", "nx.dev"),
            ("turbo", "Turborepo", "turborepo.dev"),
            ("moon", "moon", "moonrepo.dev"),
            ("bazel-diff", "bazel-diff", "github.com/Tinder/bazel-diff"),
        ],
    )
    def test_doctor_names_a_missing_tool(self, tmp_path, capsys, name, label, page):
        config = self.config(tmp_path, name, "definitely-not-a-real-tool-xyz")
        assert main(["doctor", "--how-to-install", "--config", config]) == 1
        captured = capsys.readouterr()
        assert (
            f"warning: {label} is not installed: 'definitely-not-a-real-tool-xyz'" in captured.err
        )
        assert page in captured.out
        assert 'engine = "native"' in captured.out

    def test_doctor_says_an_installed_tool_is_ready(self, tmp_path, capsys):
        config = self.config(tmp_path, "nx", sys.executable)
        assert main(["doctor", "--config", config]) == 0
        assert "the 'nx' impact engine is ready" in capsys.readouterr().out
