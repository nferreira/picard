"""Build the plan one pipeline tier executes.

Inputs: the config, the tier, the PR title(s) and the changed files. Output: a plain dict
(JSON-serialisable) listing the profile, the release effect, the packages and the commands
to run, and the extra jobs to enable. Workflows only read that dict, they never decide.

Profile selection (PR tiers) takes the strongest of:
  1. the Conventional Commit type (or the first matching [[overrides]] rule),
  2. the breaking-change floor, when the title has `!` or a BREAKING CHANGE footer,
  3. the paths touched, so a `docs:` title cannot hide a source change.
Titles can only raise the bar that paths set, never lower it, and an unparseable title
falls back to the strictest profile (fail closed).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from picard import commits as cc
from picard import graph, rules
from picard.config import RELEASE_ORDER, Config, Package
from picard.executors import Executor, SuiteContext
from picard.globs import matches

SCHEMA = 1
MAX_LISTED_FILES = 200


def _max_release(*values: str) -> str:
    return max(values, key=RELEASE_ORDER.index)


def affected_packages(cfg: Config, files: Sequence[str]) -> list[str]:
    """Packages touched by `files`, plus everything that depends on them, transitively."""
    return list(graph.compute_impact(cfg, files).affected)


def baseline_packages(
    cfg: Config, files: Sequence[str], impact: graph.Impact | None = None
) -> list[str]:
    """Affected packages, packages pulled in by path rules, and always-on baseline packages."""
    chosen = set((impact or graph.compute_impact(cfg, files)).affected)
    chosen.update(p.name for p in cfg.packages.values() if p.baseline)
    for rule in cfg.path_rules:
        if any(matches(f, rule.globs) for f in files):
            chosen.update(rule.packages)
    return [name for name in cfg.packages if name in chosen]


def _file_floor(cfg: Config, path: str, overrides: dict[str, str] | None = None) -> str | None:
    """Minimum profile a single file demands. A rule's `profile` wins, then docs, then floors."""
    if overrides and path in overrides:
        return overrides[path]
    if matches(path, cfg.docs_globs):
        return min(cfg.profiles, key=cfg.rank)
    for floor in cfg.path_floors:
        if matches(path, floor.globs):
            return floor.profile
    return cfg.default_floor


def _commit_profile(cfg: Config, commit: cc.Commit) -> tuple[str, str, str]:
    """(profile, release, reason) for one parsed title."""
    for override in cfg.overrides:
        if override.pattern.search(commit.raw):
            rule = cfg.type_rules.get(commit.type or "")
            release = override.release or (rule.release if rule else "none")
            return override.profile, release, f"title matches override {override.pattern.pattern!r}"
    rule = cfg.type_rules.get(commit.type or "")
    if rule is None:
        return cfg.fallback_profile, "none", "title has no known type, using the strictest profile"
    return rule.profile, rule.release, f"type '{commit.type}'"


def _ordered_unique(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _executor(cfg: Config, cache: dict[str, Executor], name: str) -> Executor:
    if name not in cache:
        cache[name] = cfg.registries.executors.get(name)(cfg.executor_options.get(name))
    return cache[name]


def _render(command: str, package: Package, markers: str) -> str:
    return (
        command.replace("{package}", package.name)
        .replace("{path}", package.path)
        .replace("{markers}", markers)
    )


def build_plan(
    cfg: Config,
    tier_number: int,
    *,
    titles: Sequence[str] = (),
    body: str = "",
    changed_files: Sequence[str] = (),
    paths_only: bool = False,
) -> dict[str, Any]:
    """Plan one tier. With `paths_only` there is no title: the profile follows the paths alone."""
    if tier_number not in cfg.tiers:
        raise ValueError(f"tier {tier_number} is not defined in the config")
    tier = cfg.tiers[tier_number]
    files = sorted(set(changed_files))
    plan: dict[str, Any] = {
        "schema": SCHEMA,
        "tier": tier_number,
        "name": tier.name,
        "compare": tier.compare,
        "skip": False,
        "skip_reason": None,
        "profile": None,
        "release": None,
        "commits": [],
        # A 2,400-file list is ~190 KB on one line, past the 128 KiB a single env var or argument
        # may hold. Workflows never need it, so the plan carries a sample and the total.
        "changed_files": files[:MAX_LISTED_FILES],
        "changed_files_total": len(files),
        "rules": [],
        "review": [],
        "unmapped": [],
        "packages": [],
        "markers": tier.markers,
        "suites": [],
        "extras": [],
        "reasons": [],
        "warnings": [],
        "impact": None,
        "runtime": {
            "runs_on": cfg.runtime.runs_on,
            "python_version": cfg.runtime.python_version,
            "node_version": cfg.runtime.node_version,
            "setup_command": cfg.runtime.setup_command,
        },
    }
    reasons: list[str] = plan["reasons"]
    warnings: list[str] = plan["warnings"]
    resolution = rules.Resolution()

    if tier.compare == "none":
        # Scheduled and merge-queue tiers ignore the diff and the title: they run everything.
        selected = list(cfg.packages)
        reasons.append(f"tier {tier_number} ({tier.name}) runs every package")
        plan["extras"] = list(tier.extras)
    else:
        parsed = [
            cc.parse_title(
                t,
                types=cfg.types,
                require_ticket=cfg.require_ticket,
                ticket_exempt=cfg.ticket_exempt,
                body=body,
            )
            for t in titles
        ]
        if paths_only:
            parsed = []
        elif not parsed:
            parsed = [cc.parse_title("", types=cfg.types)]
        plan["commits"] = [
            {
                "title": c.raw,
                "valid": c.valid,
                "type": c.type,
                "scope": c.scope,
                "breaking": c.breaking,
                "ticket": c.ticket,
                "errors": list(c.errors),
            }
            for c in parsed
        ]

        profile, release = None, "none"
        for commit in parsed:
            if not commit.valid and commit.type not in cfg.type_rules:
                warnings.append(f"invalid title {commit.raw!r}: {'; '.join(commit.errors)}")
            name, rel, why = _commit_profile(cfg, commit)
            reasons.append(f"{why} -> profile '{name}'")
            release = _max_release(release, rel)
            if profile is None or cfg.rank(name) > cfg.rank(profile):
                profile = name
        if paths_only:
            profile = min(cfg.profiles, key=cfg.rank)
            reasons.append("no title given: the profile follows the changed paths alone")
        assert profile is not None

        breaking = any(c.breaking for c in parsed)
        enabled = set(cfg.profiles[profile].tiers)
        if breaking:
            if cfg.rank(cfg.breaking.profile) > cfg.rank(profile):
                profile = cfg.breaking.profile
            release = _max_release(release, cfg.breaking.release)
            enabled.update(cfg.breaking.tiers)
            reasons.append(
                f"breaking change -> at least '{cfg.breaking.profile}', release {cfg.breaking.release}"
            )

        type_profile = profile
        resolution = rules.resolve(cfg, files)
        if files:
            floors = [(f, _file_floor(cfg, f, resolution.floors)) for f in files]
            strongest_file, strongest = max(floors, key=lambda pair: cfg.rank(pair[1]))
            if cfg.rank(strongest) > cfg.rank(profile):
                warnings.append(
                    f"title says '{profile}' but {strongest_file} needs '{strongest}', "
                    "so the stricter profile runs"
                )
                reasons.append(f"path {strongest_file} -> profile '{strongest}'")
                profile = strongest
                enabled.update(cfg.profiles[profile].tiers)
            elif cfg.rank(strongest) < cfg.rank(type_profile):
                warnings.append(
                    f"title says '{type_profile}' but only low-risk files changed "
                    f"(strongest: {strongest_file}); release effect stays '{release}'"
                )

        plan["profile"] = profile
        plan["release"] = release
        prof = cfg.profiles[profile]

        if not files:
            plan["skip"], plan["skip_reason"] = True, "no changed files"
        elif tier_number not in enabled:
            plan["skip"] = True
            plan["skip_reason"] = f"profile '{profile}' does not run tier {tier_number}"

        extras = list(prof.extras.get(tier_number, ()))
        if breaking and tier_number in cfg.breaking.tiers:
            extras.extend(cfg.breaking.extras)
        plan["extras"] = _ordered_unique(extras) if not plan["skip"] else []

        impact = _impact(cfg, resolution)
        warnings.extend(impact.notes)
        plan["rules"] = resolution.rules_summary()
        plan["review"] = [{"rule": r, "files": n} for r, n in resolution.review.items()]
        plan["unmapped"] = resolution.unmapped[:MAX_LISTED_FILES]
        warnings.extend(resolution.warnings)
        if files:
            plan["impact"] = {
                "changed": list(impact.changed),
                "everything": impact.everything,
                "global_files": list(impact.global_files),
                "affected": {name: list(chain) for name, chain in impact.affected.items()},
                "why": [
                    *graph.render_why(impact),
                    *(f"rule '{name}' says full" for name in resolution.full_reasons),
                ],
            }

        if tier.packages == "affected":
            selected = list(impact.affected)
        elif tier.packages == "baseline":
            selected = baseline_packages(cfg, resolution.graph_files, impact)
            added = [n for n in selected if n not in impact.affected]
            if added:
                reasons.append(
                    f"baseline also runs {', '.join(added)} (path rule or baseline = true)"
                )
        else:
            selected = list(cfg.packages)
        if not prof.run_suites:
            selected = []
            reasons.append(f"profile '{profile}' runs no test suites")
        if plan["skip"]:
            selected = []

    plan["packages"] = selected
    suites = []
    executors: dict[str, Executor] = {}
    for name in selected:
        package = cfg.packages[name]
        for suite in tier.run:
            # `test@pr-baseline` overrides `test` for that tier; an empty value skips it there.
            key = f"{suite}@{tier.name}"
            command = package.commands.get(key, package.commands.get(suite))
            explicit = key in package.commands or suite in package.commands
            if not explicit and package.executor:
                command = _executor(cfg, executors, package.executor).command(
                    SuiteContext(package, suite, tier.name, tier.markers)
                )
            if command:
                suites.append(
                    {
                        "id": f"{name}:{suite}",
                        "package": name,
                        "suite": suite,
                        "command": _render(command, package, tier.markers),
                    }
                )
    if tier.compare != "none" and not plan["skip"]:
        for action in resolution.actions:
            if tier_number in action.tiers:
                suites.append(
                    {
                        "id": action.id,
                        "package": None,
                        "suite": action.name,
                        "command": action.command.replace("{markers}", tier.markers),
                        "rule": action.rule,
                    }
                )
    plan["suites"] = suites
    return plan


def impact_of(cfg: Config, files: Sequence[str]) -> graph.Impact:
    """What the code graph says a set of files affects, after the rules have had their say."""
    return _impact(cfg, rules.resolve(cfg, sorted(set(files))))


def _impact(cfg: Config, resolution: rules.Resolution) -> graph.Impact:
    """The code graph's view of the files, plus what rules pinned (`own`) or absorbed (`full`)."""
    if resolution.full:
        return graph.everything(cfg)
    impact = graph.compute_impact(cfg, resolution.graph_files)
    if not resolution.own:
        return impact
    changed = {*impact.changed, *resolution.own}
    affected = {**impact.affected}
    decided = dict(impact.decided_by)
    for name in resolution.own:
        affected.setdefault(name, (name,))
        decided.setdefault(name, "rule")
    return graph.Impact(
        tuple(n for n in cfg.packages if n in changed),
        {n: affected[n] for n in cfg.packages if n in affected},
        impact.everything,
        impact.global_files,
        impact.notes,
        {n: decided[n] for n in cfg.packages if n in decided},
    )


def render_text(plan: dict[str, Any]) -> str:
    head = f"tier {plan['tier']} ({plan['name']})"
    if plan["skip"]:
        return f"{head}: skipped, {plan['skip_reason']}"
    lines = [head]
    if plan["profile"]:
        lines.append(f"  profile: {plan['profile']}   release: {plan['release']}")
    lines.append(f"  packages: {', '.join(plan['packages']) or 'none'}")
    lines.append(f"  markers: {plan['markers'] or '(all)'}")
    if plan["impact"]:
        lines.extend(f"  impact: {w}" for w in plan["impact"]["why"])
    for rule in plan["rules"]:
        lines.append(
            f"  rule: {rule['name']} ({rule['files']} file(s)) -> {', '.join(rule['actions'])}"
        )
    lines.extend(f"  review required: {r['rule']} ({r['files']} file(s))" for r in plan["review"])
    lines.extend(f"  run: {s['id']}: {s['command']}" for s in plan["suites"])
    if plan["extras"]:
        lines.append(f"  extras: {', '.join(plan['extras'])}")
    lines.extend(f"  note: {r}" for r in plan["reasons"])
    lines.extend(f"  warning: {w}" for w in plan["warnings"])
    return "\n".join(lines)


def render_markdown(plan: dict[str, Any]) -> str:
    """Step-summary view: why this PR runs what it runs."""
    title = f"### Pipeline plan, tier {plan['tier']} · {plan['name']}"
    if plan["skip"]:
        return f"{title}\n\nSkipped: {plan['skip_reason']}.\n"
    rows = [
        ("Profile", plan["profile"] or "n/a"),
        ("Release effect", plan["release"] or "n/a"),
        ("Packages", ", ".join(plan["packages"]) or "none"),
        ("Markers", plan["markers"] or "all"),
        ("Extras", ", ".join(plan["extras"]) or "none"),
    ]
    out = [title, "", "| | |", "|---|---|"]
    out.extend(f"| {k} | {v} |" for k, v in rows)
    if plan["impact"]:
        out += ["", "Impact:"] + [f"- {w}" for w in plan["impact"]["why"]]
    if plan["rules"]:
        out += ["", "Rules:"] + [
            f"- **{r['name']}** ({r['files']} file(s)): {', '.join(r['actions'])}"
            for r in plan["rules"]
        ]
    if plan["review"]:
        out += ["", "Review required:"] + [
            f"- {r['rule']} ({r['files']} file(s))" for r in plan["review"]
        ]
    if plan["suites"]:
        out += ["", "Suites: " + ", ".join(f"`{s['id']}`" for s in plan["suites"])]
    if plan["reasons"]:
        out += ["", "Why:"] + [f"- {r}" for r in plan["reasons"]]
    if plan["warnings"]:
        out += ["", "Warnings:"] + [f"- {w}" for w in plan["warnings"]]
    return "\n".join(out) + "\n"
