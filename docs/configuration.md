# pipeline.toml reference

The file is `.pipeline/pipeline.toml`. Validate after every edit, from the repo root:

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
`[[discover]]` and `[[packages]]`. See [architecture](architecture.md#impact-and-engines).

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
paths   = [".pipeline/plugins/bazel.py"]   # loaded from a file in the repository
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

The code graph says which packages a code change affects. Configs, schemas, database
definitions, datasets, baselines, docs and automation have no import graph, so **rules** say
what must run when those files change. Rules complement the graph; they do not replace it.

```toml
[rules_policy]
unmapped = "full"             # ignore | warn | full: a file no rule and no package covers

[inventory.model]             # resolves the captured {model} to an id and a status
file = "models/inventory.toml"
entries = "model"             # the [[model]] tables in that file
runnable = ["trainable"]      # statuses that may run actions using {model}

[actions.train-model]         # a named command; never takes a file list
run = "python -m train --data models/{model_dir}/data.csv --out build/{model}.json"
tiers = [2]                   # optional: overrides the rule's tiers for this action
needs = ["trainable"]         # optional: inventory statuses this action requires

[[rules]]
name = "model dataset"
match = ["models/{model}/data/**"]       # {model} captures one path segment
actions = ["run:test-model", "run:train-model", "require-review"]
tiers = [0, 1, 2]             # optional, default [0, 1, 2]
profile = "full"              # optional: the profile these files demand (a docs rule lowers it)
```

**Resolution**

1. Each changed file uses the **first** rule whose `match` fits, top to bottom.
2. The results of all files are **merged**; identical actions run once.
3. A file a rule matches reaches the code graph only if the rule says `package` or `own`, so a
   dataset or YAML change does not run unit suites.
4. A file no rule matches goes to the code graph, as before.
5. `unmapped` decides what happens to a file that no rule, package, docs glob or global glob
   covers: `full` runs everything (recommended), `warn` only warns, `ignore` does nothing.
6. `full` absorbs everything.

**Actions**

| Action | Meaning |
|---|---|
| `none` | nothing; combine with `profile = "docs"` for documentation |
| `full` | every package |
| `package` | the owning package and everything that depends on it (the graph) |
| `own` | the owning package only |
| `require-review` | recorded in the plan and shown as `REVIEW REQUIRED` |
| `run:<name>` | the command of `[actions.<name>]`, with captures filled in, as one suite |

`{markers}` is available in an action's command. `{package}` and `{path}` are not (they belong
to package commands). A capture `{model}` becomes the inventory id when an inventory with that
name exists, and `{model_dir}` is always the directory name.

**Safety.** Captured names end up in shell commands and come from paths a PR controls, so they
must match `[A-Za-z0-9._-]+`. Anything else, a name missing from the inventory, or an
unreadable inventory runs **everything** and warns, and is never turned into a command. An
action whose inventory status is not allowed is skipped **with a warning naming the status**.

**Large pull requests.** Rules are matched inside the planner, so no file list goes through a
command line or an environment variable. The plan lists at most 200 changed files plus the
total (`changed_files_total`), because one string over 128 KiB fails at process start. Use
`--files-from FILE` (NUL or newline separated, or `-` for stdin) to simulate thousands of paths.

**`require-review`.** Today it is data: the plan lists it and `simulate` prints it. Blocking a
merge until a person approves needs a CI decision (a required label, CODEOWNERS or an
environment approval) that is not made yet.
