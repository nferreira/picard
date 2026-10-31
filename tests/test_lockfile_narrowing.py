"""Lockfile narrowing: a lockfile listed under [lockfiles] narrow affects only the packages its
change reaches, and every package whenever that cannot be worked out (it widens, never narrows)."""

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from picard import config as cfgmod
from picard import graph
from picard.cli import main
from picard.graph import Revisions
from picard.graph.impact import NativeImpactEngine

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).parent.parent / "src" / "picard" / "templates" / "pipeline.toml"

PACKAGES = """\
[lockfiles]
narrow = ["uv.lock", "**/package-lock.json", "Cargo.lock"]

# core, api (depends on core) and web are uv workspace members, read from uv.lock.
[[discover]]
provider = "uv"
[discover.commands]
test = "pytest {path}"

# Merged over the discovered package: it keeps provider "uv".
[[packages]]
name = "api"
baseline = true
"""


def git(repo, *args):
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@e.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def uv_lock(
    *,
    httpx="0.27.0",
    idna="3.7",
    rich="13.7.0",
    ruff="0.5.0",
    stray="1.0",
    wheel="aaa",
    tools=False,
) -> str:
    """A uv workspace: core uses httpx (which uses idna), api uses core, web uses rich, the
    workspace root has ruff as a dev group, and `stray` is locked but used by nobody."""
    members = '"core", "api", "web"' + (', "tools"' if tools else "")
    text = f"""\
version = 1
requires-python = ">=3.11"

[manifest]
members = [{members}]

[manifest.dependency-groups]
dev = [{{ name = "ruff" }}]

[[package]]
name = "core"
version = "0.1.0"
source = {{ editable = "packages/core" }}
dependencies = [{{ name = "httpx" }}]

[[package]]
name = "api"
version = "0.1.0"
source = {{ editable = "apps/api" }}
dependencies = [{{ name = "core" }}]

[[package]]
name = "web"
version = "0.1.0"
source = {{ editable = "apps/web" }}
dependencies = [{{ name = "rich" }}]

[[package]]
name = "httpx"
version = "{httpx}"
source = {{ registry = "https://pypi.org/simple" }}
dependencies = [{{ name = "idna" }}]
wheels = [{{ url = "https://example.invalid/httpx.whl", hash = "sha256:{wheel}" }}]

[[package]]
name = "idna"
version = "{idna}"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "rich"
version = "{rich}"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "ruff"
version = "{ruff}"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "stray"
version = "{stray}"
source = {{ registry = "https://pypi.org/simple" }}
"""
    if tools:
        text += """
[[package]]
name = "tools"
version = "0.1.0"
source = { editable = "tools" }
dependencies = [{ name = "rich" }]
"""
    return text


def npm_lock(left_pad="1.3.0") -> str:
    return json.dumps(
        {
            "name": "web",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "web", "version": "1.0.0", "dependencies": {"left-pad": "^1"}},
                "node_modules/left-pad": {"version": left_pad},
            },
        },
        indent=2,
    )


def config_text() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index('[[packages]]\nname = "app"')
    end = text.index("# A monorepo, packages listed by hand")
    return text[:start] + PACKAGES + "\n" + text[end:]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo on `main` with the uv and npm lockfiles committed, checked out on `feature`."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    git(work, "init", "-b", "main")
    write(work, ".picard/pipeline.toml", config_text())
    write(work, "uv.lock", uv_lock())
    write(work, "apps/web/package-lock.json", npm_lock())
    commit_all(work, "chore: base (A-1)")
    git(work, "checkout", "-b", "feature")
    return work


@pytest.fixture
def cfg(repo):
    return cfgmod.load(repo / ".picard/pipeline.toml", root=repo)


def base_of(repo) -> str:
    return git(repo, "rev-parse", "main")


def bump(repo, **versions) -> Revisions:
    write(repo, "uv.lock", uv_lock(**versions))
    commit_all(repo, "chore(deps): bump (A-2)")
    return Revisions(base_of(repo), "HEAD")


def assert_widened(impact, cfg, *fragments):
    """Every package is affected, by the lockfile, and the note says why."""
    assert impact.everything
    assert set(impact.affected) == set(cfg.packages)
    assert set(impact.decided_by.values()) == {"lockfile"}
    text = " ".join(impact.notes)
    assert "cannot be attributed to packages" in text and "running everything" in text
    for fragment in fragments:
        assert fragment in text


# ── it narrows ─────────────────────────────────────────────────────────────────────────


def test_a_uv_bump_affects_only_its_users_and_their_dependents(repo, cfg):
    impact = graph.compute_impact(cfg, ["uv.lock"], bump(repo, httpx="0.28.1"))
    assert not impact.everything and impact.global_files == ()
    assert impact.changed == ("core",)
    assert impact.affected == {"core": ("core",), "api": ("api", "core")}
    assert impact.decided_by == {"core": "lockfile", "api": "lockfile"}
    assert impact.notes == (
        "uv.lock changed httpx 0.27.0 -> 0.28.1: reaches 2 of 3 packages (core, api)",
    )


def test_a_transitive_bump_reaches_the_member_through_external_packages(repo, cfg):
    impact = graph.compute_impact(cfg, ["uv.lock"], bump(repo, idna="3.8"))
    assert impact.changed == ("core",) and set(impact.affected) == {"core", "api"}


def test_plan_with_base_narrows_through_the_cli(repo, capsys):
    write(repo, "uv.lock", uv_lock(httpx="0.28.1"))
    commit_all(repo, "chore(deps): bump httpx (A-2)")
    args = ["plan", "--tier", "1", "--base", "main", "--title", "fix: bump httpx (A-2)"]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["packages"] == ["core", "api"]
    assert any("reaches 2 of 3 packages (core, api)" in w for w in plan["warnings"])


def test_local_reads_the_working_tree_lockfile(repo, cfg):
    write(repo, "uv.lock", uv_lock(httpx="0.28.1"))  # not committed
    revisions = Revisions(base_of(repo), "HEAD", ("uv.lock",))
    impact = graph.compute_impact(cfg, ["uv.lock"], revisions)
    assert impact.changed == ("core",) and not impact.everything


def test_an_npm_lockfile_maps_its_root_entry_to_the_package_at_its_directory(repo, cfg):
    write(repo, "apps/web/package-lock.json", npm_lock(left_pad="1.3.1"))
    commit_all(repo, "chore(deps): bump left-pad (A-2)")
    revisions = Revisions(base_of(repo), "HEAD")
    impact = graph.compute_impact(cfg, ["apps/web/package-lock.json"], revisions)
    assert impact.changed == ("web",) and impact.affected == {"web": ("web",)}


def cargo_lock(serde="1.0.200", path_crate="") -> str:
    text = f"""\
version = 3

[[package]]
name = "core"
version = "0.1.0"
dependencies = ["serde"]

[[package]]
name = "serde"
version = "{serde}"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
    if path_crate:
        text += f'\n[[package]]\nname = "{path_crate}"\nversion = "0.1.0"\n'
        text += 'dependencies = ["serde"]\n'
    return text


@pytest.mark.parametrize(
    ("after", "narrowed"),
    [
        (cargo_lock(serde="1.0.201"), True),
        # A path crate picard does not know: Cargo.lock calls it a member, it maps to nothing.
        (cargo_lock(serde="1.0.201", path_crate="xtask"), False),
    ],
)
def test_a_cargo_bump_maps_members_by_name(repo, cfg, after, narrowed):
    cfg = with_provider(cfg, "core", "cargo")
    write(repo, "Cargo.lock", cargo_lock(path_crate="xtask" if not narrowed else ""))
    base = commit_all(repo, "chore: add cargo (A-2)")
    write(repo, "Cargo.lock", after)
    commit_all(repo, "chore(deps): bump serde (A-3)")
    impact = graph.compute_impact(cfg, ["Cargo.lock"], Revisions(base, "HEAD"))
    if narrowed:
        assert impact.changed == ("core",) and set(impact.affected) == {"core", "api"}
    else:
        assert_widened(impact, cfg, "'xtask' is not a picard package")


def with_provider(cfg, name, provider):
    package = dataclasses.replace(cfg.packages[name], provider=provider)
    return dataclasses.replace(cfg, packages={**cfg.packages, name: package})


def test_a_merged_package_keeps_its_discovered_provider(cfg):
    assert {p.provider for p in cfg.packages.values()} == {"uv"}
    assert cfg.packages["api"].baseline


def test_a_source_change_and_a_lockfile_bump_add_up(repo, cfg):
    revisions = bump(repo, httpx="0.28.1")
    impact = graph.compute_impact(cfg, ["apps/web/index.js", "uv.lock"], revisions)
    assert impact.changed == ("core", "web") and set(impact.affected) == {"core", "api", "web"}
    assert impact.decided_by == {"core": "lockfile", "api": "lockfile", "web": "native"}


# ── it widens on every doubt ───────────────────────────────────────────────────────────


def test_without_revisions_it_widens(cfg):
    impact = graph.compute_impact(cfg, ["uv.lock"])
    assert_widened(impact, cfg, "lockfile narrowing needs --base or --local")
    assert impact.global_files == ("uv.lock",)
    assert graph.render_why(impact) == [
        "everything is affected: uv.lock cannot be attributed to packages"
    ]


def test_files_on_the_command_line_widen_through_the_cli(repo, capsys):
    args = ["plan", "--tier", "1", "--files", "uv.lock", "--title", "fix: x (A-2)"]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["packages"] == ["core", "api", "web"]
    assert any("needs --base or --local" in w for w in plan["warnings"])


def test_a_new_lockfile_widens(repo, cfg):
    write(repo, "apps/other/package-lock.json", npm_lock())
    commit_all(repo, "chore: add a lockfile (A-2)")
    revisions = Revisions(base_of(repo), "HEAD")
    impact = graph.compute_impact(cfg, ["apps/other/package-lock.json"], revisions)
    assert_widened(impact, cfg, "it is not in the base")


def test_a_deleted_lockfile_widens(repo, cfg):
    (repo / "uv.lock").unlink()
    commit_all(repo, "chore: drop uv (A-2)")
    impact = graph.compute_impact(cfg, ["uv.lock"], Revisions(base_of(repo), "HEAD"))
    assert_widened(impact, cfg, "it was deleted")


def test_a_lockfile_deleted_in_the_working_tree_widens(repo, cfg):
    (repo / "uv.lock").unlink()
    revisions = Revisions(base_of(repo), "HEAD", ("uv.lock",))
    assert_widened(graph.compute_impact(cfg, ["uv.lock"], revisions), cfg, "it was deleted")


def test_an_unparseable_lockfile_widens(repo, cfg):
    write(repo, "uv.lock", "this is = = not toml\n")
    commit_all(repo, "chore: break the lockfile (A-2)")
    impact = graph.compute_impact(cfg, ["uv.lock"], Revisions(base_of(repo), "HEAD"))
    assert_widened(impact, cfg, "cannot be parsed")


def test_a_base_git_cannot_read_widens(repo, cfg):
    write(repo, "uv.lock", uv_lock(httpx="0.28.1"))
    commit_all(repo, "chore(deps): bump httpx (A-2)")
    impact = graph.compute_impact(cfg, ["uv.lock"], Revisions("0" * 40, "HEAD"))
    assert_widened(impact, cfg, "is not a commit")


def test_a_non_utf8_lockfile_widens(repo, cfg):
    (repo / "uv.lock").write_bytes(b"\xff\xfe not utf-8")
    commit_all(repo, "chore: binary lockfile (A-2)")
    impact = graph.compute_impact(cfg, ["uv.lock"], Revisions(base_of(repo), "HEAD"))
    assert_widened(impact, cfg, "is not UTF-8 text")


def test_an_unreadable_working_tree_lockfile_widens(repo, cfg):
    (repo / "uv.lock").write_bytes(b"\xff\xfe not utf-8")
    revisions = Revisions(base_of(repo), "HEAD", ("uv.lock",))
    impact = graph.compute_impact(cfg, ["uv.lock"], revisions)
    assert_widened(impact, cfg, "the working-tree file cannot be read")


def test_a_member_that_is_not_a_picard_package_widens(repo, cfg):
    impact = graph.compute_impact(cfg, ["uv.lock"], bump(repo, tools=True))
    assert_widened(impact, cfg, "'tools' is not a picard package")


@pytest.mark.parametrize("provider", ["npm", "manual"])
def test_a_member_name_from_another_provider_is_not_that_member(repo, cfg, provider):
    # uv member web reaches the bump, but picard's web came from npm or was listed by hand.
    cfg = with_provider(cfg, "web", provider)
    impact = graph.compute_impact(cfg, ["uv.lock"], bump(repo, rich="13.8.0"))
    assert_widened(impact, cfg, "'web' is not a picard package")


def test_a_uv_discovered_member_narrows(repo, cfg):
    impact = graph.compute_impact(cfg, ["uv.lock"], bump(repo, rich="13.8.0"))
    assert not impact.everything and impact.affected == {"web": ("web",)}


def test_a_root_dev_dependency_bump_widens(repo, cfg):
    impact = graph.compute_impact(cfg, ["uv.lock"], bump(repo, ruff="0.6.0"))
    assert_widened(impact, cfg, "the workspace root is not a picard package")


def test_an_orphan_change_widens(repo, cfg):
    impact = graph.compute_impact(cfg, ["uv.lock"], bump(repo, stray="2.0"))
    assert_widened(impact, cfg, "stray reaches no workspace member")


def test_a_text_only_change_widens(repo, cfg):
    impact = graph.compute_impact(cfg, ["uv.lock"], bump(repo, wheel="bbb"))
    assert_widened(impact, cfg, "no resolved version, source or dependency changed")


def test_an_unsupported_lockfile_widens(repo, cfg):
    # Config rejects it under [lockfiles] narrow; the engine still never narrows for it.
    engine = NativeImpactEngine({}, cfg.registries)
    engine.revisions = Revisions(base_of(repo), "HEAD")
    impact = engine.lockfile_impact(cfg, ["pnpm-lock.yaml"])
    assert_widened(impact, cfg, "picard cannot parse 'pnpm-lock.yaml'")


def test_one_lockfile_that_widens_widens_the_whole_answer(repo, cfg):
    write(repo, "apps/web/package-lock.json", "{ not json")
    revisions = bump(repo, httpx="0.28.1")
    impact = graph.compute_impact(cfg, ["uv.lock", "apps/web/package-lock.json"], revisions)
    assert impact.everything and set(impact.affected) == set(cfg.packages)
    assert impact.global_files == ("apps/web/package-lock.json",)
    assert len(impact.notes) == 2
    assert impact.notes[0].startswith("uv.lock changed httpx 0.27.0 -> 0.28.1")
    assert "apps/web/package-lock.json changed and cannot be attributed" in impact.notes[1]


def test_many_changes_are_summarised_in_the_note(repo, cfg):
    write(repo, "uv.lock", uv_lock(httpx="0.28.1", idna="3.8"))
    commit_all(repo, "chore(deps): bump two (A-2)")
    impact = graph.compute_impact(cfg, ["uv.lock"], Revisions(base_of(repo), "HEAD"))
    assert impact.notes == (
        "uv.lock changed httpx 0.27.0 -> 0.28.1, idna 3.7 -> 3.8: reaches 2 of 3 packages "
        "(core, api)",
    )
