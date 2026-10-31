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

Labels can only raise it too: the `[labels] full` label (or a reason the caller gives, such as
a base commit that cannot be found) makes every package affected and runs the strictest
profile. A package with `always = true` is in every diff tier's affected set.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from picard import commits as cc
from picard import graph, rules
from picard.config import RELEASE_ORDER, Config, Package, Tier
from picard.executors import Executor, Shard, SuiteContext
from picard.globs import matches
from picard.graph.impact import merge as merge_impacts

SCHEMA = 1
MAX_LISTED_FILES = 200
MAX_JOBS = 256  # GitHub's limit on the jobs one matrix can expand to
NO_SELECTION = (
    "test selection needs a committed git range (--base, or --local with no uncommitted "
    "files): whole suites run"
)


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


def _render(command: str, package: Package, markers: str, shard: Shard | None = None) -> str:
    # {shard} and {shards} belong to package commands only; an unsplit suite is shard 1 of 1.
    return (
        command.replace("{package}", package.name)
        .replace("{path}", package.path)
        .replace("{markers}", markers)
        .replace("{shards}", str(shard.total if shard else 1))
        .replace("{shard}", str(shard.index if shard else 1))
    )


def _package_suites(
    cfg: Config,
    executors: dict[str, Executor],
    package: Package,
    suite: str,
    tier: Tier,
    *,
    selection_base: str | None = None,
    expand_shards: bool = False,
    warnings: list[str] | None = None,
) -> list[dict[str, Any]]:
    """The suite entries for one suite of one package: none, one, or one per shard.

    An explicit command wins over the executor; `test@<tier name>` overrides `test` for that
    tier, and an empty value skips the suite there.
    """
    key = f"{suite}@{tier.name}"
    explicit = package.commands.get(key, package.commands.get(suite))
    executor = None
    if explicit is None and package.executor:
        executor = _executor(cfg, executors, package.executor)

    def build(shard: Shard | None) -> str | None:
        if executor is None:
            return explicit
        ctx = SuiteContext(package, suite, tier.name, tier.markers, shard, selection_base)
        return executor.command(ctx)

    command = build(None)
    if not command:
        return []
    total = package.shards
    shards: list[Shard | None] = [None]
    if total > 1 and expand_shards:
        # The executor passes the tool's own split flag, or the command says where k goes.
        splits = suite in executor.shardable if executor is not None else "{shard}" in command
        if splits:
            shards = [Shard(k, total) for k in range(1, total + 1)]
        elif suite == "test" and warnings is not None:
            why = (
                f"executor '{executor.name}' cannot shard 'test'"
                if executor is not None
                else "its 'test' command has no {shard} placeholder"
            )
            warnings.append(
                f"package '{package.name}' has shards = {total} but {why}: it runs as one job"
            )
    entries = []
    for shard in shards:
        text = command if shard is None else build(shard)
        if not text:
            continue
        entry: dict[str, Any] = {
            "id": f"{package.name}:{suite}"
            + (f"#{shard.index}/{shard.total}" if shard is not None else ""),
            "package": package.name,
            "suite": suite,
            "command": _render(text, package, tier.markers, shard),
        }
        if shard is not None:
            entry["shard"], entry["shards"] = shard.index, shard.total
        entries.append(entry)
    return entries


def _forced(cfg: Config, labels: Sequence[str], full_reasons: Sequence[str]) -> list[str]:
    """Why this change must run everything, whatever it touches: a label, or the caller says so."""
    forced = list(full_reasons)
    if cfg.full_label and cfg.full_label in labels:
        forced.append(f"label {cfg.full_label} forces a full run")
    return forced


def _metrics(
    cfg: Config, tier: Tier, executors: dict[str, Executor], selected: Sequence[str], suites: list
) -> dict[str, int]:
    """What affected-based selection saved for this tier, in packages and suites."""
    suites_total = sum(
        1
        for package in cfg.packages.values()
        for suite in tier.run
        if _package_suites(cfg, executors, package, suite, tier)
    )
    suites_selected = len({(s["package"], s["suite"]) for s in suites if s["package"] is not None})
    return {
        "packages_total": len(cfg.packages),
        "packages_selected": len(selected),
        "suites_total": suites_total,
        "suites_selected": suites_selected,
        "jobs": len(suites),
        "selected_pct": round(100 * suites_selected / suites_total) if suites_total else 0,
    }


def build_plan(
    cfg: Config,
    tier_number: int,
    *,
    titles: Sequence[str] = (),
    body: str = "",
    changed_files: Sequence[str] = (),
    paths_only: bool = False,
    revisions: graph.Revisions | None = None,
    labels: Sequence[str] = (),
    full_reasons: Sequence[str] = (),
    expand_shards: bool = True,
) -> dict[str, Any]:
    """Plan one tier. With `paths_only` there is no title: the profile follows the paths alone.

    `revisions` are the commits the change lies between (None without a git range), `labels`
    the PR's labels and `full_reasons` reasons the caller already knows to run everything.
    `expand_shards=False` (local runs) keeps every suite as one command.
    """
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
        "labels": list(labels),
        "forced_full": [],
        "revisions": (
            {"base": revisions.base, "head": revisions.head} if revisions is not None else None
        ),
        "metrics": {},
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

        forced = _forced(cfg, labels, full_reasons)
        plan["forced_full"] = forced
        if forced:
            # "Run everything for this PR": every package, and every tier the strictest runs.
            profile = max(cfg.profiles, key=cfg.rank)
            enabled.update(cfg.profiles[profile].tiers)
            reasons.extend(forced)

        plan["profile"] = profile
        plan["release"] = release
        prof = cfg.profiles[profile]

        if not files and not forced:
            plan["skip"], plan["skip_reason"] = True, "no changed files"
        elif tier_number not in enabled:
            plan["skip"] = True
            plan["skip_reason"] = f"profile '{profile}' does not run tier {tier_number}"

        extras = list(prof.extras.get(tier_number, ()))
        if breaking and tier_number in cfg.breaking.tiers:
            extras.extend(cfg.breaking.extras)
        plan["extras"] = _ordered_unique(extras) if not plan["skip"] else []

        impact = _affected(cfg, resolution, revisions, forced)
        warnings.extend(impact.notes)
        plan["rules"] = resolution.rules_summary()
        plan["review"] = [{"rule": r, "files": n} for r, n in resolution.review.items()]
        plan["unmapped"] = resolution.unmapped[:MAX_LISTED_FILES]
        warnings.extend(resolution.warnings)
        if files or forced:
            plan["impact"] = {
                "changed": list(impact.changed),
                "everything": impact.everything,
                "global_files": list(impact.global_files),
                "affected": {name: list(chain) for name, chain in impact.affected.items()},
                "why": [
                    *graph.render_why(impact),
                    *(f"rule '{name}' says full" for name in resolution.full_reasons),
                    *forced,
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
    # Test selection narrows inside a suite, so it needs a base that holds every changed line:
    # with uncommitted files or no git range there is none, and whole suites run (fail closed).
    # A forced-full run asked for whole suites, so it never selects either.
    selection_base = None
    if not tier.selection or plan["forced_full"]:
        pass
    elif revisions is not None and not revisions.uncommitted:
        selection_base = revisions.base
    else:
        warnings.append(NO_SELECTION)
    suites = []
    executors: dict[str, Executor] = {}
    for name in selected:
        for suite in tier.run:
            suites.extend(
                _package_suites(
                    cfg,
                    executors,
                    cfg.packages[name],
                    suite,
                    tier,
                    selection_base=selection_base,
                    expand_shards=expand_shards,
                    warnings=warnings,
                )
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
    if len(suites) > MAX_JOBS:
        warnings.append(
            f"tier {tier_number} has {len(suites)} jobs, over GitHub's {MAX_JOBS}-job matrix "
            "limit: the workflow will fail"
        )
    plan["metrics"] = _metrics(cfg, tier, executors, selected, suites)
    return plan


def impact_of(
    cfg: Config,
    files: Sequence[str],
    *,
    revisions: graph.Revisions | None = None,
    labels: Sequence[str] = (),
    full_reasons: Sequence[str] = (),
) -> graph.Impact:
    """The affected set of a change: the code graph after the rules, `always` and forced-full.

    It is what a tier with `packages = "affected"` runs, before the profile has its say.
    """
    resolution = rules.resolve(cfg, sorted(set(files)))
    return _affected(cfg, resolution, revisions, _forced(cfg, labels, full_reasons))


def _affected(
    cfg: Config,
    resolution: rules.Resolution,
    revisions: graph.Revisions | None,
    forced: Sequence[str],
) -> graph.Impact:
    """Everything when forced, else the impact plus the packages marked `always = true`."""
    if forced:
        return graph.everything(cfg, by="forced")
    impact = _impact(cfg, resolution, revisions)
    always = [n for n, p in cfg.packages.items() if p.always and n not in impact.affected]
    if not always:
        return impact
    pinned = graph.Impact(
        (), {n: (n,) for n in always}, False, (), (), dict.fromkeys(always, "always")
    )
    return merge_impacts(cfg, impact, pinned)


def _impact(
    cfg: Config, resolution: rules.Resolution, revisions: graph.Revisions | None = None
) -> graph.Impact:
    """The code graph's view of the files, plus what rules pinned (`own`) or absorbed (`full`)."""
    if resolution.full:
        return graph.everything(cfg)
    impact = graph.compute_impact(cfg, resolution.graph_files, revisions)
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
        impact.test_only,
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
