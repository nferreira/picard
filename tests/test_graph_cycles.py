"""Dependency cycles: refused by default, accepted on request, and walked safely.

Real workspaces have them: a package that lists a sibling in a dev or test group while that
sibling depends on it for real. `[graph] cycles = "allow"` keeps every edge (dropping one would
run less than the truth) and the graph walks, renderers and lockfile narrowing must stay finite.
"""

import re
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import graph, lockfiles
from picard.cli import main
from picard.graph import render

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def pkg(name, *deps):
    return {"name": name, "path": name, "depends_on": list(deps)}


def config(packages, graph_table=None):
    data = tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["packages"] = packages
    if graph_table is not None:
        data["graph"] = graph_table
    return cfgmod.parse(data)


TWO_CYCLE = [pkg("a", "b"), pkg("b", "a"), pkg("app", "a"), pkg("lib")]
THREE_CYCLE = [pkg("a", "b"), pkg("b", "c"), pkg("c", "a"), pkg("d", "c")]
ALLOW = {"cycles": "allow"}


def affected(cfg, *files):
    return set(graph.compute_impact(cfg, list(files)).affected)


# ── the default does not change ────────────────────────────────────────────────────────


def test_a_cycle_is_refused_by_default_with_the_same_trail():
    with pytest.raises(
        cfgmod.ConfigError, match=re.escape("package dependency cycle: a -> b -> a")
    ):
        config(TWO_CYCLE)


def test_the_refusal_says_how_to_accept_cycles():
    with pytest.raises(cfgmod.ConfigError, match=re.escape('[graph] cycles = "allow"')):
        config(TWO_CYCLE)


def test_choosing_error_explicitly_is_the_default():
    with pytest.raises(cfgmod.ConfigError, match="cycle"):
        config(TWO_CYCLE, {"cycles": "error"})


def test_an_acyclic_graph_loads_either_way_and_reports_no_cycles():
    packages = [pkg("a", "b"), pkg("b"), pkg("c", "a")]
    assert config(packages).cycles_found == ()
    assert config(packages, ALLOW).cycles_found == ()


# ── validation of the new table ────────────────────────────────────────────────────────


def test_cycles_must_be_a_known_value():
    with pytest.raises(cfgmod.ConfigError, match=r"\[graph\] cycles must be one of"):
        config(TWO_CYCLE, {"cycles": "ignore"})


def test_an_unknown_key_in_graph_is_named():
    with pytest.raises(cfgmod.ConfigError, match=r"\[graph\] has unknown key 'cycle'"):
        config(TWO_CYCLE, {"cycle": "allow"})


def test_graph_must_be_a_table():
    with pytest.raises(cfgmod.ConfigError, match=r"\[graph\] must be a table"):
        config(TWO_CYCLE, "allow")


# ── allowed cycles are reported and walked ─────────────────────────────────────────────


def test_allowed_cycles_are_reported_in_package_order():
    assert config(TWO_CYCLE, ALLOW).cycles_found == (("a", "b"),)
    assert config(THREE_CYCLE, ALLOW).cycles_found == (("a", "b", "c"),)


def test_a_self_dependency_is_a_cycle_of_one():
    cfg = config([pkg("a", "a"), pkg("b", "a")], ALLOW)
    assert cfg.cycles_found == (("a",),)
    assert affected(cfg, "a/x.py") == {"a", "b"}


def test_a_change_in_one_member_reaches_the_whole_cycle_and_its_dependents():
    cfg = config(TWO_CYCLE, ALLOW)
    assert affected(cfg, "b/x.py") == {"a", "b", "app"}
    assert affected(cfg, "app/x.py") == {"app"}
    assert affected(cfg, "lib/x.py") == {"lib"}


def test_a_three_member_cycle_with_an_outside_dependent():
    cfg = config(THREE_CYCLE, ALLOW)
    assert affected(cfg, "a/x.py") == {"a", "b", "c", "d"}
    assert affected(cfg, "d/x.py") == {"d"}


def test_find_cycles_lists_each_cycle_once():
    found = graph.find_cycles(
        {"a": ("b",), "b": ("a",), "c": ("d",), "d": ("e",), "e": ("c",), "f": ("a",), "g": ()}
    )
    assert found == (("a", "b"), ("c", "d", "e"))
    assert graph.find_cycles({"a": ("b",), "b": ()}) == ()


def test_find_cycles_survives_a_long_chain():
    chain = {f"p{i}": (f"p{i + 1}",) for i in range(5000)}
    chain["p5000"] = ("p0",)
    (only,) = graph.find_cycles(chain)
    assert len(only) == 5001


# ── renderers terminate ────────────────────────────────────────────────────────────────


def test_the_tree_terminates_and_marks_the_way_back():
    cfg = config(THREE_CYCLE, ALLOW)
    text = render.render_tree(cfg)
    for name in ("a", "b", "c", "d"):
        assert f" {name}  {name}" in text
    assert "cycle" in text


def test_the_tree_of_a_graph_made_only_of_cycles_still_prints():
    cfg = config([pkg("a", "b"), pkg("b", "a")], ALLOW)
    assert render.render_tree(cfg).count("\n") >= 2


def test_mermaid_lists_every_edge_of_a_cycle():
    text = render.render_mermaid(config(TWO_CYCLE, ALLOW))
    assert text.count("-->") == 3


# ── the command line ───────────────────────────────────────────────────────────────────

PACKAGES_BLOCK = re.compile(r"\[\[packages\]\]\nname = \"app\".*?\ntest = .*?\n", re.DOTALL)


def write_config(tmp_path, *, allow):
    text = TEMPLATE.read_text(encoding="utf-8")
    blocks = (
        '[[packages]]\nname = "a"\npath = "a"\ndepends_on = ["b"]\n'
        '[packages.commands]\ntest = "true"\n\n'
        '[[packages]]\nname = "b"\npath = "b"\ndepends_on = ["a"]\n'
        '[packages.commands]\ntest = "true"\n'
    )
    text = PACKAGES_BLOCK.sub(lambda _: blocks, text, count=1)
    assert 'name = "a"' in text
    if allow:
        text += '\n[graph]\ncycles = "allow"\n'
    path = tmp_path / "pipeline.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_validate_config_accepts_allowed_cycles_and_says_so(tmp_path, capsys):
    path = write_config(tmp_path, allow=True)
    assert main(["validate-config", "--config", str(path)]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("ok: 2 package(s)")
    assert "dependency cycle" in err
    assert "a -> b -> a" in err


def test_validate_config_refuses_cycles_without_the_option(tmp_path, capsys):
    path = write_config(tmp_path, allow=False)
    assert main(["validate-config", "--config", str(path)]) != 0
    assert 'cycles = "allow"' in capsys.readouterr().err


# ── lockfile narrowing ─────────────────────────────────────────────────────────────────


def uv_lock(ext):
    return f"""\
version = 1
requires-python = ">=3.11"

[manifest]
members = ["a", "b"]

[[package]]
name = "a"
version = "0.1.0"
source = {{ editable = "a" }}
dependencies = [{{ name = "b" }}, {{ name = "ext" }}]

[[package]]
name = "b"
version = "0.1.0"
source = {{ editable = "b" }}
dependencies = [{{ name = "a" }}]

[[package]]
name = "ext"
version = "{ext}"
source = {{ registry = "https://pypi.org/simple" }}
"""


def test_lockfile_narrowing_terminates_on_members_that_depend_on_each_other():
    change = lockfiles.diff("uv.lock", uv_lock("1.0"), uv_lock("2.0"))
    assert change.members["a"] == ("ext",)
    assert set(change.members) <= {"a", "b"}


def test_with_dependents_is_finite_on_a_cycle():
    walk = graph.DependencyGraph({"a": ("b",), "b": ("a",), "c": ("a",)})
    assert walk.with_dependents(["a"]) == {"a": ("a",), "b": ("b", "a"), "c": ("c", "a")}
