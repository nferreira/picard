# 0001 · Plan-driven tiers

Status: accepted

## Context

CI for a repo usually grows as `if:` expressions and `paths:` filters across several workflow
files. That is hard to test, drifts between repos, and has a known failure: a required check
behind a `paths:` filter never reports, so the PR waits forever. We also want what a change
must prove to depend on what the change is, and Conventional Commit titles already say that.

## Decision

A tested planner turns (config, tier, PR title, changed files) into a JSON plan. Workflows
read the plan and execute it. Policy lives in `pipeline.toml`. Five tiers differ only by their
`[tiers.N]` table. One gate job per event converts the run into a single required check.

## Consequences

- Behaviour changes are config edits with unit tests, not workflow edits.
- A local run (`picard run --tier 0`) and CI follow the same rules.
- The planner is a dependency of every repo's CI, so it is small (standard library only),
  versioned and covered by tests.
- Every job exists on every run, so skips are visible in the run and checked by the gate.
