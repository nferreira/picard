import copy
import re
import tomllib
from pathlib import Path

import pytest

from picard import config as cfgmod

pytestmark = pytest.mark.unit

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"


def raw():
    return tomllib.loads(TEMPLATE.read_text(encoding="utf-8"))


def test_template_config_is_valid():
    cfg = cfgmod.load(TEMPLATE)
    assert set(cfg.tiers) == {0, 1, 2, 3, 4}
    assert cfg.profiles["docs"].rank < cfg.profiles["full"].rank


def _mutate(fn):
    data = copy.deepcopy(raw())
    fn(data)
    return data


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        (lambda d: d["types"]["feat"].update(profile="nope"), "unknown profile 'nope'"),
        (lambda d: d["types"].pop("chore"), "no [types.<name>] rule for: chore"),
        (lambda d: d["types"].update(wat={"profile": "full"}), "not listed in [commits] types"),
        (lambda d: d["types"]["feat"].update(release="huge"), "release must be one of"),
        (lambda d: d["profiles"]["full"]["extras"].update({"2": ["magic"]}), "unknown extra"),
        (lambda d: d["packages"][0].update(depends_on=["ghost"]), "unknown package 'ghost'"),
        (lambda d: d["tiers"]["1"].update(compare="sideways"), "compare must be one of"),
        (lambda d: d["tiers"]["1"].update(packages="some"), "packages must be one of"),
        (lambda d: d["tiers"]["1"].update(markers='unit"; rm -rf /'), "markers may only contain"),
        (lambda d: d["overrides"][0].update(match="(unclosed"), "bad regex"),
        (lambda d: d.update(packages=[]), "at least one package"),
    ],
)
def test_invalid_configs_fail_loudly(mutation, fragment):
    with pytest.raises(
        cfgmod.ConfigError,
        match=fragment.replace("[", r"\[")
        .replace("]", r"\]")
        .replace("(", r"\(")
        .replace(")", r"\)"),
    ):
        cfgmod.parse(_mutate(mutation))


def test_dependency_cycle_is_rejected():
    def mutation(d):
        d["packages"] = [
            {"name": "a", "path": "a", "depends_on": ["b"]},
            {"name": "b", "path": "b", "depends_on": ["a"]},
        ]

    with pytest.raises(cfgmod.ConfigError, match="cycle"):
        cfgmod.parse(_mutate(mutation))


def test_missing_file_and_bad_toml(tmp_path):
    with pytest.raises(cfgmod.ConfigError, match="not found"):
        cfgmod.load(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError, match="invalid TOML"):
        cfgmod.load(bad)


def test_runtime_defaults_and_overrides():
    assert cfgmod.parse(_mutate(lambda d: d.pop("runtime", None))).runtime.runs_on == "ubuntu-24.04"
    rt = cfgmod.parse(_mutate(lambda d: d["runtime"].update(node_version="22", runs_on="ci-x64")))
    assert (rt.runtime.node_version, rt.runtime.runs_on) == ("22", "ci-x64")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("runs_on", "ubuntu 24.04; rm -rf /"),
        ("python_version", "3.12 && echo hi"),
        ("node_version", "$(id)"),
    ],
)
def test_runtime_rejects_shell_metacharacters(key, value):
    with pytest.raises(cfgmod.ConfigError, match="runtime"):
        cfgmod.parse(_mutate(lambda d: d["runtime"].update({key: value})))


def test_known_extras_match_what_the_composite_action_implements():
    action = (
        Path(__file__).parent.parent
        / "src"
        / "picard"
        / "templates"
        / "github"
        / "actions"
        / "pipeline-extra"
        / "action.yml"
    )
    implemented = set(
        re.findall(r"inputs\.name == '([a-z-]+)'", action.read_text(encoding="utf-8"))
    )
    assert implemented == set(cfgmod.KNOWN_EXTRAS)
