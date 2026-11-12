# Tiers

A tier is a moment in a change's life with its own cost and confidence budget. Each one is a
`[tiers.N]` table in `pipeline.toml`, so the table below is configuration, not code.

picard is an [affected-based](../dev/concepts.md) pipeline, and the tiers are where that narrows and
widens. Tiers 0 to 2 are **incremental**: they run the packages the change affects (`affected`,
`baseline`), found with the [dependency graph and impact engine](../dev/architecture.md#impact-and-engines).
Tiers 3 and 4 run **everything** and ignore the diff, as the safety net for what an incremental
run can miss.

| Tier | Name | Trigger | `compare` | `packages` | `markers` | `run` |
|---|---|---|---|---|---|---|
| 0 | pre-push | `git push` hook | `merge-base` | `affected` | `unit` | lint, test |
| 1 | pr-fast-gate | `pull_request` | `pr-base` | `affected` | `unit or integration` | lint, test |
| 2 | pr-baseline | `pull_request`, after tier 1 | `pr-base` | `baseline` | `baseline` | test |
| 3 | merge-queue | `merge_group` | `none` | `all` | `not slow` | lint, test |
| 4 | nightly | `schedule` | `none` | `all` | *(empty: all)* | test |
| 5 | release | push to `main` | n/a | n/a | n/a | Release Please |

Fields:

- **compare**: `merge-base` diffs against `git merge-base HEAD origin/main` (local),
  `pr-base` diffs against the PR base commit, `none` ignores the diff and the title.
- **packages**: `affected` is the packages whose files changed plus everything that depends
  on them. `baseline` is affected + packages pulled in by `[[path_rules]]` + packages with
  `baseline = true`. `all` is every package.
- **markers**: substituted into `{markers}` in package commands. For pytest that is
  `-m "{markers}"`. An empty value means no filter.
- **run**: which names from a package's `[packages.commands]` execute at this tier.

Rule actions follow the tiers differently from package suites: `run:` actions honour their
own `tiers`, while `own`, `package` and `full` select packages and so follow the tier's `run`
list and the profile. See [rules](rules.md#which-tiers-an-action-honours).

## Tier 0 · Pre-push

`.picard/hooks/pre-push` runs `picard run --tier 0 --local`: the unit tests of the packages
your branch touched, and of everything that depends on them. It considers everything that differs
locally (commits since the branch yours grew out of, plus uncommitted work), and the commit
subjects pick the profile. See [simulation](simulation.md).
It catches the obvious in seconds. It is a convenience, not a gate: `--no-verify` bypasses
it, and CI re-checks everything.

`.picard/hooks/commit-msg` validates each commit message as a Conventional Commit (without the
ticket ID rule, which applies to the PR title).

## Tier 1 · PR fast gate

Runs the suites of the affected packages with `unit or integration` markers, in parallel as a
matrix, plus the extras the profile enables (secret scan, workflow lint, dependency review).
Docs-only and light profiles stop here.

## Tier 2 · PR baseline

Runs only after tier 1 is green. Selects the **must-pass list**:

```toml
[[packages]]
name = "api"
baseline = true          # always part of the baseline

[[path_rules]]
globs = ["db/migrations/**"]
packages = ["worker"]    # touching migrations also runs the worker's baseline
```

and runs only tests carrying the `baseline` marker. Use it for the checks that must hold for
any change touching code: contract tests, migration checks, smoke tests, the critical path.
Profiles without tier 2 in `tiers` (docs, light) skip it, and a breaking change forces it.

## Tier 3 · Merge queue

The merge queue builds a temporary branch of main plus the queued PRs and runs `merge_group`
checks on it. Tier 3 runs **every package** with `not slow`, ignoring the diff and the title,
because what matters is how this PR behaves together with the ones ahead of it. It is the
last gate before main changes.

Requirements: enable the merge queue in branch protection and require the check `merge-gate`.
`pipeline-merge-queue.yml` already reports it on `merge_group`.

## Tier 4 · Nightly

Everything, slow tests included, plus deep scans (`secrets`, `semgrep`). A failure opens or
updates one tracking issue. Put the tests too slow or too flaky for a PR here
(`@pytest.mark.slow`).

## Tier 5 · Release

Release Please keeps a rolling Release PR built from the Conventional Commit titles merged
since the last tag. Merging it bumps the version, writes the changelog and creates the tag
and GitHub Release. See [conventional-commits.md](conventional-commits.md#release-effect).

Pre-production: the **release candidate** workflow (`pipeline-rc-tag.yml`) pushes `vX.Y.Z-rc.N` on the open Release
PR so a tag-driven pre-production deploy can start before the release is merged. Release
Please never creates that tag itself.

## The marker prerequisite

Tiers select tests by marker, so a test with no marker is invisible to tiers 0 to 3. Adopting
a repo with thousands of tests and a handful of markers means marking them first.
`.picard/templates/pytest/conftest.py` does it without touching each test (copy it to your
test root):

- **by directory**: `tests/unit/**` is `unit`, `tests/integration/**` is `integration`,
  `tests/e2e/**` is `integration` + `slow`.
- **by fixture**: a test using `db`, `postgres` or `redis` is `integration`.
- **enforcement**: `PICARD_REQUIRE_MARKERS=1` fails collection and lists unmarked tests.
  Turn it on once the backlog is marked.

`baseline` is the one marker you assign by hand, because it is a judgement about which tests
must always pass.

## Why one gate check

Required checks plus workflow-level `paths:` filters deadlock: a skipped workflow never
reports its required check and the PR waits forever. Here every job always exists, the plan
decides whether it has work, and a single `pipeline-gate` job (tiers 1 and 2) or
`merge-gate` (tier 3) turns the whole run into one pass or fail. A job the plan said must run
that did not succeed fails the gate, so a silent skip cannot go green.
