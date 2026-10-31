"""Lockfile diffs: which external packages changed, and which workspace members they reach.

A lockfile change runs everything unless picard can say who it reaches. This module is the pure
half of that: given the old and the new text of one lockfile it names the external packages whose
resolved versions, sources or dependencies differ, and for each workspace member the changed names
it reaches through external packages. It never walks into another member, because picard's own
graph adds a member's dependents. Git and config are the caller's business.

Everything here widens on doubt. Edges are the union of the old and the new graph, matched by
name, so a dependency removed in the same change still counts. Anything unparseable or of an
unexpected shape raises `LockfileError`, and the caller runs everything. A change that reaches no
member lands in `orphans`, and a change nothing can be attributed to sets `text_only`; the caller
treats both as "run everything".

Formats read: `uv.lock` and `Cargo.lock` (TOML), `package-lock.json` v2 and v3 (JSON). Cargo.lock
does not list workspace members, so every package without a `source` (a path crate) is a member;
a path crate that is not a picard package maps to nothing and the caller widens.
"""

from __future__ import annotations

import json
import tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath


class LockfileError(ValueError):
    """A lockfile cannot be read, or a change in it cannot be attributed."""


SUPPORTED = {"uv.lock": "uv", "Cargo.lock": "cargo", "package-lock.json": "npm"}

UV_ROOT = "<root>"  # the uv root member key when the workspace has no root project

# Fields that only verify content picard already identifies by version and source.
_HASH_FIELDS = {"uv": ("sdist", "wheels"), "cargo": ("checksum",), "npm": ("integrity",)}


@dataclass(frozen=True)
class LockChange:
    kind: str  # "uv" | "cargo" | "npm"
    # external name -> (old versions, new versions); "" = absent
    changed: dict[str, tuple[str, str]]
    members: dict[str, tuple[str, ...]]  # member key -> changed names that reach it, sorted
    orphans: tuple[str, ...] = ()  # changed names that reach no member
    text_only: bool = False  # nothing in the change can be attributed to a member


@dataclass
class _Node:
    """One package in a lockfile: a member, a link to a member (npm), or an external package."""

    name: str
    member: bool = False
    stop: bool = False  # the walk does not enter it: a member, or an npm link to one
    versions: set[str] = field(default_factory=set)
    identity: set[str] = field(default_factory=set)  # version, source and dependencies, as JSON
    signature: list[str] = field(default_factory=list)  # each entry minus hash fields, as JSON
    depends_on: set[str] = field(default_factory=set)  # names


@dataclass
class _Side:
    nodes: dict[str, _Node] = field(default_factory=dict)  # key -> node

    def node(self, key: str, name: str) -> _Node:
        if key not in self.nodes:
            self.nodes[key] = _Node(name)
        return self.nodes[key]


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _strip(entry: dict, kind: str) -> dict:
    return {k: v for k, v in entry.items() if k not in _HASH_FIELDS[kind]}


def _table(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise LockfileError(f"{what} is not a table")
    return value


def _array(value: object, what: str) -> list:
    if not isinstance(value, list):
        raise LockfileError(f"{what} is not a list")
    return value


def _string(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise LockfileError(f"{what} is not a string")
    return value


def _uv_names(requirements: object, what: str) -> set[str]:
    names = set()
    for item in _array(requirements, what):
        names.add(_string(_table(item, what).get("name"), f"{what} name"))
    return names


def _uv_dependencies(package: dict, what: str) -> set[str]:
    names = _uv_names(package.get("dependencies", []), f"{what} dependencies")
    for key in ("optional-dependencies", "dev-dependencies"):
        for group, items in _table(package.get(key, {}), f"{what} {key}").items():
            names |= _uv_names(items, f"{what} {key}.{group}")
    return names


def _parse_uv(text: str) -> _Side:
    data = tomllib.loads(text)
    packages = _array(data.get("package"), "[[package]]")
    manifest = _table(data.get("manifest", {}), "[manifest]")
    declared = {
        _string(m, "[manifest] members entry")
        for m in _array(manifest.get("members", []), "[manifest] members")
    }
    side = _Side()
    root = None
    for package in packages:
        package = _table(package, "[[package]] entry")
        name = _string(package.get("name"), "[[package]] name")
        what = f"package '{name}'"
        source = _table(package.get("source", {}), f"{what} source")
        local = source.get("editable") or source.get("virtual")
        is_root = local is not None and PurePosixPath(
            _string(local, f"{what} source")
        ) == PurePosixPath(".")
        if is_root:
            root = name
        # Like the uv provider: without [manifest] members every local package is a member.
        member = is_root or name in declared or (not declared and local is not None)
        node = side.node(name, name)
        node.member = node.stop = node.member or member
        version = package.get("version", "")
        node.versions.add(_string(version, f"{what} version"))
        deps = _uv_dependencies(package, what)
        node.depends_on |= deps
        node.identity.add(_canonical([version, source, sorted(deps)]))
        node.signature.append(_canonical(_strip(package, "uv")))
    root_deps = _uv_names(manifest.get("requirements", []), "[manifest] requirements")
    groups = _table(manifest.get("dependency-groups", {}), "[manifest] dependency-groups")
    for group, items in groups.items():
        root_deps |= _uv_names(items, f"[manifest] dependency-groups.{group}")
    if root_deps or root is not None:
        key = root if root is not None else UV_ROOT
        node = side.node(key, key)
        node.member = node.stop = True
        node.depends_on |= root_deps
        node.signature.append(
            _canonical(
                {
                    "manifest": {
                        "requirements": manifest.get("requirements", []),
                        "dependency-groups": groups,
                    }
                }
            )
        )
    for name in declared:
        node = side.node(name, name)  # a declared member with no entry is still a member
        node.member = node.stop = True
    return side


def _parse_cargo(text: str) -> _Side:
    data = tomllib.loads(text)
    side = _Side()
    for package in _array(data.get("package"), "[[package]]"):
        package = _table(package, "[[package]] entry")
        name = _string(package.get("name"), "[[package]] name")
        what = f"package '{name}'"
        version = _string(package.get("version", ""), f"{what} version")
        source = package.get("source")
        if source is not None:
            _string(source, f"{what} source")
        deps = set()
        for spec in _array(package.get("dependencies", []), f"{what} dependencies"):
            words = _string(spec, f"{what} dependencies entry").split()
            if not words:
                raise LockfileError(f"{what} has an empty dependencies entry")
            deps.add(words[0])
        node = side.node(name, name)
        node.member = node.stop = node.member or source is None
        node.versions.add(version)
        node.depends_on |= deps
        node.identity.add(_canonical([version, source, sorted(deps)]))
        node.signature.append(_canonical(_strip(package, "cargo")))
    return side


_NPM_DEPENDENCY_FIELDS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


def _npm_name(key: str) -> str:
    """The package name of a `node_modules/...` key: what follows its last `node_modules/`."""
    parts = key.split("/")
    last = max(i for i, part in enumerate(parts) if part == "node_modules")
    name = "/".join(parts[last + 1 :])
    if not name:
        raise LockfileError(f"packages entry '{key}' names no package")
    return name


def _parse_npm(text: str) -> _Side:
    data = _table(json.loads(text), "the lockfile")
    version = data.get("lockfileVersion")
    if isinstance(version, bool) or not isinstance(version, int):
        raise LockfileError("lockfileVersion is missing or not an integer")
    if version < 2:
        raise LockfileError(
            f"lockfileVersion {version} has no 'packages' section: 2 or later needed"
        )
    side = _Side()
    for key, entry in _table(data.get("packages"), "packages").items():
        what = f"packages entry '{key}'"
        entry = _table(entry, what)
        member = "node_modules" not in key.split("/")
        name = key if member else _npm_name(key)
        deps = set()
        for dep_field in _NPM_DEPENDENCY_FIELDS:
            deps |= set(_table(entry.get(dep_field, {}), f"{what} {dep_field}"))
        node = side.node(key, name)
        node.member = member
        # A `link` entry is the symlink npm makes to a workspace member: the member, not a package.
        node.stop = member or entry.get("link") is True
        node.versions.add(_string(entry.get("version", ""), f"{what} version"))
        node.depends_on |= deps
        node.identity.add(
            _canonical([entry.get("version", ""), entry.get("resolved"), sorted(deps)])
        )
        node.signature.append(_canonical(_strip(entry, "npm")))
    return side


_PARSERS = {"uv": _parse_uv, "cargo": _parse_cargo, "npm": _parse_npm}


def _externals(side: _Side) -> dict[str, tuple[set[str], set[str]]]:
    """External name -> (versions, identities), over every entry of that name."""
    found: dict[str, tuple[set[str], set[str]]] = {}
    for node in side.nodes.values():
        if not node.stop:
            versions, identity = found.setdefault(node.name, (set(), set()))
            versions |= node.versions
            identity |= node.identity
    return found


def _compare(kind: str, old: _Side, new: _Side) -> LockChange:
    old_ext, new_ext = _externals(old), _externals(new)
    changed: dict[str, tuple[str, str]] = {}
    for name in sorted(old_ext.keys() | new_ext.keys()):
        before, after = old_ext.get(name), new_ext.get(name)
        if before is None or after is None or before[1] != after[1]:
            changed[name] = (
                ",".join(sorted(before[0])) if before else "",
                ",".join(sorted(after[0])) if after else "",
            )

    # The union graph, by key: a node that stops the walk on either side stops it.
    keys = old.nodes.keys() | new.nodes.keys()
    stop = {k for k in keys if any(s.nodes[k].stop for s in (old, new) if k in s.nodes)}
    member_keys = {k for k in keys if any(s.nodes[k].member for s in (old, new) if k in s.nodes)}
    by_name: dict[str, set[str]] = {}
    depends_on: dict[str, set[str]] = {}
    for side in (old, new):
        for key, node in side.nodes.items():
            by_name.setdefault(node.name, set()).add(key)
            depends_on.setdefault(key, set()).update(node.depends_on)

    members: dict[str, tuple[str, ...]] = {}
    reached_any: set[str] = set()
    for member in sorted(member_keys):
        hits: set[str] = set()
        seen = {member}
        queue = deque([member])
        while queue:
            for name in depends_on.get(queue.popleft(), ()):
                for key in by_name.get(name, ()):
                    if key in seen or key in stop:
                        continue
                    seen.add(key)
                    queue.append(key)
                    node_name = (new.nodes.get(key) or old.nodes[key]).name
                    if node_name in changed:
                        hits.add(node_name)
        reached_any |= hits
        before, after = old.nodes.get(member), new.nodes.get(member)
        if before is None or after is None or sorted(before.signature) != sorted(after.signature):
            hits.add(member)
        if hits:
            members[member] = tuple(sorted(hits))

    orphans = tuple(name for name in changed if name not in reached_any)
    return LockChange(
        kind=kind,
        changed=changed,
        members=members,
        orphans=orphans,
        text_only=not changed and not members,
    )


def diff(path: str, old: str, new: str) -> LockChange:
    """What changed between two texts of the lockfile at `path`. Raises `LockfileError`."""
    basename = PurePosixPath(path).name
    kind = SUPPORTED.get(basename)
    if kind is None:
        raise LockfileError(f"{path}: picard cannot parse '{basename}'")
    parse = _PARSERS[kind]
    sides = []
    for label, text in (("base", old), ("head", new)):
        try:
            sides.append(parse(text))
        except LockfileError as exc:
            raise LockfileError(f"{path} ({label}): {exc}") from exc
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise LockfileError(f"{path} ({label}) cannot be parsed: {exc}") from exc
    return _compare(kind, *sides)
