# Missing features: picard against other affected-based CI tools

picard decides what CI runs for a change. This page lists what production-grade tools in the
same space do that picard does not, and says for each whether it makes sense to bring in
**without rebuilding what a tool already does**. It started as a decision aid. Items 1 to 11
and 17 are now built. The others stay decisions, with no code behind them.

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
  engine. Item 7 (`test_paths`) closes the part of that gap that costs most: a test-only change
  no longer runs the dependents.
- **A reason for every decision**: `why` chains, `via <engine>` labels, a Mermaid graph and a
  `--debug` trace (Nx `--explain`, bazel-diff `explain`).
- **Local simulation** of a change that is not made yet (`--files`), of a branch (`--local`) and
  of the index (`--staged`), against the right parent branch.
- **Pluggable impact engines with a safe fallback**: Pants is an engine (and, since item 1, so
  are Nx, Turborepo, moon and bazel-diff), and a failing or missing engine widens the answer and
  never narrows it.
- **Beyond the others:** the PR title picks how much to verify, and one gate job makes skipped
  required checks safe. None of the tools below does either. Rule actions that run a named
  command with values captured from the path and checked against an inventory are also not
  something they document. Selecting work for a file with no code is not unique: Nx named inputs
  and moon task inputs can include any file glob, so a dataset change can select one project's
  tasks there too.

## Summary

| # | Feature | Seen in | Verdict | Effort | Status |
|---|---|---|---|---|---|
| 1 | Impact engines for Nx, Turborepo, moon and bazel-diff | Nx, Turborepo, moon, Bazel | **Integrate** | S for Nx, M once for the rest | Built |
| 2 | Lockfile-aware narrowing | Nx | **Adopt** | M | Built |
| 3 | Test sharding by file or timing | Pants, Nx, Buildkite, Jest | **Integrate** | M | Built (splitter default, no timings) |
| 4 | A first-class "list the affected packages" command | Turborepo, moon, Nx, Pants | **Adopt** | S | Built |
| 5 | Shallow clone and unknown base handling | Turborepo, Nx | **Adopt** | S | Built |
| 6 | Force a full run, mark a package unskippable | Datadog | **Adopt** | S | Built |
| 7 | A test-only change does not propagate to dependents | Nx, Pants | **Adopt** | S to M | Built |
| 8 | Executors that delegate to a build tool with a cache | Pants, Nx, Turborepo, Bazel | **Integrate** | M | Built |
| 9 | Undeclared dependency detection | Nx (gotcha), Pants | **Integrate** | S to M | Built |
| 10 | Flaky retry and quarantine | Nx, Buildkite, Develocity | **Integrate** | S | Built (retries; quarantine is the marker) |
| 11 | Test-level selection | Jest, pytest-impacted, Datadog, Develocity | **Integrate**, opt-in | M | Built |
| 12 | Incremental merge queue tier | GitHub merge queue | **Defer** | M | Decision, no code |
| 13 | Base = last green commit | Nx | **Defer** | S to M | Decision, no code (a recipe) |
| 14 | Cross-package task ordering | Nx, Turborepo, Bazel, Pants | **Skip** (delegate, see 8) | L | Decision, served by 8 |
| 15 | Result caching and remote cache | Nx, Turborepo, Pants, Bazel | **Skip** (delegate, see 8) | L | Decision, served by 8 |
| 16 | Distributed execution | Nx, Bazel, Pants | **Skip** | L | Decision, no code |
| 17 | Sticky PR plan comment, run metrics | Nx Cloud, Buildkite | **Adopt**, small | S to M | Built |
| 18 | Per-package releases in a monorepo | Nx, changesets, release-please | **Defer** | M | Decision, no code |
| 19 | Self-healing CI, flaky analytics, conformance, code owners | Nx Cloud | **Skip** | n/a | Decision, no code |
| 20 | Module boundary enforcement | Nx, Turborepo | **Skip** (as an extra) | S | Decision, no code (a recipe) |
| 21 | Daemon, watch mode, hash service | Pants, Nx, Turborepo, bazel-diff | **Skip** | M | Decision, no code |

## The items

Every item has the same three parts: what the feature is, **Our solution**, and **How it will be
used**. In the examples, `# today` means it works now and `# planned` means the command or key
does not exist and is only a proposed design. Output is illustrative unless it says it is real,
and each real example says where it was run.
The configuration lives in `.picard/pipeline.toml`.

### 1. Impact engines for the tools people already run

Nx (`nx show projects --affected`), Turborepo (`turbo query affected`, `turbo ls --affected`),
moon (`moon query projects --affected`) and bazel-diff (`get-impacted-targets`) each compute an
affected set with their own, more precise graph. Reusing them beats copying their graph logic,
but the interface matters. Before this item an engine received only a list of files, which fits
one of the four:

| Tool | What it takes | Fits a list of files? |
|---|---|---|
| Nx | `--base`/`--head`, or `--files` when there is no git | Yes. It can answer `picard simulate --files`. |
| Turborepo | `--base` and `--head` git refs (or `TURBO_SCM_BASE` and `TURBO_SCM_HEAD`). No file list is documented. | No |
| moon | Changed files from the VCS (`git status`). The page does not document base, head or a file list. | No, as far as the docs say. picard sends the files on stdin anyway (below), unverified. |
| bazel-diff | Two checked-out revisions, hashed separately. `--modified-filepaths` only speeds that up and must be a superset of what changed. | No |

**Our solution**

Verdict: **Integrate**. Effort: S for Nx, then M once for the rest. Built.

1. Engines receive the change's revisions (base, head, uncommitted files) when picard has them
   (`--base`, `--local`), as `self.revisions` ([ADR 0007](adr/0007-engines-receive-the-change-revisions.md)).
2. `nx`, `turbo`, `moon` and `bazel-diff` engines share one base: files a package the tool knows
   owns go to the tool, the rest and any uncommitted files to the fallback, picard's graph adds
   the owners of those files and their dependents, and any failure, timeout or unexpected output hands the whole question to the
   fallback. `picard doctor` checks that the tool is installed. Nx asks with `--base`/`--head`
   or `--files`, Turborepo runs `turbo ls --affected --output=json` with `TURBO_SCM_BASE` and
   `TURBO_SCM_HEAD`, moon gets the files on stdin in the shape `moon query changed-files`
   prints, and bazel-diff hashes the base in a temporary git worktree and never relies on
   `--modified-filepaths`.
3. For `--files`, where no git change exists, a git-based engine cannot answer. The fallback
   answers instead and the plan carries a note, so `picard simulate --files` and CI can differ for
   a package, and `--debug` shows exactly where.

The details are in [configuration](configuration.md#impact).

**How it will be used**

```toml
# .picard/pipeline.toml          # today
[impact]
engine   = "nx"
command  = "npx nx"              # default "nx"
fallback = "native"
```

Illustrative (the tools were tested with fakes, not run for real):

```sh
picard doctor                                       # today: picard: ok: the 'nx' impact engine is ready
picard simulate --files libs/ui/src/button.ts       # today: asks `nx show projects --affected --files=... --json`; packages are marked `via nx`
picard plan --tier 1 --base origin/main --format text   # today: asks `nx show projects --affected --base=<merge-base> --head=HEAD --json`
```

For a git-based tool:

```toml
[impact]
engine = "turbo"                                    # today (also "moon", "bazel-diff")
```

```sh
picard plan --tier 1 --base origin/main --head HEAD     # today: runs `turbo ls --affected --output=json` with TURBO_SCM_BASE=<merge-base>, TURBO_SCM_HEAD=HEAD
picard simulate --files apps/web/src/a.ts               # today, illustrative output:
# ! turbo answers from git refs only: the native engine answered
```

When the tool is missing or fails, the fallback answers for every file (today, real output from
a uv workspace with no Nx installed):

```sh
picard doctor
# picard: warning: Nx is not installed: 'nx' was not found
# picard: for install steps run: picard doctor --how-to-install      (exit 1)

picard affected --files packages/core/src/mod.py --format json
# picard: warning: Nx is not installed: 'nx' was not found. Using the native engine instead. To see how to install it, run: picard doctor --how-to-install
#   ... "notes": ["Nx is not installed: 'nx' was not found; the native engine answered"]
```

### 2. Lockfile-aware narrowing

A lockfile is a global file: any change to it affects every package. Nx narrows this with
`projectsAffectedByDependencyUpdates: "auto"`, so only the projects whose resolved dependencies
changed are affected. Without it every Dependabot bump runs everything.

**Our solution**

Verdict: **Adopt**. Effort: M. No tool provides this for picard, because it is the decision layer.
Built, opt-in per lockfile under `[lockfiles] narrow`.

- When a listed lockfile changed, read its old version at the merge base and the new one at the
  head (the working tree for a lockfile `--local` finds uncommitted), find the external
  packages whose resolved version, source or dependencies changed, and follow the lockfile's own
  edges to the workspace members that use them. Those members and their dependents are affected.
- picard is standard library only, so it reads the lockfiles it can parse without a
  dependency: `uv.lock` and `Cargo.lock` (TOML) and `package-lock.json` (JSON).
  `pnpm-lock.yaml` and `yarn.lock` need a parser picard does not have, so they stay fail-closed
  and keep running everything.
- Fail closed everywhere: a lockfile that cannot be parsed, a change that cannot be attributed,
  or a lockfile not listed under `narrow` runs everything and says why.
- It needs the old content, so it works with `--base` and `--local`, and not with `--files`.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[lockfiles]
narrow = ["uv.lock", "Cargo.lock", "**/package-lock.json"]   # anything else stays "run everything"
```

A listed lockfile is narrowed even if `[paths] global` also matches it, so the existing
`global` list can stay as it is.

```sh
# A Dependabot-style PR: `uv lock -P httpx` moved httpx 0.27.0 -> 0.28.1 and dropped sniffio,
# and uv.lock is the only changed file.  # today, real output (a uv workspace: core uses httpx,
# api depends on core, web does not)
picard plan --tier 1 --base main \
  --title "chore(deps): bump httpx from 0.27 to 0.28 (ABC-9)" --format text
# tier 1 (pr-fast-gate)
#   profile: deps   release: none
#   packages: api, core
#   markers: unit or integration
#   impact: api is affected through api -> core (via lockfile)
#   impact: core owns a changed file (via lockfile)
#   ...
#   warning: uv.lock changed httpx 0.27.0 -> 0.28.1, sniffio 1.3.1 -> none: reaches 2 of 3 packages (api, core)

# The same lockfile without a commit range (--files)   # today, real output
#   packages: api, core, web
#   impact: everything is affected: uv.lock cannot be attributed to packages
#   warning: uv.lock changed and cannot be attributed to packages (lockfile narrowing needs --base or --local): running everything
```

`pnpm-lock.yaml` and `yarn.lock` are rejected under `narrow` and stay in `[paths] global`. A
new or deleted lockfile, one that does not parse, a bump that reaches a workspace member that is
not a picard package (such as the uv workspace root's dev tools), a change used by no member and
a hash-only change all run everything with the reason in the warning. In a copy of the demo,
moving its root dev dependency `ruff` with `uv lock -P` runs all seven packages with
`warning: uv.lock changed and cannot be attributed to packages ('<root project>' is not a picard package): running everything`
(real output, the root project's name replaced).

### 3. Test sharding

One package is one job, so a big package is one long job. Pants has `--test-shard=k/N`, Nx splits
end-to-end tests by file (the atomizer), Buildkite splits by recorded timings, and Jest has
`--shard`.

**Our solution**

Verdict: **Integrate**. Effort: M. Built, except balancing by timings.

- A `shards = N` key per package expands a suite into N matrix jobs. picard's plan is already a
  matrix, so this is a plan field and nothing else changes in the workflows.
- Each executor passes the tool's own flag: `pytest-split` for pytest, Jest's `--shard=k/N` for
  the pnpm and npm executors, `--test-shard` for Pants. picard never splits tests itself. A
  Vitest repo writes a `test` command with a `{shard}` placeholder, because Vitest's flags were
  not verified.
- Shards split by the splitter's default. Balancing by recorded duration, from the nightly
  run's timing file, is not built and stays on the [roadmap](improvements.md).
- A local `picard run` runs the whole suite once, because shards exist to parallelise CI.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[[packages]]
name     = "some-api"
path     = "apps/some-api"
executor = "uv-pytest"
shards   = 4
```

```sh
picard simulate --files apps/some-api/src/some_api/handlers.py --tiers 1 --commands   # today, real output (trimmed):
# tier 1 (pr-fast-gate) commands:
#   some-api:lint               uv run --no-sync ruff check apps/some-api
#   some-api:test#1/4           uv run --no-sync pytest apps/some-api -m "unit or integration" --splits 4 --group 1
#   some-api:test#2/4           uv run --no-sync pytest apps/some-api -m "unit or integration" --splits 4 --group 2
#   some-api:test#3/4           uv run --no-sync pytest apps/some-api -m "unit or integration" --splits 4 --group 3
#   some-api:test#4/4           uv run --no-sync pytest apps/some-api -m "unit or integration" --splits 4 --group 4
picard run --tier 1 --local            # today: runs the whole suite once, not four shards
```

The repo installs pytest-split; picard only passes its flags. The pnpm and npm executors pass
` -- --shard=k/N` to the package's `test` script.

### 4. A first-class "list what is affected" command

`turbo query affected`, `moon query projects --affected --json`, `nx show projects --affected`
and `pants dependents` let other steps ask the question: build only the affected images, deploy
only the affected apps, lint only the affected packages. picard has the data in
`plan --format json` and `graph --format json`, but no short command with a stable shape.

**Our solution**

Verdict: **Adopt**. Effort: S. Built.

`picard affected` takes the same change sources as `plan` (`--files`, `--base`, `--local`,
`--staged`) and a stable output: `names`, `paths`, `json`, and `matrix` (GitHub's
`{"include":[...]}` shape). The JSON shape is documented and versioned like the plan's. It
extends picard past tests: any step can use the answer.

**How it will be used**

```sh
# today, real output (a scratch repo with three packages, a commit that changes component-def)
picard affected --base main --format names
# component-def
# some-api

picard affected --files apps/landing-page/src/page.js --format paths     # today, real
# apps/landing-page

picard affected --base main --format matrix         # today, real
# {"include":[{"name":"component-def","path":"packages/component-def"},{"name":"some-api","path":"apps/some-api"}]}

picard affected --files README.md --format matrix   # today, real: nothing affected
# {"include":[]}
```

Names and paths come in config order, one per line, and print nothing when the set is empty.
`--format json` is versioned (`schema` 1) and adds the provider, `decided_by` and the chain of
each package; see [simulation](simulation.md#listing-what-is-affected).

Build and deploy only what changed:

```yaml
# a workflow job        # today
- id: affected
  env:
    BASE: ${{ github.event.pull_request.base.sha }}
  run: echo "apps=$(picard affected --base "$BASE" --format matrix)" >> "$GITHUB_OUTPUT"
# a second job:  strategy: { matrix: ${{ fromJSON(needs.affected.outputs.apps) }} }  -> docker build ${{ matrix.path }}
```

### 5. Shallow clones and an unknown base

Turborepo falls back to running all tasks on a shallow clone, and Nx needs full history. picard's
workflows check out full history, but run elsewhere (a shallow CI clone, a base that is not
fetched) it used to stop with a raw `git diff ... unknown revision` error and exit status 2.
That is safe, because the job fails, but it is not friendly. Checked on 2026-10-30 with a
depth-1 clone.

**Our solution**

Verdict: **Adopt**. Effort: S. Built.

When the base does not resolve, or its merge base with the head cannot be computed,
picard fetches it once (`git fetch --no-tags origin <base>`, with `--unshallow` in a shallow
clone, only when a remote `origin` exists). If that fails, it follows `[git] unknown_base`:
`full` (default) runs everything with a clear warning, which fits fail-closed, and `fail`
keeps the old red job, with a clear message and exit status 2.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[git]
unknown_base = "full"      # full (default) | fail
```

```sh
# Before (real output from a depth-1 clone, a base that is not in it):
picard plan --tier 1 --base <a commit> --head HEAD --title "fix: x (ABC-1)"
# picard: git diff --name-only --no-renames -z <a commit>...HEAD failed: fatal: ambiguous argument ...   (exit 2)

# today, real output from a depth-1 clone of a scratch repo, a full SHA that origin does not have
picard plan --tier 1 --base 0123456789abcdef0123456789abcdef01234567 --title "fix: x (ABC-1)" --format text
# picard: warning: base 0123456789abcdef0123456789abcdef01234567 is not in this shallow clone and could not be fetched
# picard: warning: running every package instead (set [git] unknown_base = "fail" to stop here)
# tier 1 (pr-fast-gate)
#   profile: full   release: patch
#   packages: component-def, some-api, landing-page
#   ...
#   note: base 0123456789abcdef0123456789abcdef01234567 is not in this shallow clone and could not be fetched

# today, real: the full SHA of a commit origin has is fetched (--unshallow), and the diff narrows
picard plan --tier 1 --base <full sha of the base> --title "fix: x (ABC-1)" --format text
#   packages: component-def, some-api

# today, real: with unknown_base = "fail" (that clone is no longer shallow after the fetch above)
# picard: base 0123456789abcdef0123456789abcdef01234567 is not in this clone and could not be fetched   (exit 2)
```

An abbreviated SHA can never be fetched, so in a clone that lacks it the base is unknown and
`full` runs everything. The workflows pass full SHAs. A `--head` that is not a commit always
stops with exit 2.

### 6. Force a full run, mark something unskippable

Datadog lets a commit message or a label force a full run, and lets a test be marked unskippable
because coverage cannot see its inputs. picard lets a title raise the profile and has
`baseline = true` for tier 2, but has no way to say "run everything for this PR" or "this package
is never skipped".

**Our solution**

Verdict: **Adopt**. Effort: S. Built.

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
# pipeline-pr.yml      # today
on:
  pull_request:
    types: [opened, edited, reopened, synchronize, ready_for_review, labeled, unlabeled]
# ... in both plan steps:
with:
  labels: ${{ toJSON(github.event.pull_request.labels.*.name) }}   # -> env PICARD_LABELS in the action
```

The label must be spelled exactly as in `[labels] full`: matching is case-sensitive.

### 7. A test-only change should not run the dependents

Under the native engine a change to a package's test files runs that package and everything that
depends on it. Nx has named inputs (a `production` set that excludes spec files, so a spec-only
change selects the package's tests but not its dependents), and Pants gets it for free because
nothing imports a test file. In the demo today (real output), changing
`packages/component-def/tests/unit/test_formatter.py` under the native engine runs
`component-def` **and** `some-api`, and under Pants only `component-def`.

**Our solution**

Verdict: **Adopt**. Effort: S to M. Built.

`test_paths` globs on a package (or a `[[discover]]` block) name its test files. A change that
only touches those files affects the owning package and not its dependents. A file that matches
nothing keeps the old behaviour, and a change that touches both test and source files affects
dependents as usual. The Pants engine already behaves this way for Python.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[[packages]]
name       = "component-def"
path       = "packages/component-def"
test_paths = ["tests/**", "**/test_*.py", "**/conftest.py"]

[[discover]]                      # or for every package a block finds
provider   = "uv"
test_paths = ["tests/**"]
```

```sh
# today, real output: a copy of the demo with test_paths = ["tests/**"] on its uv block,
# native engine, a commit that changes only that test file
picard affected --base main
# component-def
picard simulate --base main --tiers 1 --title "test(component-def): cover formatter (DEMO-1)"
# WHY
#   component-def owns a changed file (test files only: dependents are not affected) (via native)
# (without test_paths, the native engine also runs some-api)
```

A change that also touches a source file, or a file `test_paths` does not match, runs the
dependents as before.

### 8. Executors that delegate to a tool with a cache

Result caching is the biggest speed-up in Nx, Turborepo, Pants and Bazel, and it needs hermetic
tasks and declared inputs, which is what those tools exist for. picard should not grow its own
cache.

**Our solution**

Verdict: **Integrate**. Effort: M. Built. This one change answers items 14 and 15.

Executors hand a package's suite to the tool: `pants` (`pants test {path}::`), `turbo`
(`turbo run test --filter={package}`) and `nx` (`nx run {package}:test`). picard still decides
which packages run at each tier. The tool then applies its local and remote cache and its task
ordering. An explicit command in `[packages.commands]` still wins over an executor.

Each builds `lint` and `test`, with a `launcher` option (default the tool's name). Pants also
shards (`--test-shard=k/N`, k from 0) and retries (`--test-attempts-default`). turbo and nx
ignore the tier's markers. So does Pants unless `markers = true`: Pants runs pytest once per
file, and a file whose tests `-m` all deselects exits 5, which Pants counts as a failure. A repo
that turns `markers` on must make exit 5 a success itself (a `pytest_sessionfinish` hook in
`conftest.py`, or `pytest-custom-exit-code`). picard ships neither.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[[discover]]
provider = "uv"
executor = "pants"

[executors.pants]
launcher = "pants"
```

```sh
picard simulate --files packages/component-def/src/component_def/formatter.py --commands
# today (real output, executor "pants", tier 0):
#   component-def:lint          pants lint packages/component-def::
#   component-def:test          pants test packages/component-def::
#   some-api:lint               pants lint apps/some-api::
#   some-api:test               pants test apps/some-api::

# the two jobs picard emits for `shards = 2`, run with Pants 2.33.1 (real output):
pants test --test-shard=0/2 packages/component-def::
#   ✓ packages/component-def/tests/unit/test_formatter.py:tests succeeded in 0.26s.
pants test --test-shard=1/2 packages/component-def::
#   ✓ packages/component-def/tests/integration/test_roundtrip.py:tests succeeded in 0.24s.

# Turborepo or Nx on the npm block instead (today, real generated commands; the tools were not run):
#   @demo/ui-kit:test           turbo run test --filter=@demo/ui-kit
#   @demo/ui-kit:test           nx run @demo/ui-kit:test
```

### 9. Undeclared dependency detection

The native graph is as good as the dependencies a package declares. Nx lists "undeclared inputs
are missed" as a known gotcha, and Pants avoids the problem by inferring imports. An import
nobody declared is a silent under-test.

**Our solution**

Verdict: **Integrate**. Effort: S to M. Built.

Reuse the checkers that exist: `deptry` for Python and `knip` for JavaScript. They
run as a named check that a tier lists in `run`, which is a mechanism picard already has. picard
adds the defaults so nobody writes the command: the Python executors get a `deps` suite and the
JavaScript executors a `deps` suite, both off until a tier lists them.

**How it will be used**

A hand-written command works, as it did before this item:

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

The default is now built in, so only the tier line is needed: the `pytest` and `uv-pytest`
executors run `deptry <path>` and the `pnpm` and `npm` executors run
`knip --include dependencies,unlisted --workspace <path>`. Set `deps_checker` under
`[executors.<name>]` to run something else. picard uses knip rather than depcheck, which is no
longer maintained.

```toml
[tiers.1]
run = ["lint", "test", "deps"]         # today: `deps` comes from the executor
```

```sh
picard simulate --base main --tiers 1 --commands   # today, real output (trimmed):
#   some-api:deps               uv run --no-sync deptry apps/some-api
#   @demo/ui-kit:deps           pnpm exec knip --include dependencies,unlisted --workspace packages/ui-kit
```

### 10. Flaky tests

Nx detects a flaky task when the same inputs pass and fail, and re-runs it on another agent (both
need Nx Cloud). Buildkite and Develocity track and quarantine flaky tests.

**Our solution**

Verdict: **Integrate**. Effort: S. Built (retries).

Take the cheap, local half. A `flaky` marker keeps a test out of the blocking tiers, and an
executor `retries` option adds the tool's own retry flag (`--reruns` from pytest-rerunfailures,
which the repo installs) to the `test` suite of every tier that uses the executor. Jest has no
CLI retry flag, so JavaScript retries stay in the test runner's own config and the `pnpm` and
`npm` executors reject `retries`. Tracking and quarantine
analytics belong to the products that do them.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[tiers.1]
markers = "(unit or integration) and not flaky"      # blocks the PR without flaky tests

[tiers.4]
markers = ""                                          # nightly runs everything, flaky included

[executors.pytest]
retries = 2                                           # adds --reruns 2 to the test suite
```

```sh
picard simulate --files apps/some-api/src/some_api/handlers.py --tiers 4 --commands   # today, real output (trimmed):
# tier 4 (nightly) commands:
#   some-api:test               pytest apps/some-api --reruns 2
#   @demo/ui-kit:test           pnpm --filter @demo/ui-kit run test
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

Verdict: **Integrate**, opt-in. Effort: M. The least certain payoff. Built.

None of this is picard's to build. An opt-in executor option appends the tool's flag, and a
tier turns it on with `selection = true`, meant for tiers 0 and 1. A tier with
`compare = "none"` cannot select, so the starter's tiers 3 and 4 stay the full-run safety net.
It needs a committed git range: `--base`, or `--local` with no uncommitted files, because
pytest-impacted's branch mode ignores uncommitted files and would select fewer tests than the
change needs. Without one, whole suites run and the plan warns. A forced full run (`ci:full`, an
unknown base) never selects, and needs no warning. The base passed is the merge-base commit.

**How it will be used**

```toml
# .picard/pipeline.toml           # today
[executors.pnpm]
selection = "changed-since"      # adds --changedSince=<base> (Jest)

[executors.uv-pytest]
selection = "impacted"           # adds --impacted --impacted-module=<module> ... (pytest-impacted)

[tiers.1]
selection = true
```

```sh
picard simulate --base main --tiers 1 --commands     # today, real output (trimmed, <base> is the merge-base sha):
#   some-api:test               uv run --no-sync pytest apps/some-api -m "unit or integration" --impacted --impacted-module=some_api --impacted-git-mode=branch --impacted-base-branch=<base>
#   @demo/ui-kit:test           pnpm --filter @demo/ui-kit run test -- --changedSince=<base>
picard simulate --base main --label ci:full --tiers 1 --commands   # today, real output (trimmed): a forced run selects nothing
#   some-api:test               uv run --no-sync pytest apps/some-api -m "unit or integration"
#   @demo/ui-kit:test           pnpm --filter @demo/ui-kit run test
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
# .picard/pipeline.toml           # today (item 8)
[[discover]]
provider = "npm"
executor = "turbo"
```

```sh
picard simulate --files packages/ui-kit/src/index.js --commands   # today (real output, tier 1):
#   @demo/admin-frontend:test   turbo run test --filter=@demo/admin-frontend    # turbo builds ui-kit first
#   @demo/landing-page:test     turbo run test --filter=@demo/landing-page
#   @demo/ui-kit:test           turbo run test --filter=@demo/ui-kit
# (lint lines omitted; turbo itself was not run)
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
# .picard/pipeline.toml           # today (item 8)
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
picard plan --tier 1 --base origin/main --format json | jq -c '.metrics.jobs'   # today: how many jobs the matrix will have
```

```yaml
# pipeline-pr.yml, today: the plan's matrix becomes parallel jobs
strategy:
  matrix: ${{ fromJSON(needs.plan.outputs.matrix1) }}
```

### 17. A plan comment on the PR, and run metrics

bazel-diff's `explain` can post a Mermaid graph to a PR comment, and the commercial CI products
show run history and analytics. picard had the job summary and a Mermaid format, but nothing
posted where reviewers look, and nothing recorded what affected-based CI saved.

**Our solution**

Verdict: **Adopt**, small. Effort: S to M. Built.

- `picard plan --comment` writes the plan as a Markdown comment and `--metrics` a small
  metrics file. The PR workflow uploads both as the `pipeline-plan` artifact, and a separate
  job with only `pull-requests: write` and no PR code posts or updates one sticky comment.
- The metrics file records packages total and selected, suites total and selected, jobs and the
  tier. A team sends it to whatever observability it already has. picard builds no dashboard.

**How it will be used**

```sh
# today, real output (a scratch repo with three packages, a commit that changes component-def)
picard plan --tier 1 --base main --title "fix: def (ABC-1)" --comment plan.md --metrics metrics.json
cat metrics.json
# {"schema":1,"tier":1,"packages_total":3,"packages_selected":2,"suites_total":3,"suites_selected":2,"jobs":2,"selected_pct":67}
```

`--comment` appends one section per tier (the job summary's markdown, with every `@` followed
by a zero-width space so nothing in it mentions anyone). `--metrics` overwrites the file with
one JSON object. `jobs` counts shards and rule actions; the suite counts do not.

```yaml
# pipeline-pr.yml, a job that never runs PR code: no checkout, no local action      # today
plan-comment:
  needs: plan
  if: github.event.pull_request.head.repo.full_name == github.repository   # forks: read-only token
  permissions:
    pull-requests: write
  steps:
    - uses: actions/download-artifact@v8
      with: { name: pipeline-plan }     # plan.md and metrics-*.json from the plan job
    - env:
        GH_TOKEN: ${{ github.token }}
        REPO: ${{ github.repository }}
        PR: ${{ github.event.pull_request.number }}
      run: |
        # find the comment holding <!-- picard-plan --> by github-actions[bot], PATCH it or POST one,
        # with the body sent as a file: gh api ... -F body=@body.md
```

The job is not in `pipeline-gate`'s `needs`, so a failed comment never blocks a merge.

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
picard affected --base origin/main --format names     # today (item 4): which packages a release PR will touch
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
# .picard/pipeline.toml           # today (items 1 and 8): picard decides, Nx Cloud (if used) executes and reports
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

Done: all five steps are built, together with items 10, 11 and 17.

1. **An Nx engine** (item 1), then the engine interface change that passes base and head, and
   adapters for Turborepo, moon and bazel-diff.
2. **Shallow clone and unknown base** (5), **force a full run** (6) and **`picard affected`**
   (4): all small, and they make the existing behaviour safer and more useful.
3. **Lockfile-aware narrowing** (2): the largest saving, with a fail-closed design.
4. **Test sharding** (3) and **delegating executors** (8): the speed work, built on other
   tools' splitters and caches.
5. **Test-only changes** (7) and **undeclared dependency check** (9): precision, and protection
   against under-testing.

Everything else (items 12 to 16 and 18 to 21) waits for a repo that needs it.

## Not verified

From documentation, these came up but were not confirmed in this pass: Vitest's `--changed`
behaviour, pnpm's `--filter "...[origin/main]"` selector, Mergify and Aviator speculative
batching, CircleCI timing-based splitting, and how Rush and Lage detect changes.
`pytest-testmon` is described from third-party write-ups only. None of these is used by a built
item. The Pants shard flag is settled: `pants help test` (Pants 2.33.1) gives `--test-shard=k/N`
with k from 0, and the two shard jobs in item 8 were run.

What the built items rely on but was never run for real, only tested with fakes or by checking
the generated command:

- **The plan comment job never ran on GitHub.** Its permissions, the artifact hand-off and the
  `gh api` calls are checked by string tests and actionlint only. The
  `actions/upload-artifact@v7` and `actions/download-artifact@v8` pair is unverified until a
  real PR run.
- **Nx, Turborepo, moon and bazel-diff were never run.** The engines were tested with fake
  launchers. moon's stdin input (the `moon query changed-files` shape) and the JSON that
  `turbo ls --affected --output=json` prints are not documented upstream, so any output of an
  unexpected shape counts as a failure and widens to the fallback. moon's page does not say
  that changed files can be supplied at all, and picard never uses bazel-diff's
  `--modified-filepaths` (it hashes both revisions instead).
- **pytest-split, pytest-rerunfailures, pytest-impacted, deptry, knip and Jest were never run.**
  Only the commands picard generates (`--splits/--group`, `--reruns`, `--impacted ...`,
  `--shard`, `--changedSince`, `deptry <path>`, `knip ... --workspace`) were tested. Nor were
  the `turbo` and `nx` executors' commands, or the Pants executor's retries
  (`--test-attempts-default` is listed by `pants help test` in Pants 2.33.1, but never ran).
- **No real Cargo.lock or package-lock.json produced by cargo or npm was diffed.** Those parsers
  were tested with small synthetic lockfiles. `uv.lock` was checked against real `uv lock`
  output in two cases: the httpx bump in item 2, which narrowed, and a root dev-dependency move
  in a copy of the demo, which ran everything as designed.

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
