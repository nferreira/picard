"""Write the files picard ships for a repository: workflows, actions, hooks, a starter config.

`picard init` (first time) and `picard update` (after upgrading picard) run the same thing:

* **Tool-owned** files are replaced every time, so a repo follows the picard it runs:
  the `pipeline-*` workflows and actions, the git hooks and the pytest marker plugin.
* **Repo-owned** files are written only when missing and never overwritten, because the repo
  edits them: `pipeline.toml`, release-please config, CODEOWNERS, Dependabot, the PR template.
* Nothing else is touched: any workflow or action that is not named `pipeline-*`, and
  everything outside `.github/` and `.pipeline/`.

The templates are package data (`picard/templates/`), read with importlib.resources so this
works from a wheel as well as from a checkout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

TEMPLATES = files("picard") / "templates"

HOOKS_DIR = ".pipeline/hooks"


class ScaffoldError(RuntimeError):
    """The target is not somewhere picard can scaffold into."""


@dataclass(frozen=True)
class Entry:
    """One file to write: where, what, and whether the repo owns it afterwards."""

    path: str  # relative to the repository root, forward slashes
    data: bytes
    executable: bool
    repo_owned: bool


@dataclass(frozen=True)
class Result:
    path: str
    status: str  # created | updated | unchanged | kept | removed


def _read(node: Traversable) -> bytes:
    return node.read_bytes()


def _walk(node: Traversable, prefix: str) -> list[tuple[str, Traversable]]:
    found: list[tuple[str, Traversable]] = []
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        if child.name == "__pycache__":
            continue
        rel = f"{prefix}/{child.name}"
        found += _walk(child, rel) if child.is_dir() else [(rel, child)]
    return found


def entries() -> list[Entry]:
    """Every file the templates define, with its destination in a repository."""
    out: list[Entry] = []
    github = TEMPLATES / "github"
    for rel, node in _walk(github / "workflows", ".github/workflows"):
        out.append(Entry(rel, _read(node), False, False))
    for rel, node in _walk(github / "actions", ".github/actions"):
        out.append(Entry(rel, _read(node), False, False))
    for rel, node in _walk(github / "release-please", ".github/release-please"):
        out.append(Entry(rel, _read(node), False, True))
    for name in ("CODEOWNERS", "dependabot.yaml", "pull_request_template.md"):
        out.append(Entry(f".github/{name}", _read(github / name), False, True))
    for rel, node in _walk(TEMPLATES / "hooks", HOOKS_DIR):
        out.append(Entry(rel, _read(node), True, False))
    out.append(
        Entry(
            ".pipeline/templates/pytest/conftest.py",
            _read(TEMPLATES / "pytest" / "conftest.py"),
            False,
            False,
        )
    )
    out.append(Entry(".pipeline/pipeline.toml", _read(TEMPLATES / "pipeline.toml"), False, True))
    return out


def _stale(root: Path, wanted: set[str]) -> list[str]:
    """Files left in a tool-owned action directory that the templates no longer have."""
    stale: list[str] = []
    actions = root / ".github" / "actions"
    if actions.is_dir():
        for directory in sorted(actions.glob("pipeline-*")):
            for path in sorted(p for p in directory.rglob("*") if p.is_file()):
                rel = path.relative_to(root).as_posix()
                if rel not in wanted:
                    stale.append(rel)
    return stale


def scaffold(
    root: Path, *, dry_run: bool = False, say: Callable[[str], None] = print
) -> list[Result]:
    """Write the templates into `root`. With `dry_run` only report what would change."""
    if not (root / ".git").exists():
        raise ScaffoldError(
            f"{root} is not a git repository: run from the repo root or pass --root"
        )
    results: list[Result] = []
    wanted = set()
    for entry in entries():
        wanted.add(entry.path)
        target = root / entry.path
        if target.exists():
            if entry.repo_owned:
                status = "kept"
            elif target.read_bytes() == entry.data:
                status = "unchanged"
            else:
                status = "updated"
        else:
            status = "created"
        if status in ("created", "updated") and not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.data)
        if entry.executable and status != "kept" and not dry_run:
            target.chmod(0o755)
        results.append(Result(entry.path, status))
    for rel in _stale(root, wanted):
        if not dry_run:
            (root / rel).unlink()
        results.append(Result(rel, "removed"))
    for result in results:
        if result.status != "unchanged":
            say(
                f"{'would be ' if dry_run and result.status != 'kept' else ''}{result.status}: {result.path}"
            )
    return results


def install_hooks(root: Path, say: Callable[[str], None] = print) -> None:
    """Point this clone's git at the scaffolded hooks. Per clone, so each developer runs it."""
    hooks = root / HOOKS_DIR
    if not hooks.is_dir():
        raise ScaffoldError(f"{HOOKS_DIR} does not exist: run `picard init` first")
    for hook in hooks.iterdir():
        if hook.is_file() and not os.access(hook, os.X_OK):
            hook.chmod(0o755)
    if shutil.which("git") is None:
        raise ScaffoldError("git is not installed")
    subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "config", "core.hooksPath", HOOKS_DIR],  # noqa: S607
        check=True,
    )
    say(f"git hooks installed (core.hooksPath={HOOKS_DIR})")
