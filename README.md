# picard

An **affected-based CI/CD** tool, also called **change-based execution** or **incremental CI**,
built as a **multitier** pipeline. picard works out what a change can affect and runs only
that, then widens the net tier by tier until nothing is skipped. It is language-agnostic: Go,
Rust, Python, TypeScript or anything else that can describe its packages.

It uses four inputs:

- **The Conventional Commit PR title.** It decides how much to verify and what release the
  change produces.
- **The changed file paths.** They say where the change is.
- **The code dependency graph.** It says what depends on the change. It is read from each
  ecosystem's own manifests (uv, npm, pnpm, yarn, cargo, go, or a custom command), or from
  Pants, so it never drifts from a hand-kept list.
- **`[[rules]]`** for files that are not code, such as a model's dataset. A dataset change then
  runs only that model's tests and pipelines.

picard is a Python package with a `picard` command. The concepts are in
[docs/concepts.md](docs/concepts.md).

No hosting or deploy platform is assumed. The release tier ends at a `vX.Y.Z` tag and a
GitHub Release, and your repo adds whatever deploy step it needs after that.

## The idea

```
PR title ──┐
           ├─▶ picard ──▶ impact ──▶ plan (JSON) ──▶ every tier reads it its own way
changed ───┤     │         ▲
files      │     │   graph providers + impact engine
code graph ┘     └── .pipeline/pipeline.toml (the only file a repo edits)
```

Workflows never decide anything. They ask `picard` for a plan and execute it, so tuning the
pipeline means editing `pipeline.toml`, not YAML, and a local run and a CI run follow the same
rules.

## Never run less than the truth

Skipping work is only safe if the answer errs on the side of running more. A lockfile or config
change runs everything, a file no rule covers runs everything, a title can raise the bar and
never lower it, an engine that fails or does not own a file hands the question to the native
engine, and the gate fails a job that had to run and did not. The full list is in
[docs/concepts.md](docs/concepts.md#never-run-less-than-the-truth).

## Tiers

Six tiers (0 to 5). Tiers 0 to 2 are **incremental**: they run what the change affects. Tiers 3
and 4 ignore the diff and run everything, as the safety net. Each tier reads the plan its own
way:

| Tier | Runs on | Compared with | Packages | Test markers |
|---|---|---|---|---|
| 0 · Pre-push | your machine | merge base with `origin/main` | affected only | `unit` |
| 1 · PR fast gate | `pull_request` | PR base commit | affected only | `unit or integration` |
| 2 · PR baseline | `pull_request`, after tier 1 is green | PR base commit | affected + path-rule list + `baseline` packages | `baseline` |
| 3 · Merge queue | `merge_group` | nothing | all | `not slow` |
| 4 · Nightly | `schedule` | nothing | all | all, including `slow` |
| 5 · Release | push to `main` | Conventional Commits since last tag | n/a | n/a |

Tiers 0 to 4 are one `[tiers.N]` table each. Tier 5 is Release Please.
Details: [docs/tiers.md](docs/tiers.md).

## Conventional Commits control the pipeline

The PR title is the commit on main (squash merge), so it is the unit that is validated and
planned. Its type picks a **profile** (how much to verify) and a **release effect**.

| Title type | Profile | Release | What runs on the PR |
|---|---|---|---|
| `feat` | full | minor | tier 1, tier 2, semgrep, dependency review |
| `fix`, `perf` | full | patch | same |
| `refactor`, `revert` | full | none | same, no release |
| `feat!` or `BREAKING CHANGE:` | full + extras | major (minor below 1.0) | same + migration/footer check, tier 2 forced |
| `build`, `ci` | infra | none | workflow lint, tier 1 and 2 |
| `chore(deps)` (Dependabot) | deps | none | dependency review, tier 1 and 2, semgrep |
| `test`, `style`, `chore` | light | none | tier 1 only |
| `docs` | docs | none | secret scan and link check, no test suites |

**Paths can raise the bar, never lower it.** A `docs:` title on a PR that edits source runs the
full profile and warns, so a title cannot be used to skip tests. A title that cannot be parsed
falls back to the strictest profile. Full rules: [docs/conventional-commits.md](docs/conventional-commits.md).

## Install

picard is standard library only and needs Python 3.11 or newer. Clone this repository and
install the `picard` command from the clone:

```sh
git clone <this repository> picard
uv tool install --editable ./picard        # or, inside a virtualenv: pip install -e ./picard
picard --version
```

Pull the clone to get a newer picard. Pants is optional: see
[Pants is optional](#pants-is-optional).

## Adopt it

From the root of the repo you want to put under picard:

```sh
picard init              # writes .github/ workflows and actions, .pipeline/hooks, .pipeline/pipeline.toml
picard validate-config   # after editing .pipeline/pipeline.toml
picard install-hooks     # once per clone: git hooks for commit messages and pre-push
```

After upgrading picard, run `picard update` to refresh the files picard owns. It replaces the
`pipeline-*` workflows and actions and the hooks, writes the files you own (`pipeline.toml`,
release-please config, CODEOWNERS, Dependabot, the PR template) only when they are missing,
and touches nothing else. CI installs a pinned picard through the repository variables
`PICARD_REPOSITORY` and `PICARD_REF` and the secret `PICARD_TOKEN`.
Checklist: [docs/adoption.md](docs/adoption.md).

## Layout

This repository:

```
src/picard/                         the planner (Python 3.11+, standard library only)
  graph/providers/                    read the package graph: uv, npm, cargo, go, command
  graph/engines/                      answer "what is affected": native, pants, command
  executors/                          build suite commands: pytest, go-test, cargo-test, pnpm, ...
  scaffold.py                         picard init / update / install-hooks
  templates/                          what `picard init` writes into a repo
tests/, docs/                       tests and documentation
```

A repo after `picard init`:

```
.github/workflows/pipeline-*.yml    one workflow per tier, plus the rc tag
.github/actions/pipeline-*/         composite actions: plan, check-title, run-suite, extra, setup-picard
.github/release-please/             release-please config and manifest
.pipeline/pipeline.toml             the one file a repo edits
.pipeline/hooks/                    commit-msg, pre-push
.pipeline/templates/pytest/         marker plugin to copy into your test root
```

Why a package and not vendored tooling: [ADR 0006](docs/adr/0006-picard-is-a-python-package.md).

## CLI

Run from the repo root. Nothing to install.

```sh
picard simulate --local                  # what would CI do for everything I have locally?
picard simulate --files a.py b.py        # ... for a change I have not made
picard simulate --local --tiers 0,1,2 --run   # run those tiers for real
picard changed                           # what differs locally, and against which branch
picard graph --files packages/x/y.py     # the dependency graph with the impact marked
picard plan --tier 1 --base origin/main --title "feat(api): add x (ABC-1)" --format text
picard run --tier 0 --local              # what the pre-push hook does
picard check-title "fix: handle nil (ABC-2)"
picard validate-config
picard doctor                            # is what the impact engine needs installed? (exit 1 if not)
picard doctor --how-to-install           # ... and print the install steps
picard setup                             # prepare the repo for the impact engine (no-op for native)
picard setup --check                     # CI check: is that setup complete? exit 2 if not
picard simulate --debug --files a.py     # --debug on any command traces what the engines return
```

Local runs consider commits since the branch yours grew out of plus uncommitted work.
Details: [docs/simulation.md](docs/simulation.md).

## Monorepos and other languages

Packages and the dependency graph are **discovered**, so a change in a library runs the tests
of everything that depends on it, transitively. No hand-kept graph to drift.

| Provider | Reads |
|---|---|
| `uv` | `uv.lock` (uv workspaces) |
| `npm` | `package.json` workspaces and `pnpm-workspace.yaml` (npm, pnpm, yarn) |
| `cargo` | the Cargo workspace and its path dependencies |
| `go` | `go.work`, or every `go.mod` |
| `command` | any command that prints the graph as JSON (Bazel, Gradle, Nx, ...) |

An **impact engine** then maps changed files to packages and walks the dependents. The default,
`native`, walks the discovered graph and needs nothing installed. `pants` uses Pants for
import-level precision in the languages it understands and hands every other file to `native`.
`command` asks any external tool. Pick one in `[impact]`, and run `picard setup` to let the
engine prepare the repo. Nothing locks you into a build system.
See [ADR 0003](docs/adr/0003-graph-from-ecosystem-manifests.md),
[ADR 0005](docs/adr/0005-plugin-architecture.md) and
[configuration](docs/configuration.md#discover).

## Pants is optional

picard works with nothing but Python and git: the default `native` engine walks the graph it
reads from your manifests. To use Pants for import-level precision, set `engine = "pants"`
under `[impact]`. picard then checks that Pants is installed. If it is not, picard **warns on
stderr and falls back to the native engine**, so a plan is still correct. To see how to
install it, ask:

```sh
picard doctor                    # warning: Pants is not installed ... (exit 1)
picard doctor --how-to-install   # Homebrew and installer commands, and how to go back to native
```

## Develop picard

```sh
uv sync                  # picard editable plus ruff and pytest
just check               # ruff, tests, starter-config validation, actionlint
uv run pytest            # tests only
```

## Before relying on it

- Image pins in `.github/actions/pipeline-extra/action.yml` (gitleaks, semgrep, actionlint) and
  the `@v5`/`@v4` action tags were written without network access. Check each exists and pin
  third-party actions to commit SHAs. If your org already runs semgrep or
  gitleaks elsewhere, drop that extra from the profiles.
- This repository's own `.github/workflows/ci.yml` also uses action tags (`checkout`, `setup-uv`)
  that were not checked against the registry. Pin them to commit SHAs too.
- Never put a PR title or body into a `run:` step with `${{ }}`. Pass it through `env:`, as the
  actions here do (see [docs/architecture.md](docs/architecture.md#security-model)).
