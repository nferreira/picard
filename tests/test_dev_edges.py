"""Dev-group edges: kept for impact, never counted as a dependency cycle.

A package that lists a sibling in a dev or test group while that sibling depends on it for real
is a normal workspace. The edge is true (the first package's tests need the sibling), so a change
to the sibling still selects it, but it is not something the package needs to run, so it must not
make a cycle. Runtime edges, extras and edges a repository declares by hand still do.
"""

import json
import re
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import graph
from picard.cli import main
from picard.graph import Discovered

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def uv_package(name, *, dependencies=(), extras=(), dev=()):
    text = f'[[package]]\nname = "{name}"\nversion = "0.1.0"\n'
    text += f'source = {{ editable = "packages/{name}" }}\n'
    if dependencies:
        text += "dependencies = [" + ", ".join(f'{{ name = "{d}" }}' for d in dependencies) + "]\n"
    if extras:
        text += "\n[package.optional-dependencies]\nextra = ["
        text += ", ".join(f'{{ name = "{d}" }}' for d in extras) + "]\n"
    if dev:
        text += "\n[package.dev-dependencies]\ntest = ["
        text += ", ".join(f'{{ name = "{d}" }}' for d in dev) + "]\n"
    return text + "\n"


def uv_lock(*packages):
    names = ", ".join(f'"{n}"' for n, _ in packages)
    return (
        f'version = 1\nrequires-python = ">=3.11"\n\n[manifest]\nmembers = [{names}]\n\n'
        + "".join(uv_package(n, **kw) for n, kw in packages)
    )


def config(tmp_path, *packages, extra=None, manual=None):
    write(tmp_path, "uv.lock", uv_lock(*packages))
    data = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["packages"] = manual or []
    data["discover"] = [{"provider": "uv"}]
    data.update(extra or {})
    return cfgmod.parse(data, root=tmp_path)


# An adapter that depends on its core for real, and a core whose tests use the adapter.
ADAPTER = ("adapter", {"dependencies": ["core"]})
CORE_DEV = ("core", {"dev": ["adapter"]})


def affected(cfg, *files):
    return set(graph.compute_impact(cfg, list(files)).affected)


# ── uv ────────────────────────────────────────────────────────────────────────────────


def test_a_dev_group_back_edge_is_not_a_cycle_and_needs_no_option(tmp_path):
    cfg = config(tmp_path, ADAPTER, CORE_DEV)
    assert cfg.cycles == "error"
    assert cfg.cycles_found == ()
    core = cfg.packages["core"]
    assert core.dev_depends_on == ("adapter",)
    assert core.runtime_depends_on == ()
    assert core.depends_on == ("adapter",)  # the edge is still there, for impact


def test_a_change_still_reaches_the_package_whose_tests_use_it(tmp_path):
    cfg = config(tmp_path, ADAPTER, CORE_DEV)
    assert affected(cfg, "packages/adapter/x.py") == {"adapter", "core"}
    assert affected(cfg, "packages/core/x.py") == {"adapter", "core"}


def test_a_runtime_cycle_is_still_refused_with_the_same_trail(tmp_path):
    with pytest.raises(cfgmod.ConfigError, match=re.escape("package dependency cycle: ")):
        config(tmp_path, ADAPTER, ("core", {"dependencies": ["adapter"]}))


def test_an_edge_that_is_also_a_runtime_dependency_stays_runtime(tmp_path):
    both = ("core", {"dependencies": ["adapter"], "dev": ["adapter"]})
    with pytest.raises(cfgmod.ConfigError, match="dependency cycle"):
        config(tmp_path, ADAPTER, both)


def test_an_extra_is_an_optional_runtime_dependency(tmp_path):
    with pytest.raises(cfgmod.ConfigError, match="dependency cycle"):
        config(tmp_path, ADAPTER, ("core", {"extras": ["adapter"]}))


def test_an_edge_declared_by_hand_over_a_dev_edge_is_a_runtime_edge(tmp_path):
    manual = [{"name": "core", "depends_on": ["adapter"]}]
    with pytest.raises(cfgmod.ConfigError, match="dependency cycle"):
        config(tmp_path, ADAPTER, CORE_DEV, manual=manual)


def test_allow_reports_only_runtime_cycles(tmp_path):
    cfg = config(
        tmp_path,
        ("a", {"dependencies": ["b"]}),
        ("b", {"dependencies": ["a"]}),
        ADAPTER,
        CORE_DEV,
        extra={"graph": {"cycles": "allow"}},
    )
    assert cfg.cycles_found == (("a", "b"),)


# ── the other ecosystems ───────────────────────────────────────────────────────────────


def npm_config(tmp_path, a_dev=False):
    write(tmp_path, "package.json", json.dumps({"workspaces": ["pkgs/*"]}))
    write(
        tmp_path,
        "pkgs/a/package.json",
        json.dumps({"name": "a", "dependencies": {"b": "*"}}),
    )
    field = "devDependencies"
    write(tmp_path, "pkgs/b/package.json", json.dumps({"name": "b", field: {"a": "*"}}))
    data = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["packages"] = []
    data["discover"] = [{"provider": "npm"}]
    return cfgmod.parse(data, root=tmp_path)


def test_an_npm_dev_dependency_back_edge_is_not_a_cycle(tmp_path):
    cfg = npm_config(tmp_path)
    assert cfg.packages["b"].dev_depends_on == ("a",)
    assert cfg.cycles_found == ()
    assert affected(cfg, "pkgs/a/x.js") == {"a", "b"}


@pytest.mark.parametrize("table", ["[dev-dependencies]", "[target.'cfg(unix)'.dev-dependencies]"])
def test_a_cargo_dev_dependency_back_edge_is_not_a_cycle(tmp_path, table):
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
    write(
        tmp_path,
        "crates/a/Cargo.toml",
        '[package]\nname = "a"\n[dependencies]\nb = { path = "../b" }\n',
    )
    write(
        tmp_path,
        "crates/b/Cargo.toml",
        f'[package]\nname = "b"\n{table}\na = {{ path = "../a" }}\n',
    )
    data = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["packages"] = []
    data["discover"] = [{"provider": "cargo"}]
    cfg = cfgmod.parse(data, root=tmp_path)
    assert cfg.packages["b"].dev_depends_on == ("a",)
    assert cfg.cycles_found == ()


# ── compatibility ──────────────────────────────────────────────────────────────────────


def test_a_provider_that_knows_nothing_of_dev_edges_still_works():
    found = Discovered("a", "a", ("b",), "custom")  # the shape every provider had before
    assert found.dev_depends_on == ()


# ── the command line ───────────────────────────────────────────────────────────────────

DISCOVER = re.compile(r"\[\[packages\]\]\nname = \"app\".*?\ntest = .*?\n", re.DOTALL)


def test_validate_config_is_quiet_about_a_dev_only_loop(tmp_path, monkeypatch, capsys):
    write(tmp_path, "uv.lock", uv_lock(ADAPTER, CORE_DEV))
    text = DISCOVER.sub(lambda _: '[[discover]]\nprovider = "uv"\n', TEMPLATE.read_text("utf-8"), 1)
    write(tmp_path, ".picard/pipeline.toml", text)
    monkeypatch.chdir(tmp_path)
    assert main(["validate-config"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("ok: 2 package(s)")
    assert "cycle" not in err
