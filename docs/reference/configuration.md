# `pipeline.toml` reference

The file is `.picard/pipeline.toml`. Validate after every edit, from the repo root:

```sh
picard validate-config
```

Validation is strict: unknown profiles, extras, release values, package references,
dependency cycles, unsafe characters and bad regexes fail with a message naming the key.

## `[runtime]`

Where and how suites run in CI.

| Key | Default | Meaning |
|---|---|---|
| `runs_on` | `ubuntu-24.04` | Runner label for suite jobs. A single label, e.g. a self-hosted pool. |
| `python_version` | `""` | Installs Python with `setup-python` when set. |
| `node_version` | `""` | Installs Node with `setup-node` when set. |
| `setup_command` | `""` | Run before every suite, from the repo root. Install dependencies here. |

## `[commits]`

| Key | Meaning |
|---|---|
| `types` | Allowed Conventional Commit types. Every type needs a `[types.<name>]` table. |
| `require_ticket` | Require a ticket ID in parentheses at the end of the title. |
| `ticket_exempt` | Regexes. A title matching one skips the ticket rule (release PRs, Dependabot). |
| `fallback_profile` | Profile for a title with no known type. Default `full`. |

## `[types.<name>]`

```toml
[types.feat]
profile = "full"      # a name from [profiles]
release = "minor"     # none | patch | minor | major, what release-please will do
```

## `[[overrides]]`

Replace the type's profile (and optionally release) for titles matching a regex. First match
wins.

```toml
[[overrides]]
match = '^(chore|build)\((deps|deps-dev)\)'
profile = "deps"
```

## `[breaking]`

Applies when the title has `!` or the body has a `BREAKING CHANGE:` footer.

| Key | Meaning |
|---|---|
| `profile` | Minimum profile. |
| `release` | Release effect. Use `minor` while below 1.0. |
| `tiers` | Tiers forced on even if the profile would skip them. |
| `extras` | Extra jobs added at those tiers. |

## `[profiles.<name>]`

| Key | Meaning |
|---|---|
| `rank` | Higher is stricter. The strongest profile among type, override, breaking and paths wins. |
| `run_suites` | `false` runs no test suites. Extras still run. Default `true`. |
| `tiers` | PR tiers this profile applies to (0, 1, 2). A profile without 2 skips the baseline. |
| `extras.<N>` | Extra jobs enabled at tier N. |

Extras: `secrets` (gitleaks), `workflow-lint` (actionlint), `semgrep`, `dependency-review`
(PRs only), `link-check` (local links), `breaking-change-checks` (migration section and
footer). The list is enforced by a test against `pipeline-extra`.

## `[paths]`

| Key | Meaning |
|---|---|
| `docs` | Globs that demand only the lowest profile. |
| `default` | Profile demanded by a file that matches nothing else. |
| `global` | Globs that make every package affected (lockfiles, the config, workflows). |
| `[[paths.floor]]` | `globs` + `profile`: the minimum profile for matching files. |

Glob syntax: `*` within a segment, `**` across segments, `?` one character.

## `[[discover]]`

Packages and the dependency graph, read from the package managers instead of listed by hand.
One block per ecosystem; a package found by two providers is an error.

```toml
[[discover]]
provider = "uv"            # uv workspace: members and edges come from uv.lock
[discover.commands]
lint = "uv run --no-sync ruff check {path}"
test = 'uv run --no-sync pytest {path} -m "{markers}"'

[[discover]]
provider = "npm"           # npm, pnpm or yarn workspaces: package.json / pnpm-workspace.yaml
[discover.commands]
test = "pnpm --filter {package} run test"
"test@pr-baseline" = "pnpm --filter {package} run test:baseline"
```

| Provider | Reads | Edges |
|---|---|---|
| `uv` | `uv.lock` (run `uv lock` and commit it) | `dependencies`, `optional-dependencies` and `dev-dependencies` that name another workspace member |
| `npm` | `workspaces` in `package.json`, or `packages:` in `pnpm-workspace.yaml` | `dependencies`, `devDependencies`, `peerDependencies` and `optionalDependencies` that name another workspace member (`workspace:*` or a plain range) |
| `cargo` | the `[workspace]` of the root `Cargo.toml` | `dependencies`, `dev-dependencies` and `build-dependencies` (also per target) that name another member, honouring `package = "real-name"` renames |
| `go` | `go.work`, or every `go.mod` when there is none | `require` and `replace` directives that name another module |
| `command` | the JSON a command prints (below) | whatever the command says |

The `command` provider is the escape hatch for Bazel, Gradle, Maven, Nx or anything else that
can describe its workspace, without writing a plugin. The command runs from the repo root and
prints `{"packages": [{"name": "a", "path": "libs/a", "depends_on": ["b"]}]}`:

```toml
[[discover]]
provider = "command"
command  = "python3 tools/print_graph.py"
```

Any other key in a `[[discover]]` block is an option of its provider. `executor` names how to
build the suite commands for every package the block finds (see `[executors]` below), so a
block need not spell them out. A command in `commands` always wins over the executor.

External dependencies are ignored; only edges between workspace members matter. Package names
are checked against `[@A-Za-z0-9._/-]+` because they are substituted into commands.

A command key `name@<tier name>` overrides `name` for that tier, and an empty value skips it
there. Use it for a baseline suite that differs from the default test command.

## `[impact]`

Which engine answers "what does this change affect". The graph itself always comes from
`[[discover]]` and `[[packages]]`. See [architecture](../dev/architecture.md#impact-and-engines).

```toml
[impact]
engine   = "pants"     # native (default) | pants | command | a plugin's engine
command  = "pants"     # pants: the launcher. command engine: the program to ask
fallback = "native"    # pants: who answers for files Pants does not own
debug    = false       # trace what the engines return on stderr (or --debug, PICARD_DEBUG=1)
```

| Engine | Answers with | Needs |
|---|---|---|
| `native` | the discovered graph: owner of each file, then its dependents | nothing |
| `pants` | `pants list` and `pants dependents --transitive` (import-level for Python). Files Pants does not own go to `fallback`. | Pants on `PATH`, and `picard setup` |
| `command` | the JSON an external command prints for the changed files, which arrive on stdin separated by NUL | your command |

An engine can make an answer wider but never narrower than the truth: if it fails, times out or
does not own a file, the fallback answers and the plan carries a note. A global file
(`[paths] global`) affects every package whatever the engine says.

`picard doctor` checks that what the engine needs is installed and `picard doctor --how-to-install`
prints the install steps. If Pants is configured and missing, picard warns on stderr once, answers
with the fallback engine and adds a note to the plan. `picard setup` prepares the repo for the
engine and `picard setup --check` fails (exit 2) when
that setup is missing or stale, which is what CI runs. `native` needs no setup. The `pants`
engine writes `pants.toml` and the build files it needs, leaves an existing `pants.toml` alone
and is idempotent. Run any command with `--debug` to see what Pants and the native engine each
returned for the same files and where they differ.

## `[executors.<name>]`

An executor builds the command for a suite (`lint`, `test`, ...) from the package and the
tier's markers, so a package need not spell its commands out. Select one per `[[discover]]`
block or per `[[packages]]` entry with `executor = "<name>"`.

| Executor | Runs | Options |
|---|---|---|
| `pytest` | `pytest <path> -m "<markers>"` | `launcher`, `linter` |
| `uv-pytest` | the same through `uv run --no-sync` | `launcher`, `linter` |
| `go-test` | `go test ./...` and `go vet ./...` in the module | |
| `cargo-test` | `cargo test` and `cargo check` for the crate | |
| `pnpm` | `pnpm --filter <package> run test` (`test:baseline` at tier 2) | |
| `npm` | `npm run <suite> --workspace <package>` | |
| `make` | `make -C <path> <suite>` | `prefix` for target names |

```toml
[[discover]]
provider = "cargo"
executor = "cargo-test"

[executors.pytest]
linter = "ruff check"
```

## `[plugins]`

Add providers, engines or executors without changing picard. A plugin is a Python module with
a `register(registries)` function.

```toml
[plugins]
modules = ["acme_picard"]                  # importable from the environment
paths   = [".picard/plugins/bazel.py"]   # loaded from a file in the repository
```

Plugins come from the repo's own configuration, so they carry the same trust as its workflows:
review an edit to them the same way.

## `[[packages]]`

Declares a package by hand, or **merges over a discovered one by name**: `baseline`, extra
`depends_on` edges (for dependencies the manifests cannot express) and `commands` are
added to what was discovered.

```toml
[[packages]]
name = "api"
path = "apps/api"          # "." for a single-package repo
depends_on = ["core"]      # changes in core also affect api
baseline = true            # always part of tier 2
[packages.commands]
lint = "ruff check {path}"
test = 'pytest {path} -m "{markers}"'
```

Command placeholders: `{package}`, `{path}`, `{markers}`. Commands run from the repo root
through `bash -euo pipefail -c`. Only the names a tier lists in `run` execute.

## `[[path_rules]]`

```toml
[[path_rules]]
globs = ["db/migrations/**"]
packages = ["worker"]      # tier 2 also runs these when the globs match
```

## `[tiers.<N>]`

| Key | Values |
|---|---|
| `name` | Label used in the job summary. |
| `compare` | `merge-base`, `pr-base`, `none` |
| `packages` | `affected`, `baseline`, `all` |
| `markers` | Words, spaces and parentheses only. Empty means no filter. |
| `run` | Command names from `[packages.commands]`. |
| `extras` | Extra jobs for tiers 3 and 4, which ignore profiles. |

## `[[rules]]`, `[actions.*]`, `[inventory.*]`, `[rules_policy]`

Rules say what must run when files with no import graph change. They have their own page:
[rules.md](rules.md).
