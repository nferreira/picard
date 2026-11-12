"""Pull requests as a source of changes, read through the GitHub CLI.

`--pr` plans pull requests, merged, closed or still open, as CI would: their files, title, body
and labels. With no number it lists the recent ones and asks which, one or several. Nothing is
checked out and nothing is run. Arguments are always passed as a list, never through a shell.
This is the one module that runs `gh`; picard itself needs only git, and without `gh` the
option stops and says how to describe the change by hand.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

GH_TIMEOUT = 60
TITLE_WIDTH = 60

_SHA = re.compile(r"^[0-9a-f]{40}$")
_DESCRIBE_BY_HAND = "or describe the change yourself with --files, --title and --label"


class PrError(RuntimeError):
    pass


@dataclass(frozen=True)
class ListedPr:
    """One line of the list: a pull request in any state."""

    number: int
    title: str
    state: str  # OPEN, MERGED or CLOSED
    date: str  # when it was merged, else closed, else opened: ISO 8601, empty when unknown
    head_ref: str = ""
    author: str = ""
    draft: bool = False

    @property
    def day(self) -> str:
        return self.date[:10]


@dataclass(frozen=True)
class PrChange:
    number: int
    title: str
    body: str
    files: list[str]
    labels: tuple[str, ...] = ()
    state: str = ""
    # The pull request's two commits, when the platform gave them. They let a single pull request
    # use a local git range (and so narrow a lockfile change); the file list above stays the truth.
    base_sha: str = ""
    head_sha: str = ""


def _gh(*args: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise PrError(
            f"the GitHub CLI (gh) is not installed: https://cli.github.com, {_DESCRIBE_BY_HAND}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PrError(f"gh took longer than {GH_TIMEOUT}s to answer") from exc
    except subprocess.CalledProcessError as exc:
        reason = (exc.stderr or exc.stdout or "").strip().splitlines()
        raise PrError(
            f"gh {' '.join(args[:2])} failed: {reason[0] if reason else f'exit {exc.returncode}'}"
        ) from exc
    return result.stdout


def _loads(text: str, what: str) -> object:
    try:
        return json.loads(text or "null")
    except ValueError as exc:
        raise PrError(f"gh answered with something picard cannot read ({what}): {exc}") from exc


def list_recent(limit: int = 30, base: str | None = None) -> list[ListedPr]:
    """The most recent pull requests of every state, newest activity first."""
    args = [
        "pr",
        "list",
        "--state",
        "all",
        "--limit",
        str(int(limit)),
        "--json",
        "number,title,state,isDraft,author,headRefName,mergedAt,closedAt,createdAt",
    ]
    if base:
        args += ["--base", base]
    rows = _loads(_gh(*args), "pr list") or []
    try:
        found = [
            ListedPr(
                number=int(row["number"]),
                title=str(row["title"]),
                state=str(row.get("state") or ("MERGED" if row.get("mergedAt") else "OPEN")),
                date=str(row.get("mergedAt") or row.get("closedAt") or row.get("createdAt") or ""),
                head_ref=str(row.get("headRefName") or ""),
                author=str((row.get("author") or {}).get("login", "")),
                draft=bool(row.get("isDraft", False)),
            )
            for row in rows  # type: ignore[attr-defined]
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PrError(f"gh answered with something picard cannot read (pr list): {exc}") from exc
    return sorted(found, key=lambda p: p.date, reverse=True)


def fetch(number: int) -> PrChange:
    """A PR's title, body, labels and every file it touched (all pages, a rename counts both)."""
    meta = _loads(
        _gh("pr", "view", str(number), "--json", "title,body,labels,state,baseRefOid,headRefOid"),
        f"pr view {number}",
    )
    if not isinstance(meta, dict) or "title" not in meta:
        raise PrError(f"gh did not return pull request #{number}")
    listing = _gh(
        "api",
        "--paginate",
        f"repos/{{owner}}/{{repo}}/pulls/{number}/files",
        "--jq",
        r".[] | .filename, (.previous_filename // empty)",
    )
    files = list(dict.fromkeys(line for line in listing.splitlines() if line))
    labels = tuple(str(item["name"]) for item in meta.get("labels") or [] if isinstance(item, dict))
    base, head = str(meta.get("baseRefOid") or ""), str(meta.get("headRefOid") or "")
    both = bool(_SHA.fullmatch(base) and _SHA.fullmatch(head))
    return PrChange(
        number=number,
        title=str(meta["title"]),
        body=str(meta.get("body") or ""),
        files=files,
        labels=labels,
        state=str(meta.get("state") or ""),
        base_sha=base if both else "",
        head_sha=head if both else "",
    )


def _shorten(text: str, width: int = TITLE_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def render_list(prs: Sequence[ListedPr], current_branch: str | None = None) -> str:
    """One line each: number, day, state, title and branch. A star marks the checked-out branch."""
    width = max((len(str(p.number)) for p in prs), default=1)
    lines = []
    for p in prs:
        mark = "★" if current_branch and p.head_ref == current_branch else " "
        flags = " (draft)" if p.draft else ""
        branch = f"  [{p.head_ref}]" if p.head_ref else ""
        lines.append(
            f"{mark} #{p.number:<{width}}  {p.day:<10}  {p.state.lower():<6}  "
            f"{_shorten(p.title)}{flags}{branch}"
        )
    return "\n".join(lines)


def current_pr(prs: Sequence[ListedPr], branch: str | None) -> int | None:
    """The newest listed pull request of the checked-out branch, if any."""
    if not branch:
        return None
    return next((p.number for p in prs if p.head_ref == branch), None)


def parse_selection(text: str, prs: Sequence[ListedPr], default: int | None = None) -> list[int]:
    """PR numbers typed as `707 699` or `707,#699`. `q` cancels; Enter takes the default, if any."""
    text = text.strip().lower()
    if text in ("q", "quit"):
        return []
    if text == "":
        return [default] if default is not None else []
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


def _ask(prompt: str) -> str:
    # The prompt goes to stderr so that structured output on stdout stays clean.
    print(prompt, end="", file=sys.stderr, flush=True)
    return input("")


def choose(
    limit: int = 30,
    base: str | None = None,
    *,
    current_branch: str | None = None,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] = lambda text: print(text, file=sys.stderr),
) -> list[int]:
    """List recent pull requests and ask which to plan. Returns the chosen numbers."""
    listed = list_recent(limit, base)
    if not listed:
        raise PrError("no pull requests found")
    default = current_pr(listed, current_branch)
    say(f"Pull requests, newest first ({len(listed)}):")
    say(render_list(listed, current_branch))
    question = "\nPR number(s) to simulate" + (f", Enter for #{default}" if default else "")
    try:
        answer = (ask or _ask)(f"{question}, blank or q to cancel: ")
    except EOFError:
        if not sys.stdin.isatty():
            raise PrError(
                "there is no terminal to choose from: name the pull requests, "
                f"for example: picard simulate --pr {listed[0].number}"
            ) from None
        answer = ""
    picked = parse_selection(answer, listed, default)
    if not picked:
        raise PrError("no pull request selected")
    return picked
