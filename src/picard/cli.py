"""picard command line.

  picard plan      --tier 1 --base <sha>        compute one tier's plan
  picard run       --tier 0 --local             plan, then execute the commands
  picard simulate  --files a b c --title "..."  what would every tier do for this change?
  picard graph     --files a b c                the dependency graph with the impact marked
  picard changed                                what differs locally, and against what
  picard check-title "feat(x): y (ABC-1)"       validate a PR title / commit subject
  picard validate-config                        lint pipeline.toml and print the packages
  picard setup [--check]                        prepare the repo for the impact engine (--check: verify, for CI)

Where the changed files come from (first match wins):
  --files a b,c   simulate: exactly these paths, no git needed
  --files-from F  the same, read from a file or stdin (NUL or newline separated)
  --staged        what is in the index
  --working       every uncommitted change, staged or not, plus untracked files
  --base REF      git diff REF...HEAD (CI: the PR base commit)
  --local         everything that differs locally: commits since the branch this one grew
                  out of (see --against), plus staged, unstaged and untracked files

PR titles and bodies are read from PICARD_TITLE / PICARD_BODY when not given as
arguments, so workflows can pass untrusted text through `env:` and never through a shell.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from picard import __version__, gitutil, graph
from picard import commits as cc
from picard import config as cfgmod
from picard.graph.impact import SetupError
from picard.planner import build_plan, impact_of, render_markdown, render_text
from picard.scaffold import ScaffoldError

DEFAULT_CONFIG = ".picard/pipeline.toml"


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config", default=DEFAULT_CONFIG, help="path to pipeline.toml (default: %(default)s)"
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="trace on stderr what the impact engines return (same as PICARD_DEBUG=1)",
    )


def _add_change_args(p: argparse.ArgumentParser) -> None:
    _add_common(p)
    p.add_argument("--files", nargs="+", metavar="PATH", help="simulate a change to these paths")
    p.add_argument(
        "--files-from",
        metavar="FILE",
        help="like --files, read from FILE or '-' (stdin), NUL or newline separated: "
        "for thousands of paths, which must never go through the command line",
    )
    p.add_argument("--staged", action="store_true", help="changed files = the git index")
    p.add_argument("--working", action="store_true", help="changed files = all uncommitted work")
    p.add_argument("--base", help="base commit or ref to compare against")
    p.add_argument("--head", default="HEAD")
    p.add_argument(
        "--local",
        action="store_true",
        help="everything that differs locally, committed or not (see --against)",
    )
    p.add_argument(
        "--against",
        default="origin-branch",
        metavar="REF",
        help="with --local: origin-branch (the branch this one grew out of, default), "
        "upstream (the remote-tracking branch, i.e. what is not pushed) or any ref",
    )
    p.add_argument(
        "--no-working", action="store_true", help="with --local: ignore uncommitted changes"
    )
    p.add_argument("--title", action="append", help="PR title or commit subject (repeatable)")
    p.add_argument("--body", help="PR body, scanned for a BREAKING CHANGE footer")


def _add_color(p: argparse.ArgumentParser) -> None:
    p.add_argument("--color", choices=("auto", "always", "never"), default="auto")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="picard", description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=f"picard {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    plan = sub.add_parser("plan", help="compute a tier's plan")
    _add_change_args(plan)
    plan.add_argument("--tier", type=int, required=True)
    plan.add_argument("--format", choices=("json", "text"), default="json")
    plan.add_argument("--github-output", help="append plan outputs to this GITHUB_OUTPUT file")
    plan.add_argument("--summary", help="append a markdown summary to this file")

    run = sub.add_parser("run", help="plan, then run the commands")
    _add_change_args(run)
    run.add_argument("--tier", type=int, required=True)
    run.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")

    sim = sub.add_parser("simulate", help="show what every tier would do for a change")
    _add_change_args(sim)
    _add_color(sim)
    sim.add_argument("--tiers", default="0,1,2,3,4", help="comma separated, default: all")
    sim.add_argument("--commands", action="store_true", help="also print every command")
    sim.add_argument("--run", action="store_true", help="execute the selected tiers in order")
    sim.add_argument("--format", choices=("text", "json", "mermaid"), default="text")

    gr = sub.add_parser("graph", help="print the dependency graph, optionally with an impact")
    _add_change_args(gr)
    _add_color(gr)
    gr.add_argument("--format", choices=("text", "mermaid", "json"), default="text")

    chg = sub.add_parser("changed", help="show what differs locally, and against what")
    _add_common(chg)
    chg.add_argument("--head", default="HEAD")
    chg.add_argument("--against", default="origin-branch", metavar="REF")
    chg.add_argument("--no-working", action="store_true")
    chg.add_argument("--format", choices=("text", "json"), default="text")

    check = sub.add_parser("check-title", help="validate a title")
    _add_common(check)
    check.add_argument("title", nargs="?")
    check.add_argument("--body")
    check.add_argument("--file", help="read the first line of this file (commit-msg hook)")
    check.add_argument("--no-ticket", action="store_true", help="do not require a ticket ID")

    validate = sub.add_parser("validate-config", help="lint pipeline.toml")
    _add_common(validate)

    for name, text in (
        ("init", "write the workflows, hooks and starter config into this repository"),
        ("update", "refresh the scaffolded files after upgrading picard (same as init)"),
    ):
        scaffold_cmd = sub.add_parser(name, help=text)
        scaffold_cmd.add_argument("--root", default=".", help="the repository root (default: .)")
        scaffold_cmd.add_argument(
            "--dry-run", action="store_true", help="only list what would change"
        )
    hooks_cmd = sub.add_parser("install-hooks", help="point this clone's git at the picard hooks")
    hooks_cmd.add_argument("--root", default=".", help="the repository root (default: .)")
    doctor = sub.add_parser(
        "doctor", help="check that what the configured impact engine needs is installed"
    )
    _add_common(doctor)
    doctor.add_argument(
        "--how-to-install",
        action="store_true",
        help="print install steps for whatever is missing",
    )
    setup = sub.add_parser("setup", help="prepare the repository for the configured impact engine")
    _add_common(setup)
    setup.add_argument(
        "--check", action="store_true", help="only verify the setup is complete (exit 2 if not)"
    )
    return parser


# ── where the change comes from ────────────────────────────────────────────────────────


def _env(name: str) -> str:
    """PICARD_<name>, falling back to the SMARTPLAN_<name> this tool used before it was renamed."""
    return os.environ.get(f"PICARD_{name}") or os.environ.get(f"SMARTPLAN_{name}", "")


def _titles_and_body(args: argparse.Namespace) -> tuple[list[str], str]:
    titles = list(args.title or [])
    if not titles and _env("TITLE"):
        titles = [_env("TITLE")]
    body = args.body if args.body is not None else _env("BODY")
    return titles, body


def _read_file_list(source: str) -> list[str]:
    raw = sys.stdin.buffer.read() if source == "-" else Path(source).read_bytes()
    text = raw.decode("utf-8", errors="surrogateescape")
    separator = "\0" if "\0" in text else "\n"
    return graph.split_files([part for part in text.split(separator) if part], commas=False)


def _has_source(args: argparse.Namespace) -> bool:
    return bool(
        args.files or args.files_from or args.staged or args.working or args.base or args.local
    )


def _explicit_files(args: argparse.Namespace) -> list[str] | None:
    """Changed files given directly (simulation, index, working tree), or None for a diff."""
    if args.files:
        return graph.split_files(args.files)
    if args.files_from:
        return _read_file_list(args.files_from)
    if args.staged:
        return gitutil.staged_files()
    if args.working:
        return gitutil.working_files()
    return None


@dataclass(frozen=True)
class Change:
    files: list[str]
    titles: list[str]
    body: str
    paths_only: bool  # no title: the plan follows the paths alone
    source: str  # human description of where the files came from


def _change(args: argparse.Namespace, need_diff: bool) -> Change:
    """The change described by the arguments.

    A change given as files, the index or the working tree has no commit yet. Without a
    --title the plan then follows the paths alone (paths_only), so a simulation shows what
    the files demand and a title can only raise it.
    """
    titles, body = _titles_and_body(args)
    files = _explicit_files(args)
    if files is not None:
        if args.files:
            source = "files given on the command line"
        elif args.files_from:
            source = f"file list from {'stdin' if args.files_from == '-' else args.files_from}"
        else:
            source = "the git index" if args.staged else "uncommitted work"
        return Change(files, titles, body, not titles, source)
    if not need_diff:
        return Change([], titles, body, False, "")
    if args.local:
        diff = gitutil.local_diff(args.against, args.head, include_working=not args.no_working)
        titles = titles or list(diff.subjects)
        label = gitutil.current_branch() or "HEAD"
        return Change(diff.files, titles, body, not titles, diff.describe(label))
    if args.base:
        source = f"{args.base}...{args.head}"
        return Change(gitutil.changed_files(args.base, args.head), titles, body, False, source)
    raise cfgmod.ConfigError(
        "pass what changed: --files a b c, --staged, --working, --base REF or --local"
    )


def _resolve(args: argparse.Namespace, cfg: cfgmod.Config) -> dict:
    tier = cfg.tiers.get(args.tier)
    if tier is None:
        raise cfgmod.ConfigError(f"tier {args.tier} is not defined in {args.config}")
    change = _change(args, need_diff=tier.compare != "none")
    return build_plan(
        cfg,
        args.tier,
        titles=change.titles,
        body=change.body,
        changed_files=change.files,
        paths_only=change.paths_only,
    )


def _use_color(args: argparse.Namespace) -> bool:
    if args.color == "always":
        return True
    return args.color == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ


# ── plan / run ─────────────────────────────────────────────────────────────────────────


def _write_outputs(path: str, plan: dict) -> None:
    matrix = {"include": plan["suites"]}
    values = {
        "plan": json.dumps(plan, separators=(",", ":")),
        "skip": str(plan["skip"]).lower(),
        "profile": plan["profile"] or "",
        "release": plan["release"] or "",
        "matrix": json.dumps(matrix, separators=(",", ":")),
        "has_suites": str(bool(plan["suites"])).lower(),
        "extras": json.dumps(plan["extras"], separators=(",", ":")),
        "runtime": json.dumps(plan["runtime"], separators=(",", ":")),
    }
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def _cmd_plan(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    plan = _resolve(args, cfg)
    print(json.dumps(plan, indent=2) if args.format == "json" else render_text(plan))
    if args.github_output:
        _write_outputs(args.github_output, plan)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(plan))
    return 0


def _execute(plan: dict, *, dry_run: bool) -> int:
    for suite in plan["suites"]:
        print(f"\n== {suite['id']}: {suite['command']}", file=sys.stderr)
        if dry_run:
            continue
        # The command comes from the repo's own pipeline.toml, the same trust as its justfile.
        code = subprocess.run(suite["command"], shell=True, check=False).returncode  # noqa: S602
        if code != 0:
            print(f"picard: {suite['id']} failed with exit code {code}", file=sys.stderr)
            return code
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    plan = _resolve(args, cfg)
    print(render_text(plan), file=sys.stderr)
    return _execute(plan, dry_run=args.dry_run)


# ── simulate / graph ───────────────────────────────────────────────────────────────────


def _parse_tiers(value: str, cfg: cfgmod.Config) -> list[int]:
    try:
        tiers = sorted({int(t) for t in value.split(",") if t.strip()})
    except ValueError as exc:
        raise cfgmod.ConfigError(f"--tiers must be numbers separated by commas: {value!r}") from exc
    unknown = [t for t in tiers if t not in cfg.tiers]
    if unknown:
        raise cfgmod.ConfigError(f"unknown tier(s): {', '.join(map(str, unknown))}")
    return tiers


def _simulation_change(args: argparse.Namespace) -> Change:
    """Like _change, but a bare `simulate` means "the current branch"."""
    if not _has_source(args):
        args.local = True
    return _change(args, need_diff=True)


PACKAGE_COLUMN = 54


def _package_cell(names: list[str], total: int) -> str:
    """The packages a tier runs, shortened to fit the table column."""
    if not names:
        return "none"
    if len(names) == total and total > 3:
        return f"all {total} packages"
    text = ", ".join(names)
    if len(text) <= PACKAGE_COLUMN:
        return text
    shown: list[str] = []
    for name in names:
        if len(", ".join([*shown, name])) > PACKAGE_COLUMN - 12:
            break
        shown.append(name)
    return f"{', '.join(shown)} (+{len(names) - len(shown)} more)"


def _tier_row(plan: dict, total: int) -> tuple[str, str, str, str]:
    if plan["skip"]:
        return plan["name"], f"skipped: {plan['skip_reason']}", "", ""
    packages = _package_cell(plan["packages"], total)
    suites = len(plan["suites"])
    extras = ", ".join(plan["extras"])
    return (
        plan["name"],
        packages,
        plan["markers"] or "(all)",
        f"{suites} suite(s)  {extras}".strip(),
    )


def _render_simulation(cfg, change, plans, impact, *, color, commands) -> str:
    files = change.files
    out = ["SIMULATION", ""]
    if change.source:
        out += [f"changes: {change.source}", ""]
    out.append(f"changed files ({len(files)}):")
    out += [f"  {f}" for f in files] or ["  (none)"]
    first = next((p for p in plans.values() if p["profile"]), None)
    if change.paths_only:
        title = "(none given: the plan follows the changed paths alone)"
    elif len(change.titles) == 1:
        title = change.titles[0]
    else:
        title = f"{len(change.titles)} commits, the strongest wins"
    verdict = f"profile {first['profile']}, release {first['release']}" if first else "n/a"
    out += ["", f"title: {title}", f"verdict: {verdict}"]
    out += ["", "DEPENDENCY GRAPH", graph.render_tree(cfg, impact, color=color), ""]
    why = graph.render_why(impact) or ["no package is affected through the code graph"]
    out += ["WHY", *[f"  {line}" for line in why], ""]
    shown = next((p for p in plans.values() if p["rules"] or p["review"] or p["unmapped"]), None)
    if shown:
        out.append("RULES")
        for rule in shown["rules"]:
            sample = ", ".join(rule["sample"][:2]) + (" ..." if rule["files"] > 2 else "")
            out.append(f"  {rule['name']}: {rule['files']} file(s) ({sample})")
            out.append(f"      -> {', '.join(rule['actions'])}")
        for review in shown["review"]:
            out.append(f"  REVIEW REQUIRED: {review['rule']} ({review['files']} file(s))")
        out.append("")
    out.append("TIERS")
    header = f"  {'tier':<5}{'name':<14}{'packages':<56}{'markers':<22}work"
    out.append(header)
    for number, plan in plans.items():
        name, packages, markers, work = _tier_row(plan, len(cfg.packages))
        out.append(f"  {number:<5}{name:<14}{packages:<56}{markers:<22}{work}")
    warnings = list(dict.fromkeys(w for plan in plans.values() for w in plan["warnings"]))
    out += [f"  ! {w}" for w in warnings]
    if commands:
        for number, plan in plans.items():
            if plan["suites"]:
                out += ["", f"tier {number} ({plan['name']}) commands:"]
                width = max(28, *(len(s["id"]) + 2 for s in plan["suites"]))
                out += [f"  {s['id']:<{width}}{s['command']}" for s in plan["suites"]]
    return "\n".join(out)


def _cmd_simulate(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    tiers = _parse_tiers(args.tiers, cfg)
    change = _simulation_change(args)
    plans = {
        n: build_plan(
            cfg,
            n,
            titles=change.titles,
            body=change.body,
            changed_files=change.files,
            paths_only=change.paths_only,
        )
        for n in tiers
    }
    impact = impact_of(cfg, change.files)

    if args.format == "json":
        data = {"source": change.source, "files": change.files, "titles": change.titles}
        print(json.dumps({**data, "plans": plans}, indent=2))
    elif args.format == "mermaid":
        print(graph.render_mermaid(cfg, impact))
    else:
        print(
            _render_simulation(
                cfg, change, plans, impact, color=_use_color(args), commands=args.commands
            )
        )
    if not args.run:
        return 0
    for number, plan in plans.items():
        print(f"\n▶ tier {number} ({plan['name']})", file=sys.stderr)
        code = _execute(plan, dry_run=False)
        if code != 0:
            return code
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    impact = None
    if _has_source(args):
        impact = impact_of(cfg, _change(args, need_diff=True).files)
    if args.format == "json":
        data = {
            "packages": {
                n: {"path": p.path, "depends_on": list(p.depends_on), "provider": p.provider}
                for n, p in cfg.packages.items()
            }
        }
        if impact:
            data["impact"] = {
                "changed": list(impact.changed),
                "affected": {k: list(v) for k, v in impact.affected.items()},
            }
        print(json.dumps(data, indent=2))
    elif args.format == "mermaid":
        print(graph.render_mermaid(cfg, impact))
    else:
        print(graph.render_tree(cfg, impact, color=_use_color(args)))
        if impact:
            print("\n" + "\n".join(graph.render_why(impact)))
    return 0


def _cmd_changed(args: argparse.Namespace) -> int:
    diff = gitutil.local_diff(args.against, args.head, include_working=not args.no_working)
    label = gitutil.current_branch() or "HEAD"
    if args.format == "json":
        data = {
            "branch": label,
            "base_ref": diff.base_ref,
            "how": diff.how,
            "base_sha": diff.base_sha,
            "commits": list(diff.subjects),
            "committed": list(diff.committed),
            "uncommitted": list(diff.uncommitted),
            "files": diff.files,
        }
        print(json.dumps(data, indent=2))
        return 0
    print(f"{label} against {diff.base_ref}  ({diff.how})")
    print(f"merge base: {diff.base_sha[:10]}")
    sections = (
        ("commits", diff.subjects),
        ("committed files", diff.committed),
        ("uncommitted files (staged, unstaged, untracked)", diff.uncommitted),
    )
    for title, items in sections:
        print(f"\n{title} ({len(items)}):")
        print("\n".join(f"  {item}" for item in items) or "  (none)")
    return 0


# ── titles / config ────────────────────────────────────────────────────────────────────


def _cmd_check_title(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    title = args.title
    if args.file:
        path = Path(args.file)
        title = path.read_text(encoding="utf-8").splitlines()[0] if path.stat().st_size else ""
    if title is None:
        title = _env("TITLE")
    body = args.body if args.body is not None else _env("BODY")
    commit = cc.parse_title(
        title,
        types=cfg.types,
        require_ticket=cfg.require_ticket and not args.no_ticket,
        ticket_exempt=cfg.ticket_exempt,
        body=body,
    )
    if commit.valid:
        flag = " (breaking)" if commit.breaking else ""
        print(f"ok: {commit.type}{flag}: {commit.subject}")
        return 0
    print(f"invalid title: {commit.raw!r}", file=sys.stderr)
    for error in commit.errors:
        print(f"  - {error}", file=sys.stderr)
    return 1


def _cmd_validate(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    by_provider: dict[str, int] = {}
    for package in cfg.packages.values():
        by_provider[package.provider] = by_provider.get(package.provider, 0) + 1
    sources = ", ".join(f"{k}: {v}" for k, v in sorted(by_provider.items()))
    print(
        f"ok: {len(cfg.packages)} package(s) ({sources}), {len(cfg.profiles)} profile(s), "
        f"tiers {sorted(cfg.tiers)}"
    )
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    engine = cfg.registries.engines.get(cfg.impact.engine)(cfg.impact.options, cfg.registries)
    engine.setup(cfg, check=args.check, say=lambda message: print(f"picard: {message}"))
    return 0


def _cmd_scaffold(args: argparse.Namespace) -> int:
    from picard import scaffold

    root = Path(args.root).resolve()
    results = scaffold.scaffold(root, dry_run=args.dry_run, say=lambda m: print(f"picard: {m}"))
    changed = sum(r.status in ("created", "updated", "removed") for r in results)
    print(
        f"picard: {len(results)} file(s) checked, {changed} {'to change' if args.dry_run else 'changed'}"
    )
    if not args.dry_run and any(r.status == "created" for r in results):
        print(
            "picard: next: edit .picard/pipeline.toml and run `picard validate-config`, "
            "run `picard install-hooks`, then set the repository variables PICARD_REPOSITORY "
            "and PICARD_REF and the secret PICARD_TOKEN that the workflows use "
            "(see docs/adoption.md in the picard repository)"
        )
    return 0


def _cmd_install_hooks(args: argparse.Namespace) -> int:
    from picard import scaffold

    scaffold.install_hooks(Path(args.root).resolve(), say=lambda m: print(f"picard: {m}"))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    engine = cfg.registries.engines.get(cfg.impact.engine)(cfg.impact.options, cfg.registries)
    problem = engine.problem(cfg)
    if problem is None:
        print(f"picard: ok: the '{engine.name}' impact engine is ready")
    else:
        print(f"picard: warning: {problem}", file=sys.stderr)
    if args.how_to_install:
        print(engine.install_help())
    elif problem is not None:
        print("picard: for install steps run: picard doctor --how-to-install", file=sys.stderr)
    return 0 if problem is None else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "debug", False):
        os.environ["PICARD_DEBUG"] = "1"  # read by the impact engines, no plumbing through
    handlers = {
        "plan": _cmd_plan,
        "run": _cmd_run,
        "simulate": _cmd_simulate,
        "graph": _cmd_graph,
        "changed": _cmd_changed,
        "check-title": _cmd_check_title,
        "validate-config": _cmd_validate,
        "setup": _cmd_setup,
        "doctor": _cmd_doctor,
        "init": _cmd_scaffold,
        "update": _cmd_scaffold,
        "install-hooks": _cmd_install_hooks,
    }
    try:
        return handlers[args.cmd](args)
    except (
        cfgmod.ConfigError,
        gitutil.GitError,
        graph.DiscoveryError,
        SetupError,
        ScaffoldError,
    ) as exc:
        print(f"picard: {exc}", file=sys.stderr)
        return 2
