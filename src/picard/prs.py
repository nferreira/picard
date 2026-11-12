"""Merged pull requests as a source of changes, read through the GitHub CLI.

`picard simulate --pr` lists the recently merged PRs, lets you pick one or more, and plans
them as if they had just been opened: same files, same title, same body. Nothing is checked
out and nothing is run. Arguments are always passed as a list, never through a shell.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass


class PrError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergedPr:
    number: int
    title: str
    merged_at: str  # ISO 8601, empty when unknown
    base: str

    @property
    def day(self) -> str:
        return self.merged_at[:10]


@dataclass(frozen=True)
class PrChange:
    number: int
    title: str
    body: str
    files: list[str]


def _gh(*args: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PrError("the GitHub CLI (gh) is not installed: https://cli.github.com") from exc
    except subprocess.CalledProcessError as exc:
        raise PrError(f"gh {' '.join(args[:2])} failed: {exc.stderr.strip()}") from exc
    return result.stdout


def list_merged(limit: int = 30, base: str | None = None) -> list[MergedPr]:
    """The most recently merged PRs, newest first."""
    args = [
        "pr",
        "list",
        "--state",
        "merged",
        "--limit",
        str(limit),
        "--json",
        "number,title,mergedAt,baseRefName",
    ]
    if base:
        args += ["--base", base]
    rows = json.loads(_gh(*args) or "[]")
    prs = [
        MergedPr(r["number"], r["title"], r.get("mergedAt") or "", r.get("baseRefName") or "")
        for r in rows
    ]
    return sorted(prs, key=lambda p: p.merged_at, reverse=True)


def fetch(number: int) -> PrChange:
    """A PR's title, body and every file it touched (all pages, a rename counts both paths)."""
    meta = json.loads(_gh("pr", "view", str(number), "--json", "title,body"))
    listing = _gh(
        "api",
        "--paginate",
        f"repos/{{owner}}/{{repo}}/pulls/{number}/files",
        "--jq",
        r".[] | .filename, (.previous_filename // empty)",
    )
    files = list(dict.fromkeys(line for line in listing.splitlines() if line))
    return PrChange(number, meta["title"], meta.get("body") or "", files)


def render_list(prs: Sequence[MergedPr]) -> str:
    width = max((len(str(p.number)) for p in prs), default=1)
    return "\n".join(f"  #{p.number:<{width}}  {p.day:<10}  {p.title}" for p in prs)


def parse_selection(text: str, prs: Sequence[MergedPr]) -> list[int]:
    """PR numbers typed as `707 699` or `707,#699`. Empty or `q` means cancel."""
    text = text.strip().lower()
    if text in ("", "q", "quit"):
        return []
    known = {p.number for p in prs}
    picked: list[int] = []
    for token in text.replace(",", " ").split():
        try:
            number = int(token.lstrip("#"))
        except ValueError as exc:
            raise PrError(f"not a PR number: {token!r}") from exc
        if number not in known:
            raise PrError(f"#{number} is not in the list above (see --pr-limit)")
        if number not in picked:
            picked.append(number)
    return picked


def choose(
    limit: int = 30,
    base: str | None = None,
    *,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] = lambda text: print(text, file=sys.stderr),
) -> list[int]:
    """List the merged PRs and ask which to simulate. Returns the chosen numbers."""
    prs = list_merged(limit, base)
    if not prs:
        raise PrError("no merged pull requests found")
    say(f"Merged pull requests, newest first ({len(prs)}):")
    say(render_list(prs))
    try:
        picked = parse_selection(
            (ask or input)("\nPR number(s) to simulate (blank to cancel): "), prs
        )
    except EOFError:
        picked = []
    if not picked:
        raise PrError("no pull request selected")
    return picked
