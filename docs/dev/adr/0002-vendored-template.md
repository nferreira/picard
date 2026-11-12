# 0002 · Vendored tooling in `.picard/` and `.github/`

Status: superseded by [0006](0006-picard-is-a-python-package.md)

## Context

This repo is a template for new repositories, so every path at its root is copied into repos
we do not control. Those repos already use `src/`, `tests/`, `docs/`, `scripts/`, `tools/`,
`pyproject.toml` and `justfile`. The alternative to copying is referencing reusable workflows
from a central repo pinned to a tag.

## Decision

Vendor the tooling. Claim two directories only:

- `.github/` for everything GitHub reads, with every file we add prefixed `pipeline-`.
- `.picard/` for the planner, config, hooks, tests, docs and dev tooling, including its own
  `pyproject.toml` and `justfile` so nothing leaks to the root.

Provide `.picard/bin/update` to pull newer tooling and to install into an existing repo.

## Consequences

- No collisions with host repos, and nothing to install: `python3 picard`.
- A repo is self-contained and tooling changes are reviewed in that repo.
- The cost is drift: a repo runs the version it last updated to, and a fix needs an update PR
  per repo. Mitigations: tag the template, track the version, and run `bin/update` on a
  schedule if drift becomes a problem. If central rollout matters more than self-containment,
  the workflows can later be turned into reusable workflows without changing the planner.
- A repo owns its copy, so a local edit to tooling files is overwritten by the next update.
  Local policy belongs in `pipeline.toml`, which the updater never replaces.
