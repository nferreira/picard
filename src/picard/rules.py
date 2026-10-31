"""File-change rules: what must run when files change that the code graph cannot see.

The dependency graph answers "which packages are affected by a code change". Configs, schemas,
baselines, datasets, docs and automation have no import graph, so a `[[rules]]` entry says what
must happen when they change.

Resolution:
  1. For each changed file the FIRST rule (top to bottom) whose `match` fits is used.
  2. The results of all files are merged (union).
  3. `full` absorbs everything.
  4. A file no rule matches goes to the code graph, as before. When `[rules_policy] unmapped`
     is "full" and the file is not in any package, docs, a global file or a `[lockfiles] narrow`
     glob either, it runs
     everything (fail-safe), because nobody said what it means.
  5. A file a rule matches reaches the code graph only if the rule says `package` or `own`,
     so a YAML-only change does not run the unit suites.

Captured names (`{handler}`) end up in shell commands and come from file paths a PR controls,
so they are checked against a strict pattern and anything else runs everything.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from picard import graph
from picard.config import PLACEHOLDERS, SAFE_VALUE, Config, Inventory, Rule
from picard.globs import match_captures, matches

SAMPLE = 5


@dataclass
class Action:
    id: str
    name: str  # the [actions.<name>] it runs
    command: str
    rule: str
    tiers: tuple[int, ...]
    captures: dict[str, str]


@dataclass
class Resolution:
    graph_files: list[str] = field(default_factory=list)  # files the code graph should consider
    own: set[str] = field(default_factory=set)  # packages a rule pinned with `own`
    full: bool = False
    full_reasons: list[str] = field(default_factory=list)
    hits: dict[str, list[str]] = field(default_factory=dict)  # rule name -> matched files
    rule_actions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    review: dict[str, int] = field(default_factory=dict)  # rule name -> files needing review
    unmapped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    floors: dict[str, str] = field(default_factory=dict)  # file -> profile demanded by its rule

    def rules_summary(self) -> list[dict]:
        return [
            {
                "name": name,
                "files": len(files),
                "sample": files[:SAMPLE],
                "actions": list(self.rule_actions[name]),
            }
            for name, files in self.hits.items()
        ]


def _load_inventory(cfg: Config, inventory: Inventory) -> dict[str, tuple[str, str]]:
    """directory -> (id, status)."""
    if cfg.root is None:
        raise ValueError("no repository root to read the inventory from")
    data = tomllib.loads((cfg.root / inventory.file).read_text(encoding="utf-8"))
    return {
        str(e[inventory.directory_field]): (
            str(e[inventory.id_field]),
            str(e[inventory.status_field]),
        )
        for e in data.get(inventory.entries, [])
    }


def _first_match(cfg: Config, path: str) -> tuple[Rule | None, dict[str, str]]:
    for rule in cfg.rules:
        captured = match_captures(path, rule.match)
        if captured is not None:
            return rule, captured
    return None, {}


def resolve(cfg: Config, files: list[str]) -> Resolution:
    res = Resolution()
    pending: dict[tuple, tuple[Rule, str, dict[str, str]]] = {}

    for path in files:
        rule, captured = _first_match(cfg, path)
        if rule is None:
            res.graph_files.append(path)
            if cfg.unmapped != "ignore" and not (
                graph.owner(cfg, path)
                or matches(path, cfg.docs_globs)
                or matches(path, cfg.global_globs)
                or matches(path, cfg.narrow_lockfiles)  # attributed to packages, or everything
            ):
                res.unmapped.append(path)
            continue
        res.hits.setdefault(rule.name, []).append(path)
        res.rule_actions[rule.name] = rule.actions
        if rule.profile:
            res.floors[path] = rule.profile
        for action in rule.actions:
            if action == "package":
                res.graph_files.append(path)
            elif action == "own":
                if owner := graph.owner(cfg, path):
                    res.own.add(owner)
            elif action == "full":
                res.full = True
                if rule.name not in res.full_reasons:
                    res.full_reasons.append(rule.name)
            elif action == "require-review":
                res.review[rule.name] = res.review.get(rule.name, 0) + 1
            elif action.startswith("run:"):
                key = (action[4:], rule.name, tuple(sorted(captured.items())))
                pending.setdefault(key, (rule, action[4:], captured))

    _resolve_actions(cfg, res, pending)

    if res.unmapped:
        shown = ", ".join(res.unmapped[:SAMPLE]) + (" ..." if len(res.unmapped) > SAMPLE else "")
        message = f"{len(res.unmapped)} file(s) match no rule and no package: {shown}"
        if cfg.unmapped == "full":
            res.full = True
            res.full_reasons.append("unmapped files")
            res.warnings.append(f"{message}; running everything")
        else:
            res.warnings.append(message)
    res.warnings = list(dict.fromkeys(res.warnings))
    return res


def _resolve_actions(cfg: Config, res: Resolution, pending: dict) -> None:
    inventories: dict[str, dict[str, tuple[str, str]]] = {}

    def fail_safe(message: str) -> None:
        res.warnings.append(f"{message}; running everything")
        res.full = True
        res.full_reasons.append("unresolvable name")

    for (action_name, _rule_name, _caps), (rule, _, captured) in pending.items():
        definition = cfg.actions[action_name]
        values: dict[str, str] = {}
        runnable = True
        usable = True
        for key, directory in captured.items():
            inventory = cfg.inventories.get(key)
            value = directory
            if inventory is not None:
                if key not in inventories:
                    try:
                        inventories[key] = _load_inventory(cfg, inventory)
                    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
                        inventories[key] = {}
                        fail_safe(f"cannot read inventory '{key}' ({inventory.file}): {exc}")
                entry = inventories[key].get(directory)
                if entry is None:
                    fail_safe(f"{key} directory '{directory}' is not in {inventory.file}")
                    usable = False
                    break
                value, status = entry
                allowed = definition.needs if definition.needs is not None else inventory.runnable
                uses = f"{{{key}}}" in definition.run or f"{{{key}_dir}}" in definition.run
                if status not in allowed and uses:
                    runnable = False
                    res.warnings.append(
                        f"{key} '{value}' cannot run '{action_name}' (status: {status}, "
                        f"needs: {', '.join(allowed)}), skipped"
                    )

            if not SAFE_VALUE.fullmatch(value) or not SAFE_VALUE.fullmatch(directory):
                fail_safe(f"unsafe {key} name {directory!r}")
                usable = False
                break
            values[key] = value
            values[f"{key}_dir"] = directory
        if not usable or not runnable:
            continue
        command = definition.run
        for key, value in values.items():
            command = command.replace(f"{{{key}}}", value)
        assert not (set(re.findall(r"\{(\w+)\}", command)) - set(PLACEHOLDERS))
        suffix = "".join(f"[{k}={v}]" for k, v in sorted(values.items()) if not k.endswith("_dir"))
        res.actions.append(
            Action(
                id=f"rule:{action_name}{suffix}",
                name=action_name,
                command=command,
                rule=rule.name,
                tiers=definition.tiers if definition.tiers is not None else rule.tiers,
                captures=values,
            )
        )
    # One command per action and captures, even when several rules or files asked for it.
    res.actions = list({a.id: a for a in res.actions}.values())
