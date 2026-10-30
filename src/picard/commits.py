"""Conventional Commit parsing and validation for PR titles and commit subjects.

The PR title is the unit that matters: squash merge makes it the commit on main, and
release-please derives the version from it. The rules are the ones the config
sets by default: lowercase subject, ticket ID in parentheses at the end, no ticket-like token in the scope.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_HEADER = re.compile(r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()]*)\))?(?P<bang>!)?: (?P<rest>\S.*)$")
_TICKET = re.compile(r"^(?P<subject>.*\S)\s+\((?P<ticket>[A-Z][A-Z0-9]*-\d+)\)$")
_TICKET_LIKE = re.compile(r"(?i)\b[a-z]+-\d+\b")
_BREAKING_FOOTER = re.compile(r"(?m)^BREAKING[ -]CHANGE: \S")


@dataclass(frozen=True)
class Commit:
    raw: str
    valid: bool
    type: str | None = None
    scope: str | None = None
    breaking: bool = False
    subject: str | None = None
    ticket: str | None = None
    errors: tuple[str, ...] = ()


def parse_title(
    title: str,
    *,
    types: Sequence[str],
    require_ticket: bool = True,
    ticket_exempt: Iterable[re.Pattern[str]] = (),
    body: str = "",
) -> Commit:
    """Parse one title. Never raises: problems are returned in `errors`."""
    line = title.strip().splitlines()[0] if title.strip() else ""
    breaking_footer = bool(_BREAKING_FOOTER.search(body or ""))
    match = _HEADER.match(line)
    if not match:
        return Commit(
            raw=line,
            valid=False,
            breaking=breaking_footer,
            errors=("title must look like 'type(scope): subject (TICKET-1)'",),
        )

    errors: list[str] = []
    ctype = match["type"]
    scope = match["scope"] or None
    rest = match["rest"]

    if ctype not in types:
        errors.append(f"unknown type '{ctype}', allowed: {', '.join(types)}")

    if scope is not None and _TICKET_LIKE.search(scope):
        errors.append(f"scope '{scope}' looks like a ticket ID, put the ticket at the end")

    ticket = None
    subject = rest
    ticket_match = _TICKET.match(rest)
    if ticket_match:
        subject = ticket_match["subject"]
        ticket = ticket_match["ticket"]

    if subject[0].isupper():
        errors.append("subject must start with a lowercase letter")

    exempt = any(p.search(line) for p in ticket_exempt)
    if require_ticket and ticket is None and not exempt:
        errors.append("missing ticket ID in parentheses at the end, for example (ABC-123)")

    return Commit(
        raw=line,
        valid=not errors,
        type=ctype,
        scope=scope,
        breaking=bool(match["bang"]) or breaking_footer,
        subject=subject,
        ticket=ticket,
        errors=tuple(errors),
    )
