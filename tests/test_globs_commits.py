import re

import pytest

from picard.commits import parse_title
from picard.globs import matches

pytestmark = [pytest.mark.unit, pytest.mark.baseline]

TYPES = (
    "feat",
    "fix",
    "perf",
    "refactor",
    "docs",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
    "style",
)


@pytest.mark.parametrize(
    ("path", "patterns", "expected"),
    [
        ("README.md", ["**/*.md"], True),
        ("docs/a/b.md", ["**/*.md"], True),
        ("src/a.py", ["**/*.md"], False),
        ("docs/a/b.png", ["docs/**"], True),
        ("docsx/a.png", ["docs/**"], False),
        (".github/workflows/ci.yml", [".github/**"], True),
        ("a/b/test_x.py", ["**/test_*.py"], True),
        ("test_x.py", ["**/test_*.py"], True),
        ("src/x/y.py", ["src/*.py"], False),
        ("src/y.py", ["src/*.py"], True),
        ("uv.lock", ["pyproject.toml", "uv.lock"], True),
    ],
)
def test_glob_matching(path, patterns, expected):
    assert matches(path, patterns) is expected


def _parse(title, **kw):
    return parse_title(title, types=TYPES, **kw)


@pytest.mark.parametrize(
    ("title", "ctype", "scope", "breaking", "ticket"),
    [
        ("feat(auth): add token refresh (ABC-123)", "feat", "auth", False, "ABC-123"),
        ("fix: handle empty body (ABC-9)", "fix", None, False, "ABC-9"),
        ("feat(api)!: drop v1 (ABC-10)", "feat", "api", True, "ABC-10"),
        (
            "refactor(config): move settings into the database (ABC-103)",
            "refactor",
            "config",
            False,
            "ABC-103",
        ),
    ],
)
def test_valid_titles(title, ctype, scope, breaking, ticket):
    c = _parse(title)
    assert c.valid, c.errors
    assert (c.type, c.scope, c.breaking, c.ticket) == (ctype, scope, breaking, ticket)


@pytest.mark.parametrize(
    ("title", "fragment"),
    [
        ("add token refresh", "title must look like"),
        ("", "title must look like"),
        ("feature: add thing (ABC-1)", "unknown type"),
        ("Feat: add thing (ABC-1)", "unknown type"),
        ("feat: Add thing (ABC-1)", "lowercase"),
        ("feat: add thing", "missing ticket ID"),
        ("feat(gpu-01): add thing (ABC-1)", "looks like a ticket"),
    ],
)
def test_invalid_titles(title, fragment):
    c = _parse(title)
    assert not c.valid
    assert any(fragment in e for e in c.errors), c.errors


def test_ticket_not_required_when_disabled():
    assert _parse("feat: add thing", require_ticket=False).valid


def test_ticket_exempt_patterns():
    exempt = (re.compile(r"^chore: release "), re.compile(r"^chore\(deps\)"))
    assert _parse("chore: release 0.8.0", ticket_exempt=exempt).valid
    assert _parse("chore(deps): bump mcp from 1.29.0 to 1.30.0", ticket_exempt=exempt).valid
    assert not _parse("chore: tidy", ticket_exempt=exempt).valid


@pytest.mark.parametrize(
    "body", ["BREAKING CHANGE: removes /v1", "text\n\nBREAKING-CHANGE: removes /v1"]
)
def test_breaking_change_footer(body):
    assert _parse("feat: add thing (ABC-1)", body=body).breaking


def test_breaking_word_inside_body_text_is_not_a_footer():
    assert not _parse(
        "feat: add thing (ABC-1)", body="this is not a BREAKING CHANGE: really"
    ).breaking
