"""A pull request, read from the hosting platform, so a change can be simulated as CI sees it.

CI plans a pull request from four things: its title, its body, its labels and the commits it adds
to its base. Locally you have the commits but not the rest, so `picard simulate --pr` reads them
here. GitHub only, through the `gh` command line: picard itself needs nothing but git, and this
module is the one place that runs `gh`. Anything it cannot read is an error that says what to
pass by hand instead (`--title`, `--label`, `--base`), never a silent default.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

GH_TIMEOUT = 60

# A number, or the URL of a pull request. Anything else (an option, a branch name, a path) is
# refused before it can reach `gh` as an argument.
_SPEC = re.compile(r"^(\d+|https://[^\s/]+/[^\s/]+/[^\s/]+/pull/\d+)$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_FIELDS = "number,title,body,labels,baseRefName,baseRefOid,headRefName,headRefOid,url,state"

HOW_TO_PASS_BY_HAND = "or pass --title, --label and --base yourself"
LIST_LIMIT = 30
CHOICE_ATTEMPTS = 3
TITLE_WIDTH = 64


class PullRequestError(RuntimeError):
    """The pull request could not be read."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    url: str
    state: str


Runner = Callable[[Sequence[str]], str]


def _run_gh(args: Sequence[str]) -> str:
    try:
        done = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PullRequestError(
            f"--pr needs the GitHub CLI (gh): install it and run `gh auth login`, "
            f"{HOW_TO_PASS_BY_HAND}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PullRequestError(f"gh took longer than {GH_TIMEOUT}s to answer") from exc
    if done.returncode != 0:
        reason = (done.stderr or done.stdout).strip().splitlines()
        detail = reason[0] if reason else f"exit status {done.returncode}"
        raise PullRequestError(f"gh could not read the pull request: {detail}")
    return done.stdout


def _spec(spec: str) -> list[str]:
    if spec == "":
        return []  # the pull request of the checked-out branch
    if not _SPEC.fullmatch(spec):
        raise PullRequestError(
            f"--pr takes a pull request number or URL, not {spec!r} "
            "(without a value it means the pull request of the current branch)"
        )
    return [spec]


def fetch(spec: str = "", *, run: Runner = _run_gh) -> PullRequest:
    """The pull request named by `spec`: a number, a URL, or "" for the current branch's."""
    text = run(["pr", "view", *_spec(spec), "--json", _FIELDS])
    try:
        data = json.loads(text)
        labels = tuple(str(item["name"]) for item in data.get("labels", []))
        found = PullRequest(
            number=int(data["number"]),
            title=str(data["title"]),
            body=str(data.get("body") or ""),
            labels=labels,
            base_ref=str(data["baseRefName"]),
            base_sha=str(data["baseRefOid"]),
            head_ref=str(data["headRefName"]),
            head_sha=str(data["headRefOid"]),
            url=str(data.get("url", "")),
            state=str(data.get("state", "")),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise PullRequestError(f"gh answered with something picard cannot read: {exc}") from exc
    if not (_SHA.fullmatch(found.base_sha) and _SHA.fullmatch(found.head_sha)):
        raise PullRequestError("gh did not return the commits of the pull request")
    return found


def changed_paths(number: int, *, run: Runner = _run_gh) -> list[str]:
    """The files a pull request changes, as the platform lists them (no local commits needed)."""
    text = run(["pr", "diff", str(number), "--name-only"])
    return [line for line in text.splitlines() if line]


@dataclass(frozen=True)
class PullRequestSummary:
    """One line of the list of pull requests."""

    number: int
    title: str
    head_ref: str
    author: str
    draft: bool
    state: str = "OPEN"  # OPEN, MERGED or CLOSED


def list_recent(limit: int = LIST_LIMIT, *, run: Runner = _run_gh) -> list[PullRequestSummary]:
    """The most recent pull requests, open, merged and closed, newest first, as `gh` lists them."""
    text = run(
        [
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            str(int(limit)),
            "--json",
            "number,title,headRefName,author,isDraft,state",
        ]
    )
    try:
        return [
            PullRequestSummary(
                number=int(item["number"]),
                title=str(item["title"]),
                head_ref=str(item["headRefName"]),
                author=str((item.get("author") or {}).get("login", "")),
                draft=bool(item.get("isDraft", False)),
                state=str(item.get("state", "OPEN")),
            )
            for item in json.loads(text)
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise PullRequestError(f"gh answered with something picard cannot read: {exc}") from exc


def _shorten(text: str, width: int = TITLE_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def render_choices(items: Sequence[PullRequestSummary], current_branch: str | None) -> list[str]:
    """The numbered list, each with its state. The checked-out branch's one is starred."""
    lines = []
    for position, item in enumerate(items, start=1):
        mark = "★" if current_branch and item.head_ref == current_branch else " "
        flags = " (draft)" if item.draft else ""
        who = f"  @{item.author}" if item.author else ""
        lines.append(
            f"{mark} {position:>2}  #{item.number:<6} {item.state.lower():<6} "
            f"{_shorten(item.title)}{flags}  [{item.head_ref}]{who}"
        )
    return lines


def interactive() -> bool:
    """Whether there is a person to ask: a terminal on both input and the prompt's output."""
    return sys.stdin.isatty() and sys.stderr.isatty()


def _ask(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)  # stdout stays clean for --format json
    return input()


def choose(
    items: Sequence[PullRequestSummary],
    current_branch: str | None = None,
    *,
    ask: Callable[[str], str] = _ask,
    say: Callable[[str], None] | None = None,
) -> int:
    """Show the list and return the number of the pull request the person picks.

    Picks by position in the list. Enter takes the starred pull request (the checked-out
    branch's), `q` or end of input cancels, and anything else asks again, a few times.
    """
    say = say or (lambda line: print(line, file=sys.stderr))
    for line in render_choices(items, current_branch):
        say(line)
    default = next(
        (
            position
            for position, item in enumerate(items, start=1)
            if current_branch and item.head_ref == current_branch
        ),
        None,
    )
    hint = f"1-{len(items)}" + (f", Enter for {default}" if default else "") + ", q to quit"
    for _ in range(CHOICE_ATTEMPTS):
        try:
            answer = ask(f"Pull request to simulate [{hint}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise PullRequestError("no pull request selected") from None
        if answer.lower() in {"q", "quit"}:
            raise PullRequestError("no pull request selected")
        if answer == "" and default:
            return items[default - 1].number
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1].number
        say(f"not a choice: {answer!r}")
    raise PullRequestError("no pull request selected")
