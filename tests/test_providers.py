"""Cargo, Go and command graph providers: parsed from fixtures, no toolchain needed."""

import sys
import textwrap
from pathlib import Path

import pytest

from picard.graph.model import DiscoveryError
from picard.graph.providers import CargoProvider, CommandProvider, GoProvider

pytestmark = [pytest.mark.unit, pytest.mark.baseline]


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def by_name(found):
    return {d.name: d for d in found}


class TestCargo:
    def make(self, root: Path) -> None:
        write(root, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\nexclude = ["crates/old"]\n')
        write(root, "crates/core/Cargo.toml", '[package]\nname = "core"\n')
        write(
            root,
            "crates/api/Cargo.toml",
            """\
            [package]
            name = "api"
            [dependencies]
            serde = "1"
            kernel = { path = "../core", package = "core" }
            [target.'cfg(unix)'.dev-dependencies]
            cli = { path = "../cli" }
            """,
        )
        write(
            root,
            "crates/cli/Cargo.toml",
            '[package]\nname = "cli"\n[build-dependencies]\napi = { path = "../api" }\n',
        )
        write(root, "crates/old/Cargo.toml", '[package]\nname = "old"\n')

    def test_members_and_edges(self, tmp_path):
        self.make(tmp_path)
        found = by_name(CargoProvider().discover(tmp_path))
        assert set(found) == {"core", "api", "cli"}
        assert found["core"].path == "crates/core"
        assert found["api"].depends_on == ("cli", "core")  # rename and target table honoured
        assert found["cli"].depends_on == ("api",)
        assert found["api"].provider == "cargo"

    def test_no_workspace_table(self, tmp_path):
        write(tmp_path, "Cargo.toml", '[package]\nname = "x"\n')
        with pytest.raises(DiscoveryError, match="no \\[workspace\\]"):
            CargoProvider().discover(tmp_path)

    def test_missing_manifest(self, tmp_path):
        with pytest.raises(DiscoveryError, match="not found"):
            CargoProvider().discover(tmp_path)

    def test_member_without_package_name(self, tmp_path):
        write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["a"]\n')
        write(tmp_path, "a/Cargo.toml", "[lib]\n")
        with pytest.raises(DiscoveryError, match="no \\[package\\] name"):
            CargoProvider().discover(tmp_path)

    def test_unsafe_name_rejected(self, tmp_path):
        write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["a"]\n')
        write(tmp_path, "a/Cargo.toml", '[package]\nname = "a; rm -rf ~"\n')
        with pytest.raises(DiscoveryError, match="unsafe package name"):
            CargoProvider().discover(tmp_path)


class TestGo:
    def test_go_work_with_block_and_single_use(self, tmp_path):
        write(tmp_path, "go.work", "go 1.22\nuse (\n\t./svc // main\n\t./lib\n)\nuse ./tools\n")
        write(
            tmp_path,
            "svc/go.mod",
            """\
            module example.com/svc
            require (
                example.com/lib v0.0.0
                github.com/other/dep v1.2.3
            )
            """,
        )
        write(tmp_path, "lib/go.mod", "module example.com/lib\n")
        write(
            tmp_path,
            "tools/go.mod",
            "module example.com/tools\nrequire example.com/svc v0.0.0\n",
        )
        found = by_name(GoProvider().discover(tmp_path))
        assert set(found) == {"example.com/svc", "example.com/lib", "example.com/tools"}
        assert found["example.com/svc"].path == "svc"
        assert found["example.com/svc"].depends_on == ("example.com/lib",)
        assert found["example.com/tools"].depends_on == ("example.com/svc",)

    def test_replace_directive_counts_as_dependency(self, tmp_path):
        write(tmp_path, "go.work", "use ./a\nuse ./b\n")
        write(tmp_path, "a/go.mod", "module a.dev/a\nreplace a.dev/b => ../b\n")
        write(tmp_path, "b/go.mod", "module a.dev/b\n")
        found = by_name(GoProvider().discover(tmp_path))
        assert found["a.dev/a"].depends_on == ("a.dev/b",)

    def test_without_go_work_finds_every_go_mod_but_skips_vendor(self, tmp_path):
        write(tmp_path, "one/go.mod", "module m.dev/one\n")
        write(tmp_path, "two/go.mod", "module m.dev/two\nrequire m.dev/one v0.0.0\n")
        write(tmp_path, "one/vendor/x/go.mod", "module m.dev/vendored\n")
        found = by_name(GoProvider().discover(tmp_path))
        assert set(found) == {"m.dev/one", "m.dev/two"}
        assert found["m.dev/two"].depends_on == ("m.dev/one",)

    def test_listed_module_missing(self, tmp_path):
        write(tmp_path, "go.work", "use ./ghost\n")
        with pytest.raises(DiscoveryError, match="not found"):
            GoProvider().discover(tmp_path)

    def test_no_modules(self, tmp_path):
        with pytest.raises(DiscoveryError, match="no go.mod"):
            GoProvider().discover(tmp_path)

    def test_go_mod_without_module_line(self, tmp_path):
        write(tmp_path, "a/go.mod", "go 1.22\n")
        with pytest.raises(DiscoveryError, match="no module line"):
            GoProvider().discover(tmp_path)


class TestCommand:
    def provider(self, script: str) -> CommandProvider:
        return CommandProvider({"command": [sys.executable, "-c", script]})

    def test_reads_json(self, tmp_path):
        script = (
            "import json;print(json.dumps({'packages':["
            "{'name':'a','path':'libs/a/','depends_on':['b']},{'name':'b','path':'libs/b'}]}))"
        )
        found = by_name(self.provider(script).discover(tmp_path))
        assert found["a"].path == "libs/a"
        assert found["a"].depends_on == ("b",)
        assert found["b"].depends_on == ()

    def test_string_command_is_split(self, tmp_path):
        write(tmp_path, "g.py", 'print(\'{"packages": [{"name": "a", "path": "a"}]}\')\n')
        provider = CommandProvider({"command": f"{sys.executable} g.py"})
        assert [d.name for d in provider.discover(tmp_path)] == ["a"]

    def test_needs_command(self, tmp_path):
        with pytest.raises(DiscoveryError, match="needs `command"):
            CommandProvider().discover(tmp_path)

    def test_nonzero_exit(self, tmp_path):
        with pytest.raises(DiscoveryError, match="exited 3"):
            self.provider("import sys;sys.exit(3)").discover(tmp_path)

    def test_missing_executable(self, tmp_path):
        with pytest.raises(DiscoveryError, match="cannot run"):
            CommandProvider({"command": ["definitely-not-a-real-binary-xyz"]}).discover(tmp_path)

    @pytest.mark.parametrize("output", ["not json", "{}", '{"packages": [1]}'])
    def test_bad_output(self, tmp_path, output):
        with pytest.raises(DiscoveryError, match="expected JSON"):
            self.provider(f"print({output!r})").discover(tmp_path)

    def test_unknown_dependency(self, tmp_path):
        script = 'print(\'{"packages":[{"name":"a","path":"a","depends_on":["zz"]}]}\')'
        with pytest.raises(DiscoveryError, match="unknown package"):
            self.provider(script).discover(tmp_path)
