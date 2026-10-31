"""Lockfile diffs: small synthetic uv.lock, Cargo.lock and package-lock.json texts, no tools."""

import json
import textwrap

import pytest

from picard.lockfiles import SUPPORTED, UV_ROOT, LockChange, LockfileError, diff

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

REGISTRY = '{ registry = "https://registry.example/simple" }'


def uv_package(name, version="1.0.0", deps=(), source=REGISTRY, extra=""):
    lines = ["[[package]]", f'name = "{name}"']
    if version is not None:
        lines.append(f'version = "{version}"')
    lines.append(f"source = {source}")
    if deps:
        lines.append("dependencies = [" + ", ".join(f'{{ name = "{d}" }}' for d in deps) + "]")
    lines.append(
        f'sdist = {{ url = "https://files.example/{name}-{version}.tar.gz", hash = "sha256:00" }}'
    )
    return "\n".join(lines) + ("\n" + textwrap.dedent(extra) if extra else "") + "\n"


def uv_lock(*packages, manifest=""):
    head = 'version = 1\nrequires-python = ">=3.11"\n'
    if manifest:
        head += "\n" + textwrap.dedent(manifest)
    return head + "\n" + "\n".join(packages)


def workspace(
    ext_a="1.0.0",
    lib_deps=("ext-a",),
    app_deps=("lib", "ext-b"),
    ext_b_deps=("ext-c",),
    ext_c="2.0.0",
    more=(),
):
    """A uv workspace: app -> lib (member) -> ext-a; app -> ext-b -> ext-c; tool is unused."""
    return uv_lock(
        uv_package("app", "0.1.0", app_deps, '{ editable = "apps/app" }'),
        uv_package("lib", "0.1.0", lib_deps, '{ editable = "libs/lib" }'),
        uv_package("ext-a", ext_a),
        uv_package("ext-b", "1.0.0", ext_b_deps),
        uv_package("ext-c", ext_c),
        *more,
        manifest='[manifest]\nmembers = ["app", "lib"]\n',
    )


class TestUv:
    def test_direct_bump_reaches_its_user_not_through_another_member(self):
        change = diff("uv.lock", workspace(), workspace(ext_a="1.1.0"))
        assert change == LockChange(
            kind="uv",
            changed={"ext-a": ("1.0.0", "1.1.0")},
            members={"lib": ("ext-a",)},  # app uses lib, a member: picard's graph adds app
            orphans=(),
            text_only=False,
        )

    def test_bump_reaches_through_an_external_package(self):
        change = diff("uv.lock", workspace(), workspace(ext_c="2.1.0"))
        assert change.changed == {"ext-c": ("2.0.0", "2.1.0")}
        assert change.members == {"app": ("ext-c",)}

    def test_member_own_entry_change_lists_the_member(self):
        change = diff("uv.lock", workspace(), workspace(app_deps=("lib",)))
        assert change.members == {"app": ("app",)}
        assert change.changed == {}
        assert not change.text_only

    def test_added_and_removed_packages(self):
        old = workspace()
        new = workspace(lib_deps=("ext-new",), more=())
        new = new.replace('name = "ext-a"', 'name = "ext-new"')
        change = diff("uv.lock", old, new)
        assert change.changed == {"ext-a": ("1.0.0", ""), "ext-new": ("", "1.0.0")}
        # The removed edge still counts: union of old and new graphs.
        assert change.members == {"lib": ("ext-a", "ext-new", "lib")}

    def test_union_of_edges_counts_a_dependency_removed_in_the_same_change(self):
        old = workspace()
        new = workspace(ext_a="1.1.0", lib_deps=())
        change = diff("uv.lock", old, new)
        assert change.members["lib"] == ("ext-a", "lib")
        assert change.orphans == ()

    def test_several_versions_of_one_name_are_joined(self):
        old = workspace(more=(uv_package("ext-a", "0.9.0"),))
        new = workspace(ext_a="1.2.0", more=(uv_package("ext-a", "0.9.0"),))
        assert diff("uv.lock", old, new).changed == {"ext-a": ("0.9.0,1.0.0", "0.9.0,1.2.0")}

    def test_source_only_change_with_the_same_version(self):
        git = '{ git = "https://git.example/ext-a?rev=bbb#bbb" }'
        old = workspace().replace(
            f'name = "ext-a"\nversion = "1.0.0"\nsource = {REGISTRY}',
            f'name = "ext-a"\nversion = "1.0.0"\nsource = {git.replace("bbb", "aaa")}',
        )
        new = workspace().replace(
            f'name = "ext-a"\nversion = "1.0.0"\nsource = {REGISTRY}',
            f'name = "ext-a"\nversion = "1.0.0"\nsource = {git}',
        )
        change = diff("uv.lock", old, new)
        assert change.changed == {"ext-a": ("1.0.0", "1.0.0")}
        assert change.members == {"lib": ("ext-a",)}

    def test_orphan_change_reaches_no_member(self):
        old = workspace(more=(uv_package("tool", "1.0.0"),))
        new = workspace(more=(uv_package("tool", "1.1.0"),))
        change = diff("uv.lock", old, new)
        assert change.orphans == ("tool",)
        assert change.members == {}

    def test_hash_only_edit_is_text_only(self):
        old = workspace()
        new = old.replace("sha256:00", "sha256:ff")
        change = diff("uv.lock", old, new)
        assert change.text_only
        assert change.changed == {} and change.members == {} and change.orphans == ()

    def test_identical_text_is_text_only_so_the_caller_widens(self):
        assert diff("uv.lock", workspace(), workspace()).text_only

    def root_project(self, ruff="0.8.0", httpx="0.27.0"):
        return uv_lock(
            uv_package("demo", "0.1.0", ("svc",), '{ virtual = "." }'),
            uv_package("svc", "0.1.0", ("httpx",), '{ editable = "svc" }'),
            uv_package("httpx", httpx),
            uv_package("ruff", ruff),
            manifest="""\
            [manifest]
            members = ["demo", "svc"]

            [manifest.dependency-groups]
            dev = [{ name = "ruff" }]
            """,
        )

    def test_root_project_and_manifest_dependency_groups_attribute_to_the_root(self):
        change = diff("uv.lock", self.root_project(), self.root_project(ruff="0.9.0"))
        assert change.members == {"demo": ("ruff",)}
        assert change.orphans == ()

    def test_root_project_is_a_member_and_not_walked_into(self):
        change = diff("uv.lock", self.root_project(), self.root_project(httpx="0.28.1"))
        assert change.changed == {"httpx": ("0.27.0", "0.28.1")}
        assert change.members == {"svc": ("httpx",)}

    def test_manifest_requirements_without_a_root_project_use_the_root_key(self):
        def lock(version):
            return uv_lock(
                uv_package("svc", "0.1.0", (), '{ editable = "svc" }'),
                uv_package("black", version),
                manifest="""\
                [manifest]
                members = ["svc"]
                requirements = [{ name = "black" }]
                """,
            )

        change = diff("uv.lock", lock("24.1.0"), lock("24.2.0"))
        assert change.members == {UV_ROOT: ("black",)}

    def test_optional_and_dev_dependencies_are_edges(self):
        extra = """\
        [package.optional-dependencies]
        fast = [{ name = "ext-a" }]

        [package.dev-dependencies]
        dev = [{ name = "ext-c" }]
        """

        def lock(a, c):
            return uv_lock(
                uv_package("svc", "0.1.0", (), '{ editable = "svc" }', extra),
                uv_package("ext-a", a),
                uv_package("ext-c", c),
                manifest='[manifest]\nmembers = ["svc"]\n',
            )

        change = diff("uv.lock", lock("1", "1"), lock("2", "2"))
        assert change.members == {"svc": ("ext-a", "ext-c")}

    def test_local_package_without_a_version(self):
        old = uv_lock(
            uv_package("svc", None, ("ext-a",), '{ editable = "." }'), uv_package("ext-a", "1")
        )
        new = uv_lock(
            uv_package("svc", None, ("ext-a",), '{ editable = "." }'), uv_package("ext-a", "2")
        )
        assert diff("uv.lock", old, new).members == {"svc": ("ext-a",)}


def cargo_lock(*packages):
    return "version = 3\n\n" + "\n".join(packages)


def crate(name, version, deps=(), registry=True):
    lines = ["[[package]]", f'name = "{name}"', f'version = "{version}"']
    if registry:
        lines.append('source = "registry+https://index.example/crates"')
        lines.append(f'checksum = "{name}00"')
    if deps:
        lines.append("dependencies = [" + ", ".join(f'"{d}"' for d in deps) + "]")
    return "\n".join(lines) + "\n"


class TestCargo:
    def lock(self, serde="1.0.0", core_deps=("serde",), api_deps=("core", "tokio 1.0.0")):
        return cargo_lock(
            crate("api", "0.1.0", api_deps, registry=False),
            crate("core", "0.1.0", core_deps, registry=False),
            crate("serde", serde),
            crate("tokio", "1.0.0", ("bytes",)),
            crate("tokio", "0.2.0"),
            crate("bytes", "1.0.0"),
        )

    def test_bump_reaches_its_user_not_through_another_member(self):
        change = diff("Cargo.lock", self.lock(), self.lock(serde="1.0.1"))
        assert change.changed == {"serde": ("1.0.0", "1.0.1")}
        assert change.members == {"core": ("serde",)}

    def test_through_an_external_and_versioned_dependency_strings(self):
        old = self.lock()
        new = old.replace('name = "bytes"\nversion = "1.0.0"', 'name = "bytes"\nversion = "1.1.0"')
        change = diff("Cargo.lock", old, new)
        assert change.changed == {"bytes": ("1.0.0", "1.1.0")}
        assert change.members == {"api": ("bytes",)}

    def test_several_versions_joined(self):
        old = self.lock()
        new = old.replace('version = "0.2.0"', 'version = "0.3.0"')
        assert diff("Cargo.lock", old, new).changed == {"tokio": ("0.2.0,1.0.0", "0.3.0,1.0.0")}

    def test_member_own_entry_change(self):
        change = diff("Cargo.lock", self.lock(), self.lock(api_deps=("core",)))
        assert change.members == {"api": ("api",)}

    def test_checksum_only_edit_is_text_only(self):
        old = self.lock()
        assert diff("Cargo.lock", old, old.replace("serde00", "serdeff")).text_only

    def test_orphan(self):
        old = self.lock() + "\n" + crate("unused", "1.0.0")
        new = self.lock() + "\n" + crate("unused", "2.0.0")
        assert diff("Cargo.lock", old, new).orphans == ("unused",)


def npm_lock(packages, version=3):
    return json.dumps(
        {"name": "root", "lockfileVersion": version, "requires": True, "packages": packages},
        indent=2,
    )


def npm_workspace(lodash="4.17.20", nested="1.0.0", ui_deps=None, integrity="sha512-aa"):
    return npm_lock(
        {
            "": {"name": "root", "workspaces": ["packages/*"], "devDependencies": {"eslint": "^9"}},
            "packages/ui": {
                "name": "ui",
                "version": "1.0.0",
                "dependencies": ui_deps or {"lodash": "^4"},
            },
            "packages/web": {
                "name": "web",
                "version": "1.0.0",
                "dependencies": {"ui": "*", "left": "^1"},
            },
            "node_modules/ui": {"resolved": "packages/ui", "link": True},
            "node_modules/web": {"resolved": "packages/web", "link": True},
            "node_modules/lodash": {
                "version": lodash,
                "resolved": f"https://registry.example/lodash-{lodash}.tgz",
                "integrity": integrity,
            },
            "node_modules/eslint": {"version": "9.0.0", "dev": True},
            "node_modules/left": {"version": "1.0.0", "dependencies": {"pad": "^1"}},
            "node_modules/left/node_modules/pad": {"version": nested},
            "node_modules/@scope/unused": {"version": "1.0.0"},
        }
    )


class TestNpm:
    def test_bump_reaches_its_user_not_through_the_member_link(self):
        change = diff("package-lock.json", npm_workspace(), npm_workspace(lodash="4.17.21"))
        assert change.changed == {"lodash": ("4.17.20", "4.17.21")}
        assert change.members == {"packages/ui": ("lodash",)}  # web reaches ui only via its link

    def test_nested_node_modules_package(self):
        change = diff("package-lock.json", npm_workspace(), npm_workspace(nested="1.1.0"))
        assert change.changed == {"pad": ("1.0.0", "1.1.0")}
        assert change.members == {"packages/web": ("pad",)}

    def test_member_own_entry_change_and_added_package(self):
        old = npm_workspace()
        data = json.loads(old)
        data["packages"]["packages/ui"]["dependencies"]["chalk"] = "^5"
        data["packages"]["node_modules/chalk"] = {"version": "5.0.0"}
        change = diff("package-lock.json", old, json.dumps(data))
        assert change.changed == {"chalk": ("", "5.0.0")}
        assert change.members == {"packages/ui": ("chalk", "packages/ui")}

    def test_root_member_key_is_empty(self):
        old = npm_workspace()
        new = old.replace('"version": "9.0.0"', '"version": "9.1.0"')
        assert diff("package-lock.json", old, new).members == {"": ("eslint",)}

    def test_scoped_orphan(self):
        old = npm_workspace()
        new = old.replace(
            '"node_modules/@scope/unused": {\n      "version": "1.0.0"',
            '"node_modules/@scope/unused": {\n      "version": "2.0.0"',
        )
        change = diff("package-lock.json", old, new)
        assert change.orphans == ("@scope/unused",)

    def test_integrity_only_edit_is_text_only(self):
        assert diff(
            "package-lock.json", npm_workspace(), npm_workspace(integrity="sha512-bb")
        ).text_only

    def test_lockfile_in_a_subdirectory(self):
        change = diff(
            "apps/site/package-lock.json", npm_workspace(), npm_workspace(lodash="4.17.21")
        )
        assert change.kind == "npm"
        assert change.members == {"packages/ui": ("lodash",)}

    def test_removed_dependency_still_counts(self):
        old = npm_workspace()
        new = npm_workspace(lodash="4.17.21", ui_deps={"left": "^1"})
        # ui dropped lodash in the same change that bumped it: the old edge still reaches it.
        change = diff("package-lock.json", old, new)
        assert change.members == {"packages/ui": ("lodash", "packages/ui")}
        assert change.orphans == ()


class TestFailsClosed:
    """Anything picard cannot read raises: the caller then runs everything."""

    def test_supported_names(self):
        assert SUPPORTED == {"uv.lock": "uv", "Cargo.lock": "cargo", "package-lock.json": "npm"}

    @pytest.mark.parametrize(
        "path", ["pnpm-lock.yaml", "yarn.lock", "web/pnpm-lock.yaml", "uv.lock.bak"]
    )
    def test_unsupported_basename(self, path):
        with pytest.raises(LockfileError, match="cannot parse"):
            diff(path, "", "")

    def test_npm_v1_raises(self):
        v1 = json.dumps({"lockfileVersion": 1, "dependencies": {"lodash": {"version": "4.0.0"}}})
        with pytest.raises(LockfileError, match="lockfileVersion 1"):
            diff("package-lock.json", v1, npm_workspace())

    @pytest.mark.parametrize(
        "path, text",
        [
            ("package-lock.json", "{not json"),
            ("package-lock.json", "[]"),
            ("package-lock.json", '{"lockfileVersion": true, "packages": {}}'),
            ("package-lock.json", '{"lockfileVersion": "3", "packages": {}}'),
            ("package-lock.json", '{"lockfileVersion": 3}'),
            ("package-lock.json", '{"lockfileVersion": 3, "packages": []}'),
            ("package-lock.json", '{"lockfileVersion": 3, "packages": {"node_modules/a": "x"}}'),
            (
                "package-lock.json",
                '{"lockfileVersion": 3, "packages": {"node_modules/a": {"dependencies": ["b"]}}}',
            ),
            ("package-lock.json", '{"lockfileVersion": 3, "packages": {"node_modules/": {}}}'),
            ("uv.lock", "this is = = not toml"),
            ("uv.lock", "version = 1\n"),
            ("uv.lock", 'package = "x"\n'),
            ("uv.lock", '[[package]]\nversion = "1"\n'),
            ("uv.lock", '[[package]]\nname = "a"\ndependencies = "b"\n'),
            ("uv.lock", '[[package]]\nname = "a"\ndependencies = ["b"]\n'),
            ("uv.lock", '[[package]]\nname = "a"\nsource = "x"\n'),
            ("uv.lock", '[manifest]\nmembers = "a"\n[[package]]\nname = "a"\n'),
            ("Cargo.lock", "[[package]\n"),
            ("Cargo.lock", "version = 3\n"),
            ("Cargo.lock", '[[package]]\nname = "a"\ndependencies = "b"\n'),
            ("Cargo.lock", '[[package]]\nname = "a"\ndependencies = [{ name = "b" }]\n'),
            ("Cargo.lock", '[[package]]\nname = "a"\ndependencies = [""]\n'),
            ("Cargo.lock", "[[package]]\nname = 1\n"),
        ],
    )
    def test_garbage_and_unexpected_shapes_raise(self, path, text):
        with pytest.raises(LockfileError, match=r"\((base|head)\)"):
            diff(path, text, text)

    def test_error_names_the_path_and_side(self):
        with pytest.raises(LockfileError, match=r"^svc/uv\.lock \(head\)"):
            diff("svc/uv.lock", workspace(), "garbage = = =")

    def test_lockfile_error_is_a_value_error(self):
        assert issubclass(LockfileError, ValueError)
