# 0006 · picard is a Python package, not vendored tooling

Status: accepted. Supersedes [0002](0002-vendored-template.md).

## Context

ADR 0002 copied the whole tool into every host repo under `.picard/`, because the template
repository's root was copied into repos that already use `src/`, `tests/`, `docs/` and
`pyproject.toml`. That kept hosts self-contained, but picard grew into a real program (graph
providers, impact engines, executors, plugins, a Pants setup) with its own tests and docs, and
every host carried a copy of all of it. A fix needed an update PR per repo, and the tool's
own layout was bent around not colliding with a host.

## Decision

picard is a normal Python package in its own repository, with a `picard` command.

- **Layout.** `src/picard/`, `tests/`, `docs/`, `pyproject.toml`, `justfile`, and its own CI.
  No `.picard/` tree and no `bin/` scripts. Runtime dependencies stay empty (standard
  library only, Python 3.11+), because picard is a dependency of every repo's CI.
- **Installing.** A developer clones the picard repository and installs it once:
  `uv tool install --editable <clone>`. CI installs a pinned ref in a composite action
  (`pipeline-setup-picard`) that fetches the picard repository outside the workspace, so a
  host's own linters never scan it, and runs `pip install` on it. The repository, the ref and
  a read token are the repository variables `PICARD_REPOSITORY`, `PICARD_REF` and the secret
  `PICARD_TOKEN`. Prefer a full commit SHA for the ref.
- **Scaffolding.** The workflows, composite actions, git hooks, the pytest marker plugin and a
  starter `pipeline.toml` are package data (`src/picard/templates/`). `picard init` writes
  them into a host and `picard update` refreshes them after the host moves to a newer picard.
  Files picard owns are replaced. Files the repo owns (`pipeline.toml`, release-please config,
  CODEOWNERS, Dependabot, the PR template) are written only when missing and never
  overwritten. Nothing else is touched.
- **The host keeps two things:** its config at `.picard/pipeline.toml` and the scaffolded
  `pipeline-*` workflows and actions. Hosts own their workflow files, so a change to what CI
  does is still a reviewed diff in the host.
- **Hooks** call `picard` and say how to install it when it is missing.

## Consequences

- One copy of the tool. A fix is a release of picard and a pin bump per host, not a copy.
- A host runs the picard version it pinned, and its workflows change only when it runs
  `picard update`. `picard update --check` (not built yet) would catch drift.
- A private picard repository needs a token in CI, and fork PRs get no secrets. Keep picard
  readable for forks, or do not run them. The token only reaches the install step, never the
  steps that run the PR's code.
- Installing from a pinned git ref trusts that repository. Pin a SHA, and protect its branch.
- The reusable-workflow alternative from ADR 0002 is still open, and the planner would not
  change.
