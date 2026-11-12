# Architecture

picard is an [affected-based, change-based, incremental](concepts.md) CI/CD tool with six
tiers. This page is how it is put together.

## Flow

```
                        ┌─────────────────────────────────────┐
 PR title / body ──────▶│                                     │
 changed files ────────▶│  picard                              │──▶ plan (JSON)
 tier number ──────────▶│  + .picard/pipeline.toml          │
                        └─────────────────────────────────────┘
                                                                  │
        ┌─────────────┬─────────────┬──────────────┬──────────────┴┬────────────┐
        ▼             ▼             ▼              ▼               ▼            ▼
   tier 0 hook   tier 1 fast    tier 2        tier 3 merge     tier 4        tier 5
   (local)       gate (matrix)  baseline      queue (all)      nightly       Release Please
                        └──────────┬──────────┘                                  │
                             pipeline-gate                                  tag + GitHub Release
```

## Where things live

picard is a Python package with a `picard` command. A host repo installs it, runs `picard
init`, and gets only the files it has to own.

**This repository** (the tool):

```
src/picard/                      the planner (Python 3.11+, standard library only)
  graph/providers/                 read the package graph from manifests
  graph/engines/                   decide what a change affects
  executors/                       build the command for one suite
  scaffold.py                      picard init / update / install-hooks
  templates/                       package data that `picard init` copies into a host
    github/                          workflows, actions, release-please, CODEOWNERS, ...
    hooks/                           commit-msg, pre-push
    pytest/conftest.py               marker plugin to copy into a test root
    pipeline.toml                    the starter config
tests/                           tests of the tool
docs/                            this documentation
pyproject.toml, justfile         build, dev tools and tasks
.github/workflows/ci.yml         picard's own CI (not the templates above)
```

**A host repository** after `picard init`. Everything is under `.github/` and `.picard/`,
and every file picard owns is prefixed `pipeline-` or sits in a picard-owned folder:

```
.github/workflows/pipeline-*.yml     one workflow per tier (and the rc tag), replaced on update
.github/actions/pipeline-*/          composite actions the workflows call, replaced on update
.github/release-please/, CODEOWNERS, dependabot.yaml, pull_request_template.md   yours after init
.picard/pipeline.toml              the one file a repo edits (yours after init)
.picard/hooks/                     commit-msg, pre-push, replaced on update
.picard/templates/pytest/          marker plugin to copy into a test root, replaced on update
```

The host holds no picard code. CI installs a pinned picard in `pipeline-setup-picard`
(`PICARD_REPOSITORY`, `PICARD_REF`, `PICARD_TOKEN`), and a developer installs it once with
`uv tool install --editable <clone>`. See [ADR 0006](adr/0006-picard-is-a-python-package.md).

## Components

| Component | Responsibility |
|---|---|
| `.picard/pipeline.toml` | The policy: runtime, types, profiles, paths, packages, tiers. Per repo. |
| `src/picard` | A function from (config, tier, titles, body, labels, changed files, revisions) to a plan. |
| Graph providers | Read one ecosystem's manifests and return packages, paths and edges: `uv`, `npm`, `cargo`, `go`, `command`. |
| Impact engines | Map changed files to the packages they affect: `native`, `pants`, `command`, `nx`, `turbo`, `moon`, `bazel-diff`. |
| Executors | Build a suite's command for a package that does not spell it out: `pytest`, `uv-pytest`, `go-test`, `cargo-test`, `pnpm`, `npm`, `make`, and `pants`, `turbo`, `nx`, which hand the suite to the tool and its cache. |
| `bootstrap.py` | The composition root. It registers the built-ins and the plugins, and nothing else does. |
| `pipeline-plan` action | Runs picard in Actions, exports `plan`, `matrix`, `extras`, `runtime`, `skip` and writes a job summary. Inputs `labels` (as `PICARD_LABELS`), `comment` and `metrics` add the PR labels, the plan comment file and the metrics file. |
| `plan-comment` job | In `pipeline-pr.yml`: posts or updates one sticky PR comment from the plan job's artifact. Not part of the gate. |
| `pipeline-check-title` action | Validates the PR title with the same parser the plan uses. |
| `pipeline-run-suite` action | Sets up Python/Node from `[runtime]`, then runs one suite command. |
| `pipeline-extra` action | Runs one named extra: `secrets`, `workflow-lint`, `semgrep`, `dependency-review`, `link-check`, `breaking-change-checks`. |
| `pipeline-*.yml` workflows | Orchestration per tier: plan job, matrix jobs, extras, gate. No decisions. |

## The plan

The planner is a pure function from (config, tier, titles, body, changed files) to a plan: one
JSON document that carries everything a workflow needs. Its fields are in
[reference/plan.md](../reference/plan.md).

A sharded suite appears once per shard, with id `<package>:<suite>#k/N` and the integers
`shard` and `shards`. In `metrics`, `suites_total` counts the package suites that have a
command in this tier over every package, `suites_selected` the distinct package suites the plan
runs (both without shards and rule actions), `jobs` the length of `suites` (shards and rule
actions included) and `selected_pct` is `suites_selected` over `suites_total`, rounded. The
schema stays `1`: fields are only ever added.

## Impact and engines

"Which packages does this change affect" is the one question picard delegates, and it is
answered in two separate steps so each can be swapped.

```
manifests ──▶ graph provider ──▶ packages + edges ──┐
                                                    ├──▶ impact engine ──▶ Impact
changed files ──────────────────────────────────────┘
```

- **Graph providers** (`graph/providers/`) discover packages and the edges between them from the
  files each ecosystem already maintains. They answer "what exists and what depends on what".
- **Impact engines** (`graph/engines/`) answer "what does this change affect". `native` walks
  the discovered graph: the deepest package that owns a file, then its dependents. `pants` asks
  Pants (`list`, `dependents --transitive`) and sends the files it does not own to a fallback
  engine. `command` asks any external tool over stdin and stdout. `nx`, `turbo`, `moon` and
  `bazel-diff` ask those tools, on one shared base (`graph/engines/tool.py`): files the tool
  does not own and uncommitted files go to the fallback, picard's graph always adds the owners
  of the files and their dependents, and any failure hands the whole question to the fallback. `turbo` and `bazel-diff` read the
  commits from `self.revisions` and need a git range.
- **The shared policy** lives in the base class (`ImpactEngine.impact`): a global file such as
  a lockfile affects every package, whatever the engine says. A lockfile listed under
  `[lockfiles] narrow` goes to `lockfile_impact` instead, which diffs it between the revisions
  (`lockfiles.py`) and runs everything whenever the change cannot be attributed. A subclass only
  implements the engine-specific part. An engine never decides what to run, only what is
  affected.
- **Fail safe.** An engine that fails, times out or does not own a file hands the question to
  the fallback (`native` by default) and adds a note to the plan. The answer can get wider,
  never narrower.
- **Labels.** Every affected package records what decided it: an engine (`native`, `pants`,
  `command`, `nx`, `turbo`, `moon`, `bazel-diff`), `rule`, `global file`, `lockfile`, `always`
  or `forced` (a full-run label or an unknown base). `picard graph` and `simulate` show it as
  `via X`, and `picard affected --format json` as `decided_by`.
- **Debug.** `--debug`, `PICARD_DEBUG=1` or `debug = true` under `[impact]` prints on stderr
  what each engine returned and where Pants and the native engine differ. The plan JSON on
  stdout stays clean.
- **Setup.** `picard setup` asks the engine to prepare the repo, and `picard setup --check`
  verifies it for CI. `native` needs nothing. The `pants` engine writes `pants.toml` and the
  build files, never overwrites an existing `pants.toml`, and is idempotent.
- **Memoised.** An impact is computed once per distinct file list and git range in a run, so
  an external engine is not called once per tier.

Providers, engines and executors are looked up by name in registries
(`registry.py`). A repo can add its own from `[plugins]` without changing picard. See
[ADR 0005](adr/0005-plugin-architecture.md).

## Decisions

- **Planner as code, policy as data.** Logic that decides what to run is tested Python, not
  `if:` expressions spread over YAML. See [ADR 0001](adr/0001-plan-driven-tiers.md).
- **A package, pinned by the host.** picard is a normal Python package with a `picard`
  command. A host repo pins the version it runs (a tag or commit) and owns its workflow files,
  so a tooling change is a reviewed bump in that repo. `picard init` and `picard update` write
  the files, and `picard update` is how a host follows a new picard. See
  [ADR 0006](adr/0006-picard-is-a-python-package.md), which supersedes
  [ADR 0002](adr/0002-vendored-template.md).
- **Language-agnostic.** The planner knows packages, paths, markers and command templates,
  not pytest or npm. Providers and executors per ecosystem are plugins behind small interfaces,
  and a repo can supply commands per package.
- **Reuse, do not rebuild.** The graph comes from the package managers or from Pants. picard
  owns the decision layer and the gate, and nothing a build tool already does.
- **No lock-in.** Pants, Nx or Bazel can be the source of the graph or the engine, and none is
  required. The default needs nothing installed.
- **Engines see the commits, not just the files.** `impact()` sets `self.revisions` (base,
  head, uncommitted files) before it calls `compute`, so plugin engines keep their signature.
  No git range means the engine answers from the files or its fallback. See
  [ADR 0007](adr/0007-engines-receive-the-change-revisions.md).
- **Fail closed.** An unparseable title, a file that matches no rule, or a job that should have
  run and did not all end in a stricter run or a failed gate, never a silent pass.
- **One required check per event.** `pipeline-gate` and `merge-gate` absorb skips safely.

## Security model

- **Untrusted text never reaches a shell.** PR titles and bodies are attacker-controlled. They
  are passed as action inputs, mapped to `env:` inside the composite actions and read as
  `$PICARD_TITLE`. Labels go the same way, as `PICARD_LABELS`. No `run:` step interpolates
  `${{ github.event... }}`.
- **The plan comment job runs no PR code.** `plan-comment` is the only job in
  `pipeline-pr.yml` with a write token (`pull-requests: write`, nothing else). It has no
  checkout and no local action: it downloads the plan job's artifact and sends `plan.md` to
  the GitHub API as a file (`gh api -F body=@body.md`), never interpolated. Every `@` in the
  comment is followed by a zero-width space, so text from titles or paths mentions nobody. It
  runs only for PRs from the same repository (a fork PR's token is read-only) and is not in
  `pipeline-gate`'s `needs`, so a failed comment never blocks a merge.
- **Commands come from the repo's own `pipeline.toml`**, with the same trust as its workflows
  (a PR that edits it is reviewed like a PR that edits a workflow, and `CODEOWNERS` covers
  `.picard/` and `.github/`). `markers`, `runs_on` and the version strings are validated to
  plain words because they are substituted into commands and `runs-on`.
- **Fork PRs** run with a read-only token and no secrets. Do not add `pull_request_target`
  and run PR code in it. They get no `PICARD_TOKEN` either, so a private picard repository
  cannot be installed for them: keep picard readable, or do not run fork PRs.
- **Least privilege**: workflows default to `contents: read`. Only the release workflow, the
  nightly failure report and the PR plan comment raise it, at job or workflow level.
- **Checkout without credentials** (`persist-credentials: false`) in every job that does not
  push.
- **Release tags use a GitHub App token**, so tag-triggered workflows start, and no personal
  token is involved.
- **Pin third-party actions to SHAs** before relying on this, and review the images in
  `pipeline-extra`.

## Limits

- Package-level granularity: a change inside a package runs that package's whole suite for the
  tier, unless the tier opts in to test selection (`selection = true`), which passes the test
  runner's own flag. Coverage-based test impact analysis is on the [roadmap](roadmap.md).
- `{markers}` is a pytest-style expression. Other runners map it in the command or ignore it.
- The graph is as precise as the dependencies a package declares. Edges that live in no
  manifest are added with `depends_on` or a `[[path_rules]]` entry.
- Only Python gets import-level precision, and only through the `pants` engine. The `nx`,
  `turbo`, `moon` and `bazel-diff` engines add what their graphs see on top of picard's own
  (the owners of the files and their dependents), so they can widen the native answer but not
  narrow it. Under the native engine, `test_paths` keeps a test-only change from reaching the
  dependents.
- A host runs the picard version it pinned, and its scaffolded workflows only change when
  it runs `picard update`.
