"""`picard setup`: engines prepare the repository, `--check` verifies it. Pants is faked."""

import sys
import textwrap
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from picard.bootstrap import default_registries
from picard.cli import main
from picard.config import Package
from picard.graph.engines.pants_setup import render_config, source_roots
from picard.graph.impact import SetupError

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def uv(name: str, path: str) -> Package:
    return Package(name, path, (), False, {}, provider="uv")


def make_repo(root: Path) -> SimpleNamespace:
    for path in ("packages/lib/src/lib", "apps/api/src/api"):
        (root / path).mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "x"\nrequires-python = ">=3.12"\n')
    return SimpleNamespace(
        root=root,
        packages={"lib": uv("lib", "packages/lib"), "api": uv("api", "apps/api")},
    )


def fake_tool(tmp_path: Path, *, tailor_check_exit: int = 0, requirements_file: bool = False):
    """A `pants` stand-in: `tailor` writes build files, `tailor --check` exits as told."""
    script = tmp_path / "tool.py"
    script.write_text(
        textwrap.dedent(
            f"""\
            import pathlib, sys
            args = sys.argv[1:]
            if args[0] == "tailor" and "--check" in args:
                print("Would create apps/api/BUILD.pants:\\n  - Add python_tests target tests")
                print("To fix `tailor` failures, run `pants tailor`.")
                sys.exit({tailor_check_exit})
            if args[0] == "tailor":
                pathlib.Path("apps/api/src/api/BUILD.pants").write_text("python_sources()\\n")
                if {requirements_file!r}:
                    pathlib.Path("apps/api/BUILD.pants").write_text(
                        'python_requirements(name="reqs")\\n'
                    )
            """
        )
    )
    return [sys.executable, str(script)]


def engine(tmp_path: Path, **tool):
    regs = default_registries()
    options = {"command": fake_tool(tmp_path, **tool)}
    return regs.engines.get("pants")(options, regs)


class Said(list):
    def __call__(self, message: str) -> None:
        self.append(message)


class TestRenderedConfig:
    def test_source_roots(self, tmp_path):
        cfg = make_repo(tmp_path)
        assert source_roots(tmp_path, cfg.packages.values()) == [
            "/packages/lib/src",
            "/apps/api/src",
            "/",
        ]

    def test_package_without_src_uses_its_directory(self, tmp_path):
        (tmp_path / "svc").mkdir()
        assert source_roots(tmp_path, [uv("svc", "svc")]) == ["/svc", "/"]

    def test_root_package(self, tmp_path):
        assert source_roots(tmp_path, [uv("root", ".")]) == ["/"]

    def test_is_valid_toml_with_the_options_that_matter(self):
        data = tomllib.loads(render_config("2.33.1", ["/a/src", "/"], ">=3.12"))
        assert data["GLOBAL"]["pants_version"] == "2.33.1"
        assert data["source"]["root_patterns"] == ["/a/src", "/"]
        assert data["python"]["tailor_requirements_targets"] is False
        assert data["python"]["interpreter_constraints"] == [">=3.12"]
        assert data["python-infer"]["unowned_dependency_behavior"] == "ignore"
        assert data["tailor"]["build_file_name"] == "BUILD.pants"

    def test_no_constraint_when_the_repo_has_none(self):
        assert "interpreter_constraints" not in render_config("2.33.1", ["/"], None)


class TestSetup:
    def test_writes_everything_once(self, tmp_path):
        cfg, said = make_repo(tmp_path), Said()
        engine(tmp_path).setup(cfg, say=said)
        config = tomllib.loads((tmp_path / "pants.toml").read_text())
        assert config["python"]["interpreter_constraints"] == [">=3.12"]
        assert (tmp_path / "apps/api/src/api/BUILD.pants").is_file()
        assert (tmp_path / ".gitignore").read_text() == ".pants.d/\n.pids/\ndist/\n"
        assert "wrote pants.toml" in said

    def test_is_idempotent_and_never_overwrites_the_config(self, tmp_path):
        cfg, said = make_repo(tmp_path), Said()
        (tmp_path / "pants.toml").write_text("# mine\n")
        (tmp_path / ".gitignore").write_text("node_modules\n.pants.d/")
        engine(tmp_path).setup(cfg, say=said)
        engine(tmp_path).setup(cfg, say=said)
        assert (tmp_path / "pants.toml").read_text() == "# mine\n"
        assert (tmp_path / ".gitignore").read_text() == "node_modules\n.pants.d/\n.pids/\ndist/\n"
        assert "pants.toml already exists: left as it is" in said

    def test_third_party_target_files_are_removed_with_advice(self, tmp_path):
        cfg, said = make_repo(tmp_path), Said()
        engine(tmp_path, requirements_file=True).setup(cfg, say=said)
        assert not (tmp_path / "apps/api/BUILD.pants").exists()
        assert any("tailor_requirements_targets = false" in m for m in said)

    def test_tool_failure_is_a_setup_error(self, tmp_path):
        cfg = make_repo(tmp_path)
        regs = default_registries()
        broken = regs.engines.get("pants")({"command": "definitely-not-a-real-tool-xyz"}, regs)
        with pytest.raises(SetupError, match="not installed"):
            broken.setup(cfg, say=Said())

    def test_nothing_to_set_up_without_python_packages(self, tmp_path):
        cfg = SimpleNamespace(
            root=tmp_path, packages={"ui": Package("ui", "ui", (), False, {}, provider="npm")}
        )
        said = Said()
        engine(tmp_path).setup(cfg, say=said)
        assert not (tmp_path / "pants.toml").exists()
        assert "nothing to set up" in said[0]


class TestCheck:
    def test_missing_config(self, tmp_path):
        with pytest.raises(SetupError, match="pants.toml is missing: run `picard setup`"):
            engine(tmp_path).setup(make_repo(tmp_path), check=True, say=Said())

    def test_complete(self, tmp_path):
        cfg, said = make_repo(tmp_path), Said()
        (tmp_path / "pants.toml").write_text("")
        engine(tmp_path).setup(cfg, check=True, say=said)
        assert said == ["build files are complete"]

    def test_out_of_date_names_what_is_missing_but_not_the_tool(self, tmp_path):
        cfg = make_repo(tmp_path)
        (tmp_path / "pants.toml").write_text("")
        with pytest.raises(SetupError) as err:
            engine(tmp_path, tailor_check_exit=1).setup(cfg, check=True, say=Said())
        text = str(err.value)
        assert "run `picard setup`" in text and "Would create apps/api/BUILD.pants" in text
        assert "pants tailor" not in text


class TestOtherEngines:
    def test_native_needs_no_setup(self):
        regs = default_registries()
        said = Said()
        regs.engines.get("native")({}, regs).setup(SimpleNamespace(), say=said)
        assert said == ["the 'native' engine needs no setup"]

    def test_cli_runs_the_configured_engines_setup(self, capsys):
        assert main(["setup", "--config", str(TEMPLATE)]) == 0
        assert "needs no setup" in capsys.readouterr().out

    def test_cli_reports_a_failed_check_with_exit_code_one_or_more(self, tmp_path, capsys):
        config = tmp_path / "pipeline.toml"
        config.write_text(
            TEMPLATE.read_text() + '\n[impact]\nengine = "pants"\ncommand = ["false"]\n'
        )
        assert main(["setup", "--check", "--config", str(config)]) != 0
        assert capsys.readouterr().err.startswith("picard:")


class TestDoctor:
    """`picard doctor` says whether the configured engine can run, and how to install it on request."""

    def config(self, tmp_path, engine, command=None):
        text = TEMPLATE.read_text(encoding="utf-8")
        extra = f'command = "{command}"\n' if command else ""
        (tmp_path / "pipeline.toml").write_text(
            text + f'\n[impact]\nengine = "{engine}"\n{extra}', encoding="utf-8"
        )
        return str(tmp_path / "pipeline.toml")

    def test_native_needs_nothing(self, tmp_path, capsys):
        assert main(["doctor", "--config", self.config(tmp_path, "native")]) == 0
        assert "ready" in capsys.readouterr().out

    def test_missing_pants_is_a_warning_and_exit_1(self, tmp_path, capsys):
        config = self.config(tmp_path, "pants", "definitely-not-a-real-tool-xyz")
        assert main(["doctor", "--config", config]) == 1
        captured = capsys.readouterr()
        assert "warning: Pants is not installed" in captured.err
        assert "doctor --how-to-install" in captured.err
        assert "brew install" not in captured.out

    def test_how_to_install_prints_the_steps(self, tmp_path, capsys):
        config = self.config(tmp_path, "pants", "definitely-not-a-real-tool-xyz")
        assert main(["doctor", "--how-to-install", "--config", config]) == 1
        out = capsys.readouterr().out
        assert "brew install pantsbuild/tap/pants" in out
        assert "get-pants.sh" in out
        assert 'engine = "native"' in out

    def test_an_installed_launcher_is_ready(self, tmp_path, capsys):
        config = self.config(tmp_path, "pants", sys.executable)
        assert main(["doctor", "--config", config]) == 0
        assert "ready" in capsys.readouterr().out
