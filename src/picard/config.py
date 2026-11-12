"""pipeline.toml loading and validation.

Everything the planner decides is data in pipeline.toml, so a repo tunes its pipeline by
editing config, not workflows. Validation is strict: a typo in a profile name or an
unknown extra fails here, not silently in CI.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from picard import graph
from picard.bootstrap import default_registries
from picard.globs import captures as graph_captures
from picard.registry import Registries, UnknownPluginError

RELEASE_ORDER = ("none", "patch", "minor", "major")
COMPARE_MODES = ("merge-base", "pr-base", "none")
BUILTIN_ACTIONS = ("none", "full", "package", "own", "require-review")
UNMAPPED_POLICIES = ("ignore", "warn", "full")
PLACEHOLDERS = ("package", "path", "markers")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._-]+$")  # captured names and ids end up in commands
PACKAGE_SELECTORS = ("affected", "baseline", "all")
UNKNOWN_BASE_MODES = ("full", "fail")
CYCLE_MODES = ("error", "allow")  # [graph] cycles: refuse a dependency cycle, or accept it
# Lockfiles picard can attribute to packages. Only the names live here, so loading a config
# never needs the parsers.
NARROWABLE_LOCKFILES = ("Cargo.lock", "package-lock.json", "uv.lock")
MAX_SHARDS = 256  # GitHub's matrix limit: one tier cannot run more jobs than this
MAX_LABEL = 50  # GitHub's label name limit

# Extras are named jobs the reusable workflows know how to run.
KNOWN_EXTRAS = frozenset(
    {
        "secrets",
        "workflow-lint",
        "dependency-review",
        "semgrep",
        "breaking-change-checks",
        "link-check",
    }
)


class ConfigError(ValueError):
    """pipeline.toml is invalid."""


@dataclass(frozen=True)
class Profile:
    name: str
    rank: int
    run_suites: bool
    tiers: tuple[int, ...]
    extras: dict[int, tuple[str, ...]]


@dataclass(frozen=True)
class TypeRule:
    profile: str
    release: str


@dataclass(frozen=True)
class Override:
    pattern: re.Pattern[str]
    profile: str
    release: str | None


@dataclass(frozen=True)
class Breaking:
    profile: str
    release: str
    tiers: tuple[int, ...]
    extras: tuple[str, ...]


@dataclass(frozen=True)
class PathFloor:
    globs: tuple[str, ...]
    profile: str


@dataclass(frozen=True)
class Package:
    name: str
    path: str
    depends_on: tuple[str, ...]
    baseline: bool
    commands: dict[str, str]
    provider: str = "manual"
    executor: str | None = None  # builds commands the package does not spell out
    always: bool = False  # in the affected set of every tier that compares against a diff
    shards: int = 1  # a shardable suite runs as this many matrix jobs
    test_paths: tuple[str, ...] = ()  # globs, relative to `path`, that only hold tests
    dev_depends_on: tuple[str, ...] = ()  # the part of depends_on found only in a dev group

    @property
    def runtime_depends_on(self) -> tuple[str, ...]:
        """What the package needs to run: `depends_on` without the dev-group-only edges."""
        return tuple(d for d in self.depends_on if d not in self.dev_depends_on)


@dataclass(frozen=True)
class PathRule:
    globs: tuple[str, ...]
    packages: tuple[str, ...]


@dataclass(frozen=True)
class Tier:
    number: int
    name: str
    compare: str
    packages: str
    markers: str
    run: tuple[str, ...]
    extras: tuple[str, ...]
    selection: bool = False  # executors may select the tests changed since the base commit


@dataclass(frozen=True)
class Runtime:
    """Where and how suites run in CI. Read by the workflows through the plan."""

    runs_on: str = "ubuntu-24.04"
    python_version: str = ""
    node_version: str = ""
    setup_command: str = ""


@dataclass(frozen=True)
class ImpactSettings:
    """Which impact engine answers "what does this change affect", and its options."""

    engine: str = "native"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """If a changed file matches, take these actions. Rules cover what the code graph cannot see."""

    name: str
    match: tuple[str, ...]
    actions: tuple[str, ...]
    profile: str | None
    tiers: tuple[int, ...]
    captures: tuple[str, ...]


@dataclass(frozen=True)
class ActionDef:
    name: str
    run: str
    tiers: tuple[int, ...] | None = None  # overrides the rule's tiers for this action
    needs: tuple[str, ...] | None = None  # inventory statuses it requires (default: runnable)


@dataclass(frozen=True)
class Inventory:
    """Resolves a captured directory name to an id and a status, from a checked-in file."""

    name: str
    file: str
    entries: str
    directory_field: str
    id_field: str
    status_field: str
    runnable: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    types: tuple[str, ...]
    require_ticket: bool
    ticket_exempt: tuple[re.Pattern[str], ...]
    type_rules: dict[str, TypeRule]
    overrides: tuple[Override, ...]
    breaking: Breaking
    profiles: dict[str, Profile]
    docs_globs: tuple[str, ...]
    path_floors: tuple[PathFloor, ...]
    default_floor: str
    global_globs: tuple[str, ...]
    packages: dict[str, Package]
    path_rules: tuple[PathRule, ...]
    tiers: dict[int, Tier]
    fallback_profile: str = "full"
    runtime: Runtime = Runtime()
    rules: tuple[Rule, ...] = ()
    actions: dict[str, ActionDef] = field(default_factory=dict)
    inventories: dict[str, Inventory] = field(default_factory=dict)
    unmapped: str = "ignore"
    impact: ImpactSettings = ImpactSettings()
    executor_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    git_unknown_base: str = "full"  # what --base does when the base commit cannot be found
    full_label: str = "ci:full"  # a PR label that forces a full run; "" disables it
    narrow_lockfiles: tuple[str, ...] = ()  # lockfile globs attributed to packages, not global
    cycles: str = "error"  # [graph] cycles: what a dependency cycle between packages does
    cycles_found: tuple[tuple[str, ...], ...] = ()  # the cycles accepted when cycles = "allow"
    registries: Registries = field(default_factory=Registries, compare=False)
    root: Path | None = field(default=None, compare=False)
    source: Path | None = field(default=None, compare=False)

    def rank(self, profile: str) -> int:
        return self.profiles[profile].rank

    def dependency_graph(self) -> graph.DependencyGraph:
        return graph.DependencyGraph({n: p.depends_on for n, p in self.packages.items()})


def _table(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{key}] must be a table")
    return value


def _tuple(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return tuple(value)


def _extras(value: Any, where: str) -> tuple[str, ...]:
    extras = _tuple(value, where)
    unknown = sorted(set(extras) - KNOWN_EXTRAS)
    if unknown:
        raise ConfigError(
            f"{where}: unknown extra(s) {', '.join(unknown)}; known: {', '.join(sorted(KNOWN_EXTRAS))}"
        )
    return extras


def _release(value: Any, where: str) -> str:
    if value not in RELEASE_ORDER:
        raise ConfigError(f"{where}: release must be one of {', '.join(RELEASE_ORDER)}")
    return value


def _compile(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"{where}: bad regex {pattern!r}: {exc}") from exc


def parse(raw: dict[str, Any], source: Path | None = None, root: Path | None = None) -> Config:
    plugins = _table(raw, "plugins")
    try:
        registries = default_registries(
            plugin_modules=_tuple(plugins.get("modules", []), "[plugins] modules"),
            plugin_paths=_tuple(plugins.get("paths", []), "[plugins] paths"),
            root=root,
        )
    except (RuntimeError, ValueError) as exc:  # PluginError, duplicate registration
        raise ConfigError(f"[plugins] {exc}") from exc

    commits = _table(raw, "commits")
    types = _tuple(commits.get("types", []), "[commits] types")
    if not types:
        raise ConfigError("[commits] types must list at least one type")

    profiles: dict[str, Profile] = {}
    for name, body in _table(raw, "profiles").items():
        extras_raw = body.get("extras", {})
        extras = {
            int(tier): _extras(v, f"[profiles.{name}.extras] {tier}")
            for tier, v in extras_raw.items()
        }
        profiles[name] = Profile(
            name=name,
            rank=int(body["rank"]),
            run_suites=bool(body.get("run_suites", True)),
            tiers=tuple(int(t) for t in body.get("tiers", [])),
            extras=extras,
        )
    if not profiles:
        raise ConfigError("define at least one [profiles.<name>]")

    def need_profile(name: str, where: str) -> str:
        if name not in profiles:
            raise ConfigError(f"{where}: unknown profile '{name}'")
        return name

    type_rules: dict[str, TypeRule] = {}
    for ctype, body in _table(raw, "types").items():
        if ctype not in types:
            raise ConfigError(f"[types.{ctype}] is not listed in [commits] types")
        type_rules[ctype] = TypeRule(
            profile=need_profile(body["profile"], f"[types.{ctype}]"),
            release=_release(body.get("release", "none"), f"[types.{ctype}]"),
        )
    missing = [t for t in types if t not in type_rules]
    if missing:
        raise ConfigError(f"no [types.<name>] rule for: {', '.join(missing)}")

    overrides = tuple(
        Override(
            pattern=_compile(o["match"], "[[overrides]]"),
            profile=need_profile(o["profile"], "[[overrides]]"),
            release=_release(o["release"], "[[overrides]]") if "release" in o else None,
        )
        for o in raw.get("overrides", [])
    )

    b = _table(raw, "breaking")
    breaking = Breaking(
        profile=need_profile(b.get("profile", next(iter(profiles))), "[breaking]"),
        release=_release(b.get("release", "major"), "[breaking]"),
        tiers=tuple(int(t) for t in b.get("tiers", [])),
        extras=_extras(b.get("extras", []), "[breaking] extras"),
    )

    paths = _table(raw, "paths")
    default_floor = need_profile(paths.get("default", "full"), "[paths] default")
    path_floors = tuple(
        PathFloor(
            globs=_tuple(f["globs"], "[[paths.floor]] globs"),
            profile=need_profile(f["profile"], "[[paths.floor]]"),
        )
        for f in paths.get("floor", [])
    )

    packages = _packages(raw, root, registries)
    if not packages:
        raise ConfigError(
            "define at least one package: a [[discover]] block, or a [[packages]] entry "
            '(path = "." for a single package)'
        )
    for pkg in packages.values():
        for dep in pkg.depends_on:
            if dep not in packages:
                raise ConfigError(f"package '{pkg.name}' depends on unknown package '{dep}'")
    cycles = _cycles_mode(raw)
    cycles_found: tuple[tuple[str, ...], ...] = ()
    if cycles == "error":
        _check_acyclic(packages)
    else:
        cycles_found = graph.find_cycles({n: p.runtime_depends_on for n, p in packages.items()})

    path_rules = tuple(
        PathRule(
            globs=_tuple(r["globs"], "[[path_rules]] globs"),
            packages=_tuple(r["packages"], "[[path_rules]] packages"),
        )
        for r in raw.get("path_rules", [])
    )
    for rule in path_rules:
        for name in rule.packages:
            if name not in packages:
                raise ConfigError(f"[[path_rules]] references unknown package '{name}'")

    tiers: dict[int, Tier] = {}
    for number, body in _table(raw, "tiers").items():
        n = int(number)
        compare = body.get("compare", "none")
        selector = body.get("packages", "all")
        if compare not in COMPARE_MODES:
            raise ConfigError(f"[tiers.{n}] compare must be one of {', '.join(COMPARE_MODES)}")
        if selector not in PACKAGE_SELECTORS:
            raise ConfigError(f"[tiers.{n}] packages must be one of {', '.join(PACKAGE_SELECTORS)}")
        markers = body.get("markers", "")
        if not re.fullmatch(r"[\w\s()]*", markers):
            # Markers are substituted into shell commands, so only a boolean expression of words.
            raise ConfigError(f"[tiers.{n}] markers may only contain words, spaces and parentheses")
        selection = body.get("selection", False)
        if not isinstance(selection, bool):
            raise ConfigError(f"[tiers.{n}] selection must be true or false")
        if selection and compare == "none":
            raise ConfigError(f"[tiers.{n}] selection needs a diff, but compare is 'none'")
        tiers[n] = Tier(
            number=n,
            name=body.get("name", f"tier-{n}"),
            compare=compare,
            packages=selector,
            markers=markers,
            run=_tuple(body.get("run", []), f"[tiers.{n}] run"),
            extras=_extras(body.get("extras", []), f"[tiers.{n}] extras"),
            selection=selection,
        )
    if not tiers:
        raise ConfigError("define at least one [tiers.<n>]")

    rt = _table(raw, "runtime")
    runtime = Runtime(
        runs_on=str(rt.get("runs_on", "ubuntu-24.04")),
        python_version=str(rt.get("python_version", "")),
        node_version=str(rt.get("node_version", "")),
        setup_command=str(rt.get("setup_command", "")),
    )
    if not re.fullmatch(r"[\w.\-]+", runtime.runs_on):
        raise ConfigError("[runtime] runs_on must be a single runner label")
    for key in ("python_version", "node_version"):
        if not re.fullmatch(r"[\w.\-*]*", getattr(runtime, key)):
            raise ConfigError(f"[runtime] {key} may only contain letters, digits, dots and dashes")

    actions, inventories, rules = _rules(raw, profiles, tiers_present=set(tiers))
    policy = _table(raw, "rules_policy").get("unmapped", "ignore")
    if policy not in UNMAPPED_POLICIES:
        raise ConfigError(f"[rules_policy] unmapped must be one of {', '.join(UNMAPPED_POLICIES)}")

    impact_table = dict(_table(raw, "impact"))
    engine = str(impact_table.pop("engine", "native"))
    if engine not in registries.engines:
        raise ConfigError(
            str(UnknownPluginError("impact engine", engine, registries.engines.names()))
        )

    executor_options = _executor_options(raw, registries)
    for package in packages.values():
        if package.executor and package.executor not in registries.executors:
            raise ConfigError(
                f"package '{package.name}': "
                + str(
                    UnknownPluginError("executor", package.executor, registries.executors.names())
                )
            )

    return Config(
        types=types,
        require_ticket=bool(commits.get("require_ticket", True)),
        ticket_exempt=tuple(
            _compile(p, "[commits] ticket_exempt") for p in commits.get("ticket_exempt", [])
        ),
        type_rules=type_rules,
        overrides=overrides,
        breaking=breaking,
        profiles=profiles,
        docs_globs=_tuple(paths.get("docs", []), "[paths] docs"),
        path_floors=path_floors,
        default_floor=default_floor,
        global_globs=_tuple(paths.get("global", []), "[paths] global"),
        packages=packages,
        path_rules=path_rules,
        tiers=tiers,
        fallback_profile=need_profile(commits.get("fallback_profile", "full"), "[commits]"),
        runtime=runtime,
        rules=rules,
        actions=actions,
        inventories=inventories,
        unmapped=policy,
        impact=ImpactSettings(engine=engine, options=impact_table),
        executor_options=executor_options,
        git_unknown_base=_unknown_base(raw),
        full_label=_full_label(raw),
        narrow_lockfiles=_narrow_lockfiles(raw),
        cycles=cycles,
        cycles_found=cycles_found,
        registries=registries,
        root=root,
        source=source,
    )


def _unknown_base(raw: dict[str, Any]) -> str:
    value = _table(raw, "git").get("unknown_base", "full")
    if value not in UNKNOWN_BASE_MODES:
        raise ConfigError(f"[git] unknown_base must be one of {', '.join(UNKNOWN_BASE_MODES)}")
    return value


def _cycles_mode(raw: dict[str, Any]) -> str:
    table = _table(raw, "graph")
    unknown = sorted(set(table) - {"cycles"})
    if unknown:
        raise ConfigError(f"[graph] has unknown key '{unknown[0]}'; known: cycles")
    value = table.get("cycles", "error")
    if value not in CYCLE_MODES:
        raise ConfigError(f"[graph] cycles must be one of {', '.join(CYCLE_MODES)}")
    return value


def _full_label(raw: dict[str, Any]) -> str:
    value = _table(raw, "labels").get("full", "ci:full")
    if not isinstance(value, str) or "," in value or len(value) > MAX_LABEL:
        # Labels can arrive as a comma-separated list, so a name with a comma could never match.
        raise ConfigError(
            f"[labels] full must be a label name: no commas, at most {MAX_LABEL} characters"
        )
    return value.strip()


def _narrow_lockfiles(raw: dict[str, Any]) -> tuple[str, ...]:
    globs = _tuple(_table(raw, "lockfiles").get("narrow", []), "[lockfiles] narrow")
    for glob in globs:
        segment = glob.rsplit("/", 1)[-1]
        if segment not in NARROWABLE_LOCKFILES:
            raise ConfigError(
                f"[lockfiles] narrow: picard cannot parse '{segment}'; "
                f"it can narrow {', '.join(NARROWABLE_LOCKFILES)}"
            )
    return globs


def _executor_options(raw: dict[str, Any], registries: Registries) -> dict[str, dict[str, Any]]:
    """[executors.<name>] tables. Building each executor once validates its options here."""
    options: dict[str, dict[str, Any]] = {}
    for name, body in _table(raw, "executors").items():
        if not isinstance(body, dict):
            raise ConfigError(f"[executors.{name}] must be a table")
        if name not in registries.executors:
            raise ConfigError(
                f"[executors.{name}] "
                + str(UnknownPluginError("executor", name, registries.executors.names()))
            )
        try:
            registries.executors.get(name)(dict(body))
        except ValueError as exc:
            raise ConfigError(f"[executors.{name}] {exc}") from exc
        options[name] = dict(body)
    return options


def _test_paths(value: Any, where: str, quoted: str) -> tuple[str, ...]:
    globs = _tuple(value, f"{where} test_paths")
    if any(re.search(r"\{[^}]*\}", glob) for glob in globs):
        raise ConfigError(f"{quoted} test_paths: captures like {{name}} are not allowed")
    return globs


def _always(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"package '{name}': always must be true or false")
    return value


def _shards(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SHARDS:
        raise ConfigError(f"package '{name}': shards must be an integer from 1 to {MAX_SHARDS}")
    return value


def _rules(
    raw: dict[str, Any], profiles: dict[str, Profile], tiers_present: set[int]
) -> tuple[dict[str, ActionDef], dict[str, Inventory], tuple[Rule, ...]]:
    actions: dict[str, ActionDef] = {}
    for name, body in _table(raw, "actions").items():
        if name in BUILTIN_ACTIONS:
            raise ConfigError(f"[actions.{name}] clashes with a built-in action")
        run = str(body["run"])
        if "{package}" in run or "{path}" in run:
            raise ConfigError(
                f"[actions.{name}]: {{package}} and {{path}} belong to package commands"
            )
        tiers = body.get("tiers")
        needs = body.get("needs")
        actions[name] = ActionDef(
            name=name,
            run=run,
            tiers=tuple(int(t) for t in tiers) if tiers is not None else None,
            needs=_tuple(needs, f"[actions.{name}] needs") if needs is not None else None,
        )

    inventories: dict[str, Inventory] = {}
    for name, body in _table(raw, "inventory").items():
        inventories[name] = Inventory(
            name=name,
            file=str(body["file"]),
            entries=str(body["entries"]),
            directory_field=str(body.get("directory_field", "directory")),
            id_field=str(body.get("id_field", "id")),
            status_field=str(body.get("status_field", "status")),
            runnable=_tuple(body.get("runnable", ["recorded"]), f"[inventory.{name}] runnable"),
        )

    rules: list[Rule] = []
    seen: set[str] = set()
    for index, body in enumerate(raw.get("rules", [])):
        name = str(body.get("name", f"rule-{index + 1}"))
        where = f"[[rules]] '{name}'"
        if name in seen:
            raise ConfigError(f"{where}: duplicate rule name")
        seen.add(name)
        match = _tuple(body["match"], f"{where} match")
        acts = _tuple(body["actions"], f"{where} actions")
        try:
            captured = tuple(dict.fromkeys(c for pattern in match for c in graph_captures(pattern)))
        except (ValueError, re.error) as exc:
            raise ConfigError(f"{where}: bad match pattern: {exc}") from exc
        for act in acts:
            if act.startswith("run:"):
                target = act[4:]
                if target not in actions:
                    raise ConfigError(f"{where}: unknown action '{act}', define [actions.{target}]")
                needed = set(re.findall(r"\{(\w+)\}", actions[target].run)) - set(PLACEHOLDERS)
                needed = {
                    n[:-4] if n.endswith("_dir") and n[:-4] in captured else n for n in needed
                }
                missing = needed - set(captured)
                if missing:
                    raise ConfigError(
                        f"{where}: action '{act}' needs {{{', '.join(sorted(missing))}}} "
                        "but no match pattern captures it"
                    )
            elif act not in BUILTIN_ACTIONS:
                raise ConfigError(
                    f"{where}: unknown action '{act}', use {', '.join(BUILTIN_ACTIONS)} or run:<name>"
                )
        profile = body.get("profile")
        if profile is not None and profile not in profiles:
            raise ConfigError(f"{where}: unknown profile '{profile}'")
        tiers = tuple(int(t) for t in body.get("tiers", [0, 1, 2]))
        for action_def in actions.values():
            if action_def.tiers and not set(action_def.tiers) <= tiers_present:
                raise ConfigError(f"[actions.{action_def.name}] tiers are not all defined")
        if not set(tiers) <= tiers_present:
            raise ConfigError(
                f"{where}: tiers {sorted(set(tiers) - tiers_present)} are not defined"
            )
        rules.append(Rule(name, match, acts, profile, tiers, captured))
    return actions, inventories, tuple(rules)


def _packages(raw: dict[str, Any], root: Path | None, registries: Registries) -> dict[str, Package]:
    """Discovered packages first, then [[packages]] entries merged over them by name."""
    packages: dict[str, Package] = {}
    for block in raw.get("discover", []):
        provider = block["provider"]
        if root is None:
            raise ConfigError("[[discover]] needs a repository root to read manifests from")
        executor = block.get("executor")
        if executor is not None and executor not in registries.executors:
            raise ConfigError(
                "[[discover]] "
                + str(UnknownPluginError("executor", executor, registries.executors.names()))
            )
        # Everything in the block that is not a known key is an option of the provider.
        own_keys = ("provider", "commands", "executor", "test_paths")
        options = {k: v for k, v in block.items() if k not in own_keys}
        test_paths = _test_paths(block.get("test_paths", []), "[[discover]]", "[[discover]]")
        try:
            found = graph.discover(provider, root, options=options, registries=registries)
        except graph.DiscoveryError as exc:
            raise ConfigError(f"[[discover]] {exc}") from exc
        commands = {str(k): str(v) for k, v in block.get("commands", {}).items()}
        for item in found:
            if item.name in packages:
                raise ConfigError(f"package '{item.name}' is discovered by more than one provider")
            packages[item.name] = Package(
                name=item.name,
                path=item.path,
                depends_on=item.depends_on,
                baseline=False,
                commands=dict(commands),
                provider=item.provider,
                executor=executor,
                test_paths=test_paths,
                dev_depends_on=item.dev_depends_on,
            )

    for p in raw.get("packages", []):
        name = p["name"]
        extra_deps = _tuple(p.get("depends_on", []), f"package {name} depends_on")
        extra_commands = {str(k): str(v) for k, v in p.get("commands", {}).items()}
        base = packages.get(name)
        if base is None:
            packages[name] = Package(
                name=name,
                path=p["path"].strip("/") or ".",
                depends_on=extra_deps,
                baseline=bool(p.get("baseline", False)),
                commands=extra_commands,
                executor=p.get("executor"),
                always=_always(p.get("always", False), name),
                shards=_shards(p.get("shards", 1), name),
                test_paths=_test_paths(
                    p.get("test_paths", []), f"package {name}", f"package '{name}'"
                ),
            )
        else:
            packages[name] = Package(
                name=name,
                path=(p["path"].strip("/") or ".") if "path" in p else base.path,
                depends_on=tuple(dict.fromkeys((*base.depends_on, *extra_deps))),
                dev_depends_on=tuple(d for d in base.dev_depends_on if d not in extra_deps),
                baseline=bool(p.get("baseline", base.baseline)),
                commands={**base.commands, **extra_commands},
                provider=base.provider,
                executor=p.get("executor", base.executor),
                always=_always(p.get("always", base.always), name),
                shards=_shards(p.get("shards", base.shards), name),
                test_paths=(
                    _test_paths(p["test_paths"], f"package {name}", f"package '{name}'")
                    if "test_paths" in p
                    else base.test_paths
                ),
            )
    return packages


def _check_acyclic(packages: dict[str, Package]) -> None:
    state: dict[str, int] = {}

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            raise ConfigError(
                "package dependency cycle: "
                + " -> ".join((*trail, name))
                + "; edges that exist only in a dev group are not counted, to accept a real cycle"
                + ' set [graph] cycles = "allow"'
            )
        state[name] = 1
        for dep in packages[name].runtime_depends_on:
            visit(dep, (*trail, name))
        state[name] = 2

    for name in packages:
        visit(name, ())


def load(path: str | Path, root: str | Path | None = None) -> Config:
    p = Path(path)
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {p}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{p}: invalid TOML: {exc}") from exc
    try:
        return parse(raw, p, Path(root) if root is not None else Path.cwd())
    except KeyError as exc:
        raise ConfigError(f"{p}: missing required key {exc}") from exc
