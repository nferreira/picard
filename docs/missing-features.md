# Missing features: picard against other affected-based CI tools

picard decides what CI runs for a change. This page lists what production-grade tools in the
same space do that picard does not, and says for each whether it makes sense to bring in
**without rebuilding what a tool already does**. It is a decision aid, not a commitment.

Surveyed on 2026-10-30 from each project's own documentation: Nx, Turborepo, Pants, Bazel
(with bazel-diff), moon, Jest, pytest-impacted, and the commercial test-selection services
(Develocity, Launchable, Datadog) and CI vendors (Buildkite, GitHub merge queue). Links are at
the end. What picard has today was checked against the code, not remembered.

## How to read the verdicts

| Verdict | Meaning |
|---|---|
| **Adopt** | A small piece of picard's own logic. No existing tool does this part, because it is the decision layer. |
| **Integrate** | Reuse a tool. picard writes only an adapter (an impact engine, an executor, an extra) or a recipe. |
| **Defer** | Real, but the cost or risk is not worth it yet. Revisit on a trigger. |
| **Skip** | Out of scope, vendor-specific, or a build system's job. |

Effort: **S** is under a day, **M** is a few days, **L** is weeks.

## What picard already matches

Not a gap, so not listed below.

- **Affected from the dependency graph**, transitively, at package level, with the graph read
  from the package managers' own manifests. Turborepo and moon work at about this granularity.
  Nx (since version 24, by default) selects at task level from each task's declared inputs, and
  Pants at target and file level from inferred imports, so both are finer than picard's native
  engine. Item 7 is that gap.
- **A reason for every decision**: `why` chains, `via <engine>` labels, a Mermaid graph and a
  `--debug` trace (Nx `--explain`, bazel-diff `explain`).
- **Local simulation** of a change that is not made yet (`--files`), of a branch (`--local`) and
  of the index (`--staged`), against the right parent branch.
- **Pluggable impact engines with a safe fallback**: Pants is an engine, and a failing or
  missing engine widens the answer and never narrows it.
- **Beyond the others:** the PR title picks how much to verify, and one gate job makes skipped
  required checks safe. None of the tools below does either. Rule actions that run a named
  command with values captured from the path and checked against an inventory are also not
  something they document. Selecting work for a file with no code is not unique: Nx named inputs
  and moon task inputs can include any file glob, so a dataset change can select one project's
  tasks there too.

## Summary

| # | Feature | Seen in | Verdict | Effort |
|---|---|---|---|---|
| 1 | Impact engines for Nx, Turborepo, moon and bazel-diff | Nx, Turborepo, moon, Bazel | **Integrate** (Nx fits today, the rest need base and head passed to engines) | S for Nx, M once for the rest |
| 2 | Lockfile-aware narrowing | Nx | **Adopt** | M |
| 3 | Test sharding by file or timing | Pants, Nx, Buildkite, Jest | **Integrate** | M |
| 4 | A first-class "list the affected packages" command | Turborepo, moon, Nx, Pants | **Adopt** | S |
| 5 | Shallow clone and unknown base handling | Turborepo, Nx | **Adopt** | S |
| 6 | Force a full run, mark a package unskippable | Datadog | **Adopt** | S |
| 7 | A test-only change does not propagate to dependents | Nx, Pants | **Adopt** | S to M |
| 8 | Executors that delegate to a build tool with a cache | Pants, Nx, Turborepo, Bazel | **Integrate** | M |
| 9 | Undeclared dependency detection | Nx (gotcha), Pants | **Integrate** | S to M |
| 10 | Flaky retry and quarantine | Nx, Buildkite, Develocity | **Integrate** | S |
| 11 | Test-level selection | Jest, pytest-impacted, Datadog, Develocity | **Integrate**, opt-in | M |
| 12 | Incremental merge queue tier | GitHub merge queue | **Defer** | M |
| 13 | Base = last green commit | Nx | **Defer** | S to M |
| 14 | Cross-package task ordering | Nx, Turborepo, Bazel, Pants | **Skip** (delegate, see 8) | L |
| 15 | Result caching and remote cache | Nx, Turborepo, Pants, Bazel | **Skip** (delegate, see 8) | L |
| 16 | Distributed execution | Nx, Bazel, Pants | **Skip** | L |
| 17 | Sticky PR plan comment, run metrics | Nx Cloud, Buildkite | **Adopt**, small | S to M |
| 18 | Per-package releases in a monorepo | Nx, changesets, release-please | **Defer** | M |
| 19 | Self-healing CI, flaky analytics, conformance, code owners | Nx Cloud | **Skip** | n/a |
| 20 | Module boundary enforcement | Nx, Turborepo | **Skip** (as an extra) | S |
| 21 | Daemon, watch mode, hash service | Pants, Nx, Turborepo, bazel-diff | **Skip** | M |

## The items

Every item has the same four parts: what the feature is, **Our solution**, and **How it will be
used**. In the examples, `# today` means it works now and `# planned` means the command or key
does not exist yet and is the proposed design. Output is illustrative unless it says it is real.
The configuration lives in `.picard/pipeline.toml`.

### 1. Impact engines for the tools people already run

Nx (`nx show projects --affected`), Turborepo (`turbo query affected`), moon
(`moon query projects --affected`) and bazel-diff (`get-impacted-targets`) each compute an
affected set with their own, more precise graph. Reusing them beats copying their graph logic,
but the interface matters, and only one of the four fits picard's engine as it is today:

| Tool | What it takes | Fits the current engine (a list of files)? |
|---|---|---|
| Nx | `--base`/`--head`, or `--files` when there is no git | Yes. It can answer `picard simulate --files`. |
| Turborepo | `--base` and `--head` git refs (or `TURBO_SCM_BASE` and `TURBO_SCM_HEAD`). No file list is documented. | No |
| moon | Changed files from the VCS (`git status`). The page does not document base, head or a file list. | No, as far as the docs say |
| bazel-diff | Two checked-out revisions, hashed separately. `--modified-filepaths` only speeds that up and must be a superset of what changed. | No |

**Our solution**

Verdict: **Integrate**. Effort: S for Nx, then M once for the rest.

1. Add an `nx` impact engine on the current interface, like the `pants` engine: it asks Nx for the
   affected projects, hands every file Nx does not own to the fallback, and widens the answer on
   any failure. `picard doctor` checks that Nx is installed.
2. Change the engine interface so an engine also receives the base and head when picard has
   them (`--base`, `--local`). Then add `turbo`, `moon` and `bazel-diff` engines that need them.
3. For `--files`, where no git change exists, a git-based engine cannot answer. The native engine
   answers instead and the plan carries a note, so `picard simulate --files` and CI can differ for
   a package, and `--debug` shows exactly where.

**How it will be used**

```toml
# .picard/pipeline.toml          # planned
[impact]
engine   = "nx"
fallback = "native"
```

```sh
picard doctor                                       # planned: ok: the 'nx' impact engine is ready
picard simulate --files libs/ui/src/button.ts       # planned: affected packages are marked `via nx`
picard plan --tier 1 --base origin/main --format text   # planned: the same engine in CI
```

For a git-based tool:

```toml
[impact]
engine = "turbo"                                    # planned
```

```sh
picard plan --tier 1 --base origin/main --head HEAD     # planned: asks `turbo query affected --base origin/main --head HEAD`
picard simulate --files apps/web/src/a.ts               # planned, illustrative output:
# ! turbo answers from git refs only: the native engine answered for --files
```

### 2. Lockfile-aware narrowing

Today any change to a lockfile affects every package. Nx narrows this with
`projectsAffectedByDependencyUpdates: "auto"`, so only the projects whose resolved dependencies
changed are affected. Without it every Dependabot bump runs everything.

**Our solution**

Verdict: **Adopt**. Effort: M. No tool provides this for picard, because it is the decision layer.

- When a lockfile changed, read its old version from the base (`git show <base>:<path>`) and the
  new one from the working tree, find the dependencies whose resolved version changed, and map
  them to the packages that depend on them. The lockfile already records who depends on what.
- picard is standard library only, so start with the lockfiles it can parse without a
  dependency: `uv.lock` and `Cargo.lock` (TOML) and `package-lock.json` (JSON).
  `pnpm-lock.yaml` and `yarn.lock` need a parser picard does not have, so they stay fail-closed
  and keep running everything.
- Fail closed everywhere: a lockfile that cannot be parsed, a change that cannot be attributed,
  or a lockfile not listed under `narrow` runs everything and says why.
- It needs the old content, so it works with `--base` and `--local`, and not with `--files`.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned
[paths]
global = [".picard/pipeline.toml", ".github/workflows/**"]   # lockfiles are no longer listed here

[lockfiles]
narrow = ["uv.lock", "Cargo.lock", "package-lock.json"]      # anything else stays "run everything"
```

```sh
# A Dependabot PR bumps one library          # planned, illustrative output
picard plan --tier 1 --base origin/main \
  --title "chore(deps): bump httpx from 0.27 to 0.28 (ABC-9)" --format text
#   packages: some-api, component-def
#   reason:   uv.lock changed httpx 0.27 -> 0.28, used by 2 of 7 packages

# A lockfile picard cannot parse
#   reason:   pnpm-lock.yaml changed and cannot be attributed to packages: running everything
```

### 3. Test sharding

One package is one job, so a big package is one long job. Pants has `--test-shard=k/N`, Nx splits
end-to-end tests by file (the atomizer), Buildkite splits by recorded timings, and Jest has
`--shard`.

**Our solution**

Verdict: **Integrate**. Effort: M. Already on the [roadmap](improvements.md).

- A `shards = N` key per package expands a suite into N matrix jobs. picard's plan is already a
  matrix, so this is a plan field and nothing else changes in the workflows.
- Each executor passes the tool's own flag: `pytest-split` for pytest, `--shard=k/N` for Jest
  and Vitest, `--test-shard` for Pants. picard never splits tests itself.
- Balance by recorded duration later, from the nightly run's timing file. The first version
  splits by the splitter's default.
- A local `picard run` runs the whole suite once, because shards exist to parallelise CI.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned
[[packages]]
name     = "some-api"
path     = "apps/some-api"
executor = "uv-pytest"
shards   = 4
```

```sh
picard simulate --files apps/some-api/src/some_api/handlers.py --commands   # planned, illustrative output:
# tier 1 (pr-fast-gate) commands:
#   some-api:test#1/4   uv run --no-sync pytest apps/some-api -m "unit or integration" --splits 4 --group 1
#   some-api:test#2/4   uv run --no-sync pytest apps/some-api -m "unit or integration" --splits 4 --group 2
#   ...
picard run --tier 1 --local            # runs the whole suite once, not four shards
```

### 4. A first-class "list what is affected" command

`turbo query affected`, `moon query projects --affected --json`, `nx show projects --affected`
and `pants dependents` let other steps ask the question: build only the affected images, deploy
only the affected apps, lint only the affected packages. picard has the data in
`plan --format json` and `graph --format json`, but no short command with a stable shape.

**Our solution**

Verdict: **Adopt**. Effort: S.

Add `picard affected` with the same change sources as `plan` (`--files`, `--base`, `--local`,
`--staged`) and a stable output: `names`, `paths`, `json`, and `matrix` (GitHub's
`{"include":[...]}` shape). The JSON shape is documented and versioned like the plan's. It
extends picard past tests: any step can use the answer.

**How it will be used**

```sh
picard affected --base origin/main --format names          # planned, illustrative output:
# component-abc
# component-def
# some-api

picard affected --files apps/landing-page/src/page.js --format paths     # planned
# apps/landing-page

picard affected --base origin/main --format matrix         # planned
# {"include":[{"name":"some-api","path":"apps/some-api"},{"name":"component-def","path":"packages/component-def"}]}
```

Build and deploy only what changed:

```yaml
# a workflow job        # planned
- id: affected
  env:
    BASE: ${{ github.event.pull_request.base.sha }}
  run: echo "apps=$(picard affected --base "$BASE" --format matrix)" >> "$GITHUB_OUTPUT"
# a second job:  strategy: { matrix: ${{ fromJSON(needs.affected.outputs.apps) }} }  -> docker build ${{ matrix.path }}
```

### 5. Shallow clones and an unknown base

Turborepo falls back to running all tasks on a shallow clone, and Nx needs full history. picard's
workflows check out full history, but run elsewhere (a shallow CI clone, a base that is not
fetched) it stops with a raw `git diff ... unknown revision` error and exit status 2. That is
safe, because the job fails, but it is not friendly. Checked on 2026-10-30 with a depth-1 clone.

**Our solution**

Verdict: **Adopt**. Effort: S.

Detect `git rev-parse --is-shallow-repository` and a base that is not reachable. Try to fetch
the base once (`git fetch --depth` on that commit). If that fails, follow `[git] unknown_base`:
`full` (default) runs everything with a clear warning, which fits fail-closed, and `fail`
keeps today's error for teams that prefer a red job.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned
[git]
unknown_base = "full"      # full (default) | fail
```

```sh
# Today (real output from a depth-1 clone):
picard plan --tier 1 --base c559629 --head HEAD --title "fix: x (ABC-1)"
# picard: git diff --name-only --no-renames -z c559629...HEAD failed: fatal: ambiguous argument ...   (exit 2)

# Planned, illustrative output:
# picard: warning: base c559629 is not in this shallow clone and could not be fetched
# picard: warning: running every package instead (set [git] unknown_base = "fail" to stop here)
```

### 6. Force a full run, mark something unskippable

Datadog lets a commit message or a label force a full run, and lets a test be marked unskippable
because coverage cannot see its inputs. picard lets a title raise the profile and has
`baseline = true` for tier 2, but has no way to say "run everything for this PR" or "this package
is never skipped".

**Our solution**

Verdict: **Adopt**. Effort: S.

- A PR label (default `ci:full`) is a plan input that makes every package affected. Labels can
  only raise the bar, as titles can.
- An `always = true` flag on a package keeps it in every PR's affected set for the tiers that
  select affected packages.
- Labels reach picard as an input mapped to an environment variable, never interpolated into a
  shell, like the title and body.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[labels]
full = "ci:full"           # the default; "" disables it

[[packages]]
name     = "db-migrations"
path     = "db"
always   = true            # in every PR's affected set
```

```sh
# today, real output (a scratch repo with four packages, a docs-only commit)
PICARD_TITLE="docs: fix a typo (ABC-1)" picard plan --tier 1 --base main --label ci:full --format text
# tier 1 (pr-fast-gate)
#   profile: full   release: none
#   packages: component-def, some-api, web, db-migrations
#   markers: unit or integration
#   impact: everything is affected
#   impact: label ci:full forces a full run
#   run: component-def:test: uv run --no-sync pytest packages/component-def -m "unit or integration"
#   run: some-api:test: uv run --no-sync pytest apps/some-api -m "unit or integration"
#   run: web:test: pnpm --filter web run test
#   run: db-migrations:test: make -C db check
#   extras: secrets
#   note: type 'docs' -> profile 'docs'
#   note: label ci:full forces a full run

# today, real output, excerpt with columns narrowed: preview what the label does (tier 2 runs too)
picard simulate --files README.md --label ci:full --tiers 1,2
# WHY
#   everything is affected
#   label ci:full forces a full run
#
# TIERS
#   tier name          packages          markers               work
#   1    pr-fast-gate  all 4 packages    unit or integration   4 suite(s)  secrets
#   2    pr-baseline   all 4 packages    baseline              4 suite(s)  semgrep, dependency-review

# today, real output, excerpt: without the label, the always package still runs
picard simulate --files apps/web/x.ts --tiers 1
# WHY
#   db-migrations is always affected (always = true)
#   web owns a changed file (via native)
```

Labels also come from `PICARD_LABELS` (a JSON array, or a comma-separated list) when no
`--label` is given, so a workflow passes them through `env:`.

```yaml
# in the plan step of pipeline-pr.yml      # planned
with:
  labels: ${{ join(github.event.pull_request.labels.*.name, ',') }}
```

### 7. A test-only change should not run the dependents

Under the native engine a change to a package's test files runs that package and everything that
depends on it. Nx has named inputs (a `production` set that excludes spec files, so a spec-only
change selects the package's tests but not its dependents), and Pants gets it for free because
nothing imports a test file. In the demo today (real output), changing
`packages/component-def/tests/unit/test_formatter.py` under the native engine runs
`component-def` **and** `some-api`, and under Pants only `component-def`.

**Our solution**

Verdict: **Adopt**. Effort: S to M.

Add `test_paths` globs to a package. A change that only touches those files affects the owning
package and not its dependents. A path that matches nothing keeps today's behaviour, and a
change that touches both test and source files affects dependents as usual. The Pants engine
already behaves this way for Python.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned
[[packages]]
name       = "component-def"
path       = "packages/component-def"
test_paths = ["tests/**", "**/test_*.py", "**/conftest.py"]
```

```sh
picard simulate --files packages/component-def/tests/unit/test_formatter.py
# today (native engine, real):      affected: component-def, some-api
# planned (native engine):          affected: component-def
#   WHY  component-def owns a changed file (test files only: dependents are not affected) (via native)
```

### 8. Executors that delegate to a tool with a cache

Result caching is the biggest speed-up in Nx, Turborepo, Pants and Bazel, and it needs hermetic
tasks and declared inputs, which is what those tools exist for. picard should not grow its own
cache.

**Our solution**

Verdict: **Integrate**. Effort: M. This one change answers items 14 and 15.

Add executors that hand a package's suite to the tool: `pants` (`pants test {path}::`), `turbo`
(`turbo run test --filter={package}`) and `nx` (`nx run {package}:test`). picard still decides
which packages run at each tier. The tool then applies its local and remote cache and its task
ordering. An explicit command in `[packages.commands]` still wins over an executor.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned
[[discover]]
provider = "uv"
executor = "pants"

[executors.pants]
launcher = "pants"
```

```sh
picard simulate --files packages/component-def/src/component_def/formatter.py --commands
# today (real output, executor "uv-pytest"):
#   component-def:test   uv run --no-sync pytest packages/component-def -m "unit"
# planned (executor "pants"):
#   component-def:test   pants test packages/component-def::

# Turborepo or Nx instead:
#   @demo/ui-kit:test    turbo run test --filter=@demo/ui-kit
#   ui-kit:test          nx run ui-kit:test
```

### 9. Undeclared dependency detection

The native graph is as good as the dependencies a package declares. Nx lists "undeclared inputs
are missed" as a known gotcha, and Pants avoids the problem by inferring imports. An import
nobody declared is a silent under-test.

**Our solution**

Verdict: **Integrate**. Effort: S to M.

Reuse the checkers that exist: `deptry` for Python and `knip` or `depcheck` for JavaScript. They
run as a named check that a tier lists in `run`, which is a mechanism picard already has. picard
adds the defaults so nobody writes the command: the Python executors get a `deps` suite and the
JavaScript executors a `deps` suite, both off until a tier lists them.

**How it will be used**

Works today with a hand-written command:

```toml
# .picard/pipeline.toml           # today
[discover.commands]
deps = "uv run --no-sync deptry {path}"

[tiers.1]
run = ["lint", "test", "deps"]
```

```sh
picard plan --tier 1 --base origin/main --format text   # today: `deps` appears as a suite per affected package
```

Planned: the default is built in, so only the tier line is needed.

```toml
[tiers.1]
run = ["lint", "test", "deps"]         # planned: `deps` comes from the executor
```

### 10. Flaky tests

Nx detects a flaky task when the same inputs pass and fail, and re-runs it on another agent (both
need Nx Cloud). Buildkite and Develocity track and quarantine flaky tests.

**Our solution**

Verdict: **Integrate**. Effort: S. Already on the [roadmap](improvements.md).

Take the cheap, local half. A `flaky` marker keeps a test out of the blocking tiers, and an
executor `retries` option adds the tool's own retry flag (`pytest-rerunfailures`,
`jest --retries`) for the tier that runs it. Tracking and quarantine analytics belong to the
products that do them.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned: the markers part works today
[tiers.1]
markers = "(unit or integration) and not flaky"      # blocks the PR without flaky tests

[tiers.4]
markers = ""                                          # nightly runs everything, flaky included

[executors.pytest]
retries = 2                                           # planned: adds --reruns 2
```

```sh
picard simulate --commands          # nightly line, planned:
#   some-api:test   pytest apps/some-api --reruns 2
```

### 11. Test-level selection

Jest has `--changedSince` and `--findRelatedTests`, which resolve the import graph themselves.
`pytest-impacted` selects tests from the git diff and the import graph with no database and is
beta, `pytest-testmon` tracks coverage per test but needs its database kept between runs, and
the commercial services (Datadog Test Impact Analysis, Develocity Predictive Test Selection,
Launchable) select from coverage or a model. Datadog documents the failure mode: library
dependencies, compiler options, external services and data files are invisible to
coverage-based selection.

**Our solution**

Verdict: **Integrate**, opt-in. Effort: M. The least certain payoff.

None of this is picard's to build. Add an opt-in executor option that appends the tool's flag,
enabled per tier, used at tiers 0 and 1 only. Tiers 3 and 4 stay the full-run safety net. It
needs the base, so it works with `--base` and `--local`.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned
[executors.jest]
selection = "related"            # adds --changedSince=<base>

[executors.pytest]
selection = "impacted"           # adds --impacted --impacted-module=<package>

[tiers.1]
selection = true
[tiers.3]
selection = false                # the merge queue always runs the whole suite
```

```sh
picard simulate --base origin/main --commands     # tier 1 line, planned:
#   some-api:test   uv run --no-sync pytest apps/some-api -m "unit or integration" --impacted --impacted-module=some_api
#   @demo/ui-kit:test   pnpm --filter @demo/ui-kit run test -- --changedSince=origin/main
```

### 12. Incremental merge queue

GitHub's merge queue has build concurrency, a minimum group size and a wait time, which are
settings on GitHub and not picard's. picard's tier 3 runs every package for each queue group,
which gets expensive in a large repo. The `merge_group` event carries a base SHA.

**Our solution**

Verdict: **Defer**. Effort: M.

Add an opt-in `compare = "merge-group-base"` for tier 3, so it can run the affected packages
against the group's base instead of everything. It weakens the safety net, because tier 3 is the
last gate before `main`, so it should wait until the nightly tier is trusted and reports its
failures reliably. The default stays `all`. Trigger to revisit: a repo whose queue is the
bottleneck.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned, opt-in
[tiers.3]
name     = "merge-queue"
compare  = "merge-group-base"
packages = "affected"              # the default stays "all"
markers  = "not slow"
```

```yaml
# pipeline-merge-queue.yml         # planned
with:
  tier: "3"
  base: ${{ github.event.merge_group.base_sha }}
  head: ${{ github.event.merge_group.head_sha }}
```

### 13. Base is the last green commit

Nx recommends comparing against the latest successful commit on `main`, so everything since the
last good run is included, whereas picard compares a PR with its base commit. The difference
matters for runs on `main`, not for PRs, and picard's merge queue and nightly tiers already run
everything.

**Our solution**

Verdict: **Defer**. Effort: S to M.

Do nothing now. Revisit only if a tier is added that runs affected work on `main`. When that
happens, resolve the base from the CI provider's own history (the last successful run of a
named workflow) in the workflow, and pass it to picard with `--base`. picard needs no new
logic, only a documented recipe.

**How it will be used**

Works today as a manual recipe, because `--base` takes any commit:

```sh
BASE=$(gh run list --workflow pipeline-nightly.yml --status success --limit 1 \
        --json headSha --jq '.[0].headSha')                  # today
picard simulate --base "$BASE" --tiers 1                      # today: what changed since the last green nightly
```

A tier that uses this by default would be a later change:

```toml
[tiers.4]
compare = "last-green"             # planned, only if wanted: the workflow passes the base
```

### 14. Task ordering between packages

Nx and Turborepo declare `dependsOn: ["^build"]`, and Bazel and Pants order work by their graphs,
so a package's tests wait for the libraries it needs to be built. picard runs each package's
suites as independent matrix jobs, so it has no ordering between packages.

**Our solution**

Verdict: **Skip**, delegate through item 8. Effort: L if built, none if delegated.

Ordering is what a build system is, so picard will not grow a scheduler. A repo that needs it
uses a `turbo`, `nx` or `pants` executor, and the tool orders and caches the dependencies of
each task. picard still decides which packages run.

**How it will be used**

```jsonc
// turbo.json                      // the tool's own file
{ "tasks": { "test": { "dependsOn": ["^build"] } } }
```

```toml
# .picard/pipeline.toml           # planned (item 8)
[[discover]]
provider = "npm"
executor = "turbo"
```

```sh
picard simulate --files packages/ui-kit/src/index.js --commands   # planned, illustrative output:
#   @demo/admin-frontend:test   turbo run test --filter=@demo/admin-frontend    # turbo builds ui-kit first
```

### 15. Result caching and a remote cache

Caching task results is the biggest speed-up in Nx, Turborepo, Pants and Bazel. It needs
hermetic tasks and declared inputs. Turborepo's remote cache needs `TURBO_TOKEN` and `TURBO_TEAM`
and either Vercel's service or a self-hosted API. Pants talks to REAPI servers such as BuildBarn,
and Bazel to HTTP or gRPC caches.

**Our solution**

Verdict: **Skip**, delegate through item 8. Effort: L if built, none if delegated.

picard will not store task results. When a package runs through a tool executor, that tool's
cache applies, local and remote. Plain dependency caching (uv, pnpm, buildx) is a one-place
addition to the run-suite action, and is already on the [roadmap](improvements.md).

**How it will be used**

```toml
# .picard/pipeline.toml           # planned (item 8)
[[discover]]
provider = "npm"
executor = "turbo"
```

```yaml
# the suite step in CI: the cache credentials go to the tool, never to picard   # planned
env:
  TURBO_TOKEN: ${{ secrets.TURBO_TOKEN }}
  TURBO_TEAM: ${{ vars.TURBO_TEAM }}
```

```sh
picard run --tier 1 --local         # the same tool, the same local cache on your machine
```

### 16. Distributed execution

Nx Agents and dynamic task packing (both Nx Cloud) and Bazel or Pants remote execution spread
tasks across machines.

**Our solution**

Verdict: **Skip**. Effort: L.

GitHub's matrix is the distribution layer, and picard already produces it from the plan. A repo
that adopts Pants or Bazel remote execution gets it through the executors in item 8.

**How it will be used**

```sh
picard plan --tier 1 --base origin/main --format json | jq -c '.suites | length'   # today: how many jobs the matrix will have
```

```yaml
# pipeline-pr.yml, today: the plan's matrix becomes parallel jobs
strategy:
  matrix: ${{ fromJSON(needs.plan.outputs.matrix1) }}
```

### 17. A plan comment on the PR, and run metrics

bazel-diff's `explain` can post a Mermaid graph to a PR comment, and the commercial CI products
show run history and analytics. picard has the job summary and a Mermaid format, but nothing
posts where reviewers look, and nothing records what affected-based CI saved.

**Our solution**

Verdict: **Adopt**, small. Effort: S to M. The comment is already on the
[roadmap](improvements.md).

- `picard plan` can write the plan as a Markdown comment and as a small metrics file.
  A separate job with only `pull-requests: write` and no PR code posts or updates one sticky
  comment.
- The metrics file records packages total and selected, suites total and selected, and the
  tier. A team sends it to whatever observability it already has. picard builds no dashboard.

**How it will be used**

```sh
picard plan --tier 1 --base origin/main --comment plan.md --metrics metrics.json    # planned
cat metrics.json
# {"tier":1,"packages_total":7,"packages_selected":3,"suites_total":12,"suites_selected":6,"selected_pct":50}
```

```yaml
# a job that never runs PR code         # planned
permissions: { pull-requests: write }
steps:
  - uses: ./.github/actions/pipeline-comment
    with: { file: plan.md }
```

### 18. Per-package releases in a monorepo

Nx release, changesets and release-please's manifest mode version packages separately. picard's
tier 5 and the `pipeline-rc-tag.yml` workflow assume one version for the repo. The demo monorepo
notes that the release-candidate workflow expects a single-package release PR title.

**Our solution**

Verdict: **Defer**. Effort: M.

release-please already supports separate versions per package, so the work is picard no longer
assuming one: the release-candidate workflow reads the release PR's packages instead of one
title, and the tier-5 docs describe manifest mode. Wait for a repo that needs it.

**How it will be used**

```jsonc
// .github/release-please/config.json      // today: release-please's own manifest mode
{
  "packages": {
    "packages/component-abc": { "component": "component-abc" },
    "apps/some-api":          { "component": "some-api" }
  }
}
```

```sh
picard affected --base origin/main --format names     # planned (item 4): which packages a release PR will touch
```

### 19. Self-healing CI, flaky analytics, conformance rules and code owners

These are Nx Cloud products, several on its enterprise plan. Self-healing CI proposes a fix on a
failing PR, and the others report on or govern a workspace.

**Our solution**

Verdict: **Skip**. Effort: not applicable.

They are vendor-specific and not a decision-layer concern. A team that wants them uses Nx Cloud
directly. The Nx engine (item 1) and the Nx executor (item 8) keep working alongside it, because
picard only chooses what runs.

**How it will be used**

```toml
# .picard/pipeline.toml           # planned: picard decides, Nx Cloud (if used) executes and reports
[impact]
engine = "nx"

[[discover]]
provider = "npm"
executor = "nx"
```

### 20. Module boundary enforcement

Nx and Turborepo can fail a build when a package imports something it is not allowed to.

**Our solution**

Verdict: **Skip**, as a named check. Effort: S.

It is a lint, not an affected-based feature. A repo that wants it runs `import-linter` for
Python or the tool's own boundary rule as a named check that a tier lists. This works with
picard's existing command mechanism and needs no new code.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[discover.commands]
boundaries = "uv run --no-sync lint-imports"

[tiers.1]
run = ["lint", "test", "boundaries"]
```

```sh
picard simulate --files packages/component-abc/src/component_abc/encoder.py --commands   # today: boundaries appears per affected package
```

### 21. A daemon, watch mode or hash service

Pants keeps work warm in a daemon, `nx watch` and `turbo watch` rerun on change, and bazel-diff
can run as an HTTP service that caches hashes per commit. They pay off when computing the graph
is slow.

**Our solution**

Verdict: **Skip**. Effort: M if built.

picard computes the graph from manifests on each run and memoises the answer within the run.
Measured on 2026-10-30 in the demo (7 packages, native engine): `picard graph` takes 0.06 to
0.08 seconds. A daemon would add a process to manage for no visible gain. Revisit only if a very
large repo shows otherwise. For a local watch loop, use a file watcher around the command.

**How it will be used**

```sh
time picard graph --files packages/component-abc/src/component_abc/encoder.py   # today, real: ~0.07 s for 7 packages
watchexec -e py,ts -- picard simulate --local                                    # today: rerun on every save
```

## Suggested order

1. **An Nx engine** (item 1), then the engine interface change that passes base and head, and
   adapters for Turborepo, moon and bazel-diff.
2. **Shallow clone and unknown base** (5), **force a full run** (6) and **`picard affected`**
   (4): all small, and they make the existing behaviour safer and more useful.
3. **Lockfile-aware narrowing** (2): the largest saving, with a fail-closed design.
4. **Test sharding** (3) and **delegating executors** (8): the speed work, built on other
   tools' splitters and caches.
5. **Test-only changes** (7) and **undeclared dependency check** (9): precision, and protection
   against under-testing.

Everything else waits for a repo that needs it.

## Not verified

These came up but were not confirmed from official documentation in this pass: Vitest's
`--changed` behaviour, pnpm's `--filter "...[origin/main]"` selector, Mergify and Aviator
speculative batching, CircleCI timing-based splitting, how Rush and Lage detect changes, and
the exact Pants shard flag name (its own page uses both `--test-shard` and `--shard`).
`pytest-testmon` is described from third-party write-ups only. moon's documentation page does
not say how its changed files can be supplied beyond `git status`, and bazel-diff does not
document the format of `--modified-filepaths` or whether it removes the need to check out each
revision.

## Sources

- Nx: [CI features](https://nx.dev/docs/features/ci-features),
  [affected](https://nx.dev/docs/features/ci-features/affected),
  [flaky tasks](https://nx.dev/docs/kb/flaky-tasks),
  [splitting end-to-end tasks](https://nx.dev/docs/features/ci-features/split-e2e-tasks)
- Turborepo: [constructing CI](https://turborepo.dev/docs/crafting-your-repository/constructing-ci)
- Pants: [target selection](https://www.pantsbuild.org/stable/docs/using-pants/advanced-target-selection),
  [remote caching and execution](https://www.pantsbuild.org/stable/docs/using-pants/remote-caching-and-execution),
  [how Pants works](https://www.pantsbuild.org/stable/docs/introduction/how-does-pants-work)
- Bazel: [remote caching](https://bazel.build/remote/caching),
  [bazel-diff](https://github.com/Tinder/bazel-diff)
- moon: [affected](https://moonrepo.dev/docs/concepts/affected)
- Jest: [command line options](https://dyn.manpages.debian.org/trixie/jest/jest.1)
- Python: [pytest-impacted](https://pypi.org/project/pytest-impacted)
- Test selection services:
  [Develocity](https://docs.gradle.com/develocity/2026.2/administration/build-acceleration/predictive-test-selection/),
  [Launchable](https://help.launchableinc.com/features/predictive-test-selection/),
  [Datadog](https://docs.datadoghq.com/tests/test_impact_analysis/how_it_works/)
- CI vendors: [Buildkite test splitting](https://buildkite.com/docs/test-engine/test-splitting),
  [Buildkite quarantine](https://buildkite.com/docs/test-engine/quarantine),
  [GitHub merge queue](https://docs.github.com/en/enterprise-server@3.20/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)
