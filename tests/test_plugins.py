"""Registry, plugin loading, executors and impact engines."""

import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from picard.bootstrap import default_registries
from picard.config import Package
from picard.executors import Executor, SuiteContext
from picard.graph.impact import ImpactEngine
from picard.plugins import PluginError
from picard.registry import Registry, UnknownPluginError

pytestmark = [pytest.mark.unit, pytest.mark.baseline]


def pkg(name="svc", path="svc", **kw) -> Package:
    return Package(name, path, (), False, {}, **kw)


def ctx(package=None, suite="test", markers="") -> SuiteContext:
    return SuiteContext(package or pkg(), suite, "pr", markers)


class TestRegistry:
    def test_register_get_names(self):
        reg: Registry[int] = Registry("thing")
        reg.register("b", 2)
        reg.register("a", 1)
        assert reg.get("a") == 1
        assert reg.names() == ("a", "b")
        assert "a" in reg and "z" not in reg
        assert list(reg) == ["a", "b"]

    def test_duplicate_needs_replace(self):
        reg: Registry[int] = Registry("thing")
        reg.register("a", 1)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("a", 2)
        reg.register("a", 2, replace=True)
        assert reg.get("a") == 2

    def test_unknown_lists_known(self):
        reg: Registry[int] = Registry("graph provider")
        reg.register("uv", 1)
        with pytest.raises(UnknownPluginError) as err:
            reg.get("nope")
        assert str(err.value) == "unknown graph provider 'nope', known: uv"


class TestBootstrap:
    def test_builtins_registered(self):
        regs = default_registries()
        assert {"uv", "npm", "cargo", "go", "command"} <= set(regs.providers)
        assert {"native", "pants", "command"} <= set(regs.engines)
        assert {"pytest", "uv-pytest", "go-test", "cargo-test", "pnpm", "npm", "make"} <= set(
            regs.executors
        )

    def test_registries_are_not_shared(self):
        first, second = default_registries(), default_registries()
        first.executors.register("extra", object)
        assert "extra" not in second.executors


class TestPlugins:
    def test_path_plugin_registers(self, tmp_path):
        (tmp_path / "plug.py").write_text(
            textwrap.dedent(
                """\
                from picard.executors import Executor

                class Bazel(Executor):
                    name = "bazel"
                    def command(self, ctx):
                        return f"bazel test //{ctx.package.path}/..."

                def register(registries):
                    registries.executors.register("bazel", Bazel)
                """
            )
        )
        regs = default_registries(plugin_paths=("plug.py",), root=tmp_path)
        assert regs.executors.get("bazel")({}).command(ctx()) == "bazel test //svc/..."

    def test_module_plugin_registers(self, tmp_path, monkeypatch):
        (tmp_path / "picard_demo_plugin.py").write_text(
            "def register(registries):\n    registries.actions.register('x', 1)\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        regs = default_registries(plugin_modules=("picard_demo_plugin",))
        assert regs.actions.get("x") == 1

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"plugin_modules": ("no_such_module_xyz",)}, "cannot import"),
            ({"plugin_paths": ("missing.py",)}, "not found"),
            ({"plugin_paths": ("../escape.py",)}, "outside the repository"),
            ({"plugin_paths": ("noreg.py",)}, "no register"),
            ({"plugin_paths": ("boom.py",)}, "failed to load"),
        ],
    )
    def test_errors(self, tmp_path, kwargs, message):
        (tmp_path / "noreg.py").write_text("x = 1\n")
        (tmp_path / "boom.py").write_text("raise RuntimeError('boom')\n")
        with pytest.raises(PluginError, match=message):
            default_registries(root=tmp_path, **kwargs)


class TestExecutors:
    def build(self, name, options=None):
        return default_registries().executors.get(name)(options)

    def test_is_executor(self):
        assert isinstance(self.build("pytest"), Executor)

    def test_pytest(self):
        ex = self.build("pytest")
        assert ex.command(ctx(markers="unit")) == 'pytest svc -m "unit"'
        assert ex.command(ctx()) == "pytest svc"
        assert ex.command(ctx(suite="lint")) == "ruff check svc"
        assert ex.command(ctx(suite="deploy")) is None

    def test_pytest_options(self):
        ex = self.build("pytest", {"launcher": "poetry run", "linter": "flake8"})
        assert ex.command(ctx(suite="lint")) == "poetry run flake8 svc"

    def test_uv_pytest(self):
        assert self.build("uv-pytest").command(ctx()) == "uv run --no-sync pytest svc"

    def test_paths_are_quoted(self):
        cmd = self.build("pytest").command(ctx(pkg(path="a b; rm")))
        assert cmd == "pytest 'a b; rm'"

    def test_go(self):
        ex = self.build("go-test")
        assert ex.command(ctx(markers="unit")) == "cd svc && go test -short ./..."
        assert ex.command(ctx(markers="integration")) == "cd svc && go test -tags integration ./..."
        assert ex.command(ctx(markers="not integration")) == "cd svc && go test ./..."
        assert ex.command(ctx(suite="lint")) == "cd svc && go vet ./..."

    def test_cargo(self):
        ex = self.build("cargo-test")
        assert ex.command(ctx(markers="unit")) == "cargo test --manifest-path svc/Cargo.toml --lib"
        assert ex.command(ctx(pkg(path="."))) == "cargo test --manifest-path Cargo.toml"
        assert ex.command(ctx(suite="lint")) == "cargo check --manifest-path svc/Cargo.toml"

    def test_pnpm_and_npm(self):
        assert self.build("pnpm").command(ctx()) == "pnpm --filter svc run test"
        assert (
            self.build("pnpm").command(ctx(markers="baseline"))
            == "pnpm --filter svc run test:baseline"
        )
        assert self.build("npm").command(ctx(suite="lint")) == "npm run lint --workspace svc"
        assert self.build("npm").command(ctx(suite="deploy")) is None

    def test_make(self):
        assert self.build("make", {"prefix": "ci-"}).command(ctx()) == "make -C svc ci-test"


def fake_cfg(root: Path, names=("a", "b", "c"), edges=None) -> SimpleNamespace:
    """a depends on b; c is independent. Only what the engines read."""
    from picard.graph import DependencyGraph

    packages = {n: pkg(n, n) for n in names}
    graph = DependencyGraph({n: (edges or {"a": ("b",)}).get(n, ()) for n in names})
    return SimpleNamespace(
        packages=packages,
        global_globs=("uv.lock",),
        narrow_lockfiles=(),
        root=root,
        dependency_graph=lambda: graph,
    )


class TestEngines:
    def engine(self, name, options=None):
        regs = default_registries()
        return regs.engines.get(name)(options, regs)

    def test_native(self, tmp_path):
        impact = self.engine("native").impact(fake_cfg(tmp_path), ["b/x.py"])
        assert impact.changed == ("b",)
        assert set(impact.affected) == {"a", "b"}
        assert impact.chain("a") == "a -> b"
        assert not impact.everything

    def test_global_file_affects_everything(self, tmp_path):
        impact = self.engine("native").impact(fake_cfg(tmp_path), ["uv.lock", "b/x.py"])
        assert impact.everything
        assert set(impact.affected) == {"a", "b", "c"}
        assert impact.global_files == ("uv.lock",)

    def test_is_engine(self):
        assert isinstance(self.engine("pants"), ImpactEngine)

    def script(self, tmp_path, body: str) -> list[str]:
        path = tmp_path / "eng.py"
        path.write_text(body)
        return [sys.executable, str(path)]

    def test_command_engine(self, tmp_path):
        argv = self.script(
            tmp_path,
            "import json;print(json.dumps({'changed':['c'],'affected':['c','a'],'notes':['hi']}))",
        )
        impact = self.engine("command", {"command": argv}).impact(fake_cfg(tmp_path), ["c/x"])
        assert impact.changed == ("c",)
        assert list(impact.affected) == ["a", "c"]
        assert impact.notes == ("hi",)

    def test_command_engine_receives_files_on_stdin(self, tmp_path):
        argv = self.script(
            tmp_path,
            "import sys,json;f=sys.stdin.read().split('\\0');"
            "print(json.dumps({'changed':[],'affected':[],'notes':f}))",
        )
        impact = self.engine("command", {"command": argv}).impact(
            fake_cfg(tmp_path), ["b/one.py", "c/two.py"]
        )
        assert impact.notes == ("b/one.py", "c/two.py")

    @pytest.mark.parametrize(
        "body",
        [
            "import sys;sys.exit(2)",
            "print('not json')",
            "import json;print(json.dumps({'changed':['ghost'],'affected':[]}))",
        ],
    )
    def test_command_engine_failure_falls_back_to_native(self, tmp_path, body):
        argv = self.script(tmp_path, body)
        impact = self.engine("command", {"command": argv}).impact(fake_cfg(tmp_path), ["b/x.py"])
        assert set(impact.affected) == {"a", "b"}  # native answer, never a smaller one
        assert "used the native engine" in impact.notes[-1]

    def test_command_engine_without_command_falls_back(self, tmp_path):
        impact = self.engine("command").impact(fake_cfg(tmp_path), ["b/x.py"])
        assert set(impact.affected) == {"a", "b"}
        assert "has no command" in impact.notes[-1]

    def fake_pants(self, tmp_path, owned: list[str], dependents: list[str], code=0) -> list[str]:
        return self.script(
            tmp_path,
            textwrap.dedent(
                f"""\
                import sys
                if {code}:
                    sys.stderr.write("bad"); sys.exit({code})
                if "dependents" in sys.argv:
                    print("\\n".join({dependents!r}))
                else:
                    print("\\n".join({owned!r}))
                """
            ),
        )

    def test_pants_narrows_python_and_falls_back_for_the_rest(self, tmp_path):
        argv = self.fake_pants(tmp_path, owned=["b/x.py:b"], dependents=["a/m.py"])
        engine = self.engine("pants", {"command": argv})
        impact = engine.impact(fake_cfg(tmp_path), ["b/x.py", "c/readme.js"])
        assert set(impact.affected) == {"a", "b", "c"}  # c came from the native fallback
        assert impact.changed == ("b", "c")
        assert impact.chain("b") == "b"
        assert any("not owned by pants" in n for n in impact.notes)

    def test_pants_failure_falls_back_with_note(self, tmp_path):
        argv = self.fake_pants(tmp_path, [], [], code=1)
        impact = self.engine("pants", {"command": argv}).impact(fake_cfg(tmp_path), ["b/x.py"])
        assert set(impact.affected) == {"a", "b"}
        assert "pants failed" in impact.notes[-1]

    def test_pants_missing_binary_falls_back(self, tmp_path):
        engine = self.engine("pants", {"command": "definitely-not-a-real-binary-xyz"})
        impact = engine.impact(fake_cfg(tmp_path), ["b/x.py"])
        assert set(impact.affected) == {"a", "b"}
        assert "not installed" in impact.notes[-1]

    def test_missing_pants_warns_once_and_says_how_to_ask_for_help(self, tmp_path, capsys):
        engine = self.engine("pants", {"command": "definitely-not-a-real-binary-xyz"})
        cfg = fake_cfg(tmp_path)
        engine.impact(cfg, ["b/x.py"])
        engine.impact(cfg, ["a/y.py"])
        err = capsys.readouterr().err
        assert err.count("warning: Pants is not installed") == 1
        assert "picard doctor --how-to-install" in err
        assert "brew install" not in err  # the install steps only appear when asked

    def test_a_pants_that_runs_but_fails_is_reported_as_a_failure(self, tmp_path):
        script = tmp_path / "boom.py"
        script.write_text("import sys\nsys.exit(3)\n")
        engine = self.engine("pants", {"command": [sys.executable, str(script)]})
        impact = engine.impact(fake_cfg(tmp_path), ["b/x.py"])
        assert set(impact.affected) == {"a", "b"}
        assert "pants failed" in impact.notes[-1]


class TestPantsDebug:
    def run(self, tmp_path, capsys, options, files):
        regs = default_registries()
        script = tmp_path / "p.py"
        script.write_text(
            "import sys\nprint('b/x.py:b' if 'dependents' not in sys.argv else 'a/m.py')\n"
        )
        engine = regs.engines.get("pants")(
            {"command": [sys.executable, str(script)], **options}, regs
        )
        engine.impact(fake_cfg(tmp_path), files)
        return capsys.readouterr().err

    def test_silent_by_default(self, tmp_path, capsys):
        assert self.run(tmp_path, capsys, {}, ["b/x.py"]) == ""

    def test_debug_option_shows_both_answers(self, tmp_path, capsys):
        err = self.run(tmp_path, capsys, {"debug": True}, ["b/x.py"])
        assert "pants list -> 1 owned file(s): b/x.py" in err
        assert "pants dependents --transitive -> 1: a/m.py" in err
        assert "native engine:" in err and "same packages from both engines" in err

    def test_debug_env_and_difference(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("PICARD_DEBUG", "1")
        err = self.run(tmp_path, capsys, {}, ["b/x.py", "c/y.js"])
        assert "[picard:debug:pants]" in err

    def test_difference_is_reported(self, tmp_path, capsys):
        script = tmp_path / "q.py"
        script.write_text("import sys\nprint('b/x.py:b' if 'dependents' not in sys.argv else '')\n")
        regs = default_registries()
        engine = regs.engines.get("pants")(
            {"command": [sys.executable, str(script)], "debug": True}, regs
        )
        engine.impact(fake_cfg(tmp_path), ["b/x.py"])
        assert "DIFFERENCE only in pants: []; only in native: ['a']" in capsys.readouterr().err


def test_impact_is_computed_once_per_run(tmp_path):
    from picard.graph import compute_impact

    calls = tmp_path / "calls"
    script = tmp_path / "e.py"
    script.write_text(
        f"import json;open({str(calls)!r},'a').write('x')\n"
        "print(json.dumps({'changed':['b'],'affected':['b']}))\n"
    )
    regs = default_registries()
    cfg = fake_cfg(tmp_path)
    cfg.registries = regs
    cfg.impact = SimpleNamespace(
        engine="command", options={"command": [sys.executable, str(script)]}
    )
    first = compute_impact(cfg, ["b/x.py"])
    assert compute_impact(cfg, ["b/x.py"]) is first
    compute_impact(cfg, ["c/y.py"])
    assert calls.read_text() == "xx"  # one call per distinct file list


def test_pants_keeps_real_dependency_chain(tmp_path):
    regs = default_registries()
    script = tmp_path / "p.py"
    script.write_text(
        "import sys\nprint('b/x.py:b' if 'dependents' not in sys.argv else 'a/m.py')\n"
    )
    engine = regs.engines.get("pants")({"command": [sys.executable, str(script)]}, regs)
    impact = engine.impact(fake_cfg(tmp_path), ["b/x.py"])
    assert impact.chain("a") == "a -> b"


class TestDecidedBy:
    def engine(self, name, options=None):
        regs = default_registries()
        return regs.engines.get(name)(options, regs)

    def test_native_labels_everything_it_decided(self, tmp_path):
        impact = self.engine("native").impact(fake_cfg(tmp_path), ["b/x.py"])
        assert impact.decided_by == {"a": "native", "b": "native"}

    def test_global_file_is_labelled(self, tmp_path):
        impact = self.engine("native").impact(fake_cfg(tmp_path), ["uv.lock"])
        assert set(impact.decided_by.values()) == {"global file"}

    def test_pants_labels_what_it_answered_and_what_the_fallback_answered(self, tmp_path):
        script = tmp_path / "p.py"
        script.write_text(
            "import sys\nprint('b/x.py:b' if 'dependents' not in sys.argv else 'a/m.py')\n"
        )
        engine = self.engine("pants", {"command": [sys.executable, str(script)]})
        impact = engine.impact(fake_cfg(tmp_path), ["b/x.py", "c/y.js"])
        assert impact.decided_by == {"a": "pants", "b": "pants", "c": "native"}

    def test_failed_pants_is_labelled_native(self, tmp_path):
        engine = self.engine("pants", {"command": "definitely-not-a-real-binary-xyz"})
        impact = engine.impact(fake_cfg(tmp_path), ["b/x.py"])
        assert set(impact.decided_by.values()) == {"native"}

    def test_render_shows_provider_and_who_decided(self, tmp_path):
        from picard.graph.render import render_tree, render_why

        cfg = fake_cfg(tmp_path)
        cfg.packages["a"] = pkg("a", "a", provider="uv")
        impact = self.engine("native").impact(cfg, ["b/x.py"])
        tree = render_tree(cfg, impact)
        assert "a  a  [uv]  via native" in tree
        untouched = next(line for line in tree.splitlines() if "c  c" in line)
        assert "via" not in untouched
        assert "a is affected through a -> b (via native)" in render_why(impact)
