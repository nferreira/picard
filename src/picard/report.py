"""The plan as a PR comment, and the metrics of a run.

A comment is posted where reviewers look, so it must not do anything a reviewer did not ask
for. The plan's text holds PR titles, rule names and paths, and GitHub turns `@name` in a
comment into a mention that notifies someone. Every `@` is followed by a zero-width space, so
the text reads the same and mentions nobody. The sticky marker the workflow uses to find its
own comment is not part of the body: the workflow adds it.

The metrics are what affected-based CI saved for one tier, as one small JSON object a team
can send to the observability it already has. picard builds no dashboard.
"""

from __future__ import annotations

import json
from typing import Any

from picard.planner import SCHEMA, render_markdown

ZERO_WIDTH_SPACE = "\u200b"
METRIC_KEYS = (
    "packages_total",
    "packages_selected",
    "suites_total",
    "suites_selected",
    "jobs",
    "selected_pct",
)


def render_comment(plan: dict[str, Any]) -> str:
    """The plan's markdown summary, with every `@` defused so it cannot mention anyone."""
    return render_markdown(plan).replace("@", "@" + ZERO_WIDTH_SPACE)


def metrics(plan: dict[str, Any]) -> dict[str, int]:
    """`{"schema", "tier", packages_*, suites_*, jobs, selected_pct}` for one tier's plan."""
    values = plan["metrics"]
    return {"schema": SCHEMA, "tier": plan["tier"], **{k: values[k] for k in METRIC_KEYS}}


def render_metrics(plan: dict[str, Any]) -> str:
    return json.dumps(metrics(plan), separators=(",", ":"))
