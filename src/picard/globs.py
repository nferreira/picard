"""Path globbing with `**` support (fnmatch has none)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "{":
            # {name} captures one path segment, reusable by an action: config/{handler}/**
            end = pattern.index("}", i)
            name = pattern[i + 1 : end]
            if not name.isidentifier():
                raise ValueError(f"bad capture {{{name}}} in {pattern!r}")
            out.append(f"(?P<{name}>[^/]+)")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(path: str, patterns: Iterable[str]) -> bool:
    """True when `path` (POSIX, repo-relative) matches any pattern."""
    return any(_compile(p).match(path) for p in patterns)


def captures(pattern: str) -> tuple[str, ...]:
    """Capture names a pattern defines, in order: `config/{handler}/**` -> ("handler",)."""
    return tuple(_compile(pattern).groupindex)


def match_captures(path: str, patterns: Iterable[str]) -> dict[str, str] | None:
    """Captures of the first pattern that matches `path`, {} if it has none, None if no match."""
    for pattern in patterns:
        found = _compile(pattern).match(path)
        if found:
            return found.groupdict()
    return None
