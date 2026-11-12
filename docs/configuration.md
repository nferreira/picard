# pipeline.toml reference

The file is `.picard/pipeline.toml`. Validate after every edit, from the repo root:

```sh
picard validate-config
```

Validation is strict: unknown profiles, extras, release values, package references,
dependency cycles (unless `[graph] cycles = "allow"`), unsafe characters and bad regexes fail with a
message naming the key.

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
| `global` | Globs that make every package affected (lockfiles, the config, workflows). A lockfile also listed under `[lockfiles] narrow` is narrowed instead. |
| `[[paths.floor]]` | `globs` + `profile`: the minimum profile for matching files. |

Glob syntax: `*` within a segment, `**` across segments, `?` one character.

## `[lockfiles]`

| Key | Default | Meaning |
|---|---|---|
| `narrow` | `[]` | Globs of lockfiles picard attributes to the packages a change reaches, instead of treating them as global. Each glob must end in `uv.lock`, `Cargo.lock` or `package-lock.json`; `pnpm-lock.yaml` and `yarn.lock` are rejected and stay global. |

```toml
[lockfiles]
narrow = ["uv.lock", "**/package-lock.json"]
```

Listing a lockfile is the opt-in: a file that matches `narrow` is handled here even when it
also matches `[paths] global`. A listed lockfile counts as covered for
`[rules_policy] unmapped`. A lockfile change that cannot be attributed to packages runs every
package, never none.

picard reads the lockfile at the merge base and at the head (the working tree for a file that
is uncommitted under `--local`), finds the external packages whose resolved version, source or
dependencies changed, and follows the lockfile's own edges to the workspace members that use
them. Those members are changed packages (`via lockfile`), and their dependents follow as usual.
The plan says what it found:

```text
warning: uv.lock changed httpx 0.27.0 -> 0.28.1, sniffio 1.3.1 -> none: reaches 2 of 3 packages (api, core)
```

Members map to packages by name for `uv.lock` and `Cargo.lock`, and by path for
`package-lock.json` (the `""` entry is the package at the lockfile's directory). uv and Cargo
members are matched only to packages discovered by that provider (`[[discover]] provider = "uv"`
or `"cargo"`, including a `[[packages]]` entry merged over one); a package of the same name from
anywhere else runs everything. The change runs everything, with a warning that gives the reason,
when:

- the change has no commit range: `--files`, `--files-from`, `--staged` and `--working` (use
  `--base` or `--local`);
- the lockfile is new or deleted, cannot be read at either revision, or does not parse;
- a member it reaches is not a picard package, including the uv workspace root, so bumping a
  root dev tool such as a linter runs everything;
- a changed package reaches no member, or only hashes or metadata changed.

## `[labels]`

| Key | Default | Meaning |
|---|---|---|
| `full` | `"ci:full"` | A PR label that forces a full run. `""` disables it. No commas, at most 50 characters. |

On a tier that compares against a diff (`compare` is not `none`), the label makes every package
affected, raises the profile to the strictest one (highest `rank`) and enables its tiers, so
tier 2 runs too, even when no file changed. The reason is in the plan's `reasons` and
`forced_full`. Labels can only raise the bar. Tiers 3 and 4 already run everything and ignore
it.

Labels reach picard as `--label NAME` (repeatable) or, when no `--label` is given, the
`PICARD_LABELS` environment variable: a JSON array of names (what `toJSON()` gives in a
workflow), or a comma-separated list. Pass them through `env:`, never interpolated into a shell.
Matching is case-sensitive: the label must be spelled exactly as in `[labels] full`, so `CI:Full`
does not force anything when `full = "ci:full"`. The PR workflow passes the labels and re-plans
when a label is added or removed (`labeled`, `unlabeled`).

## `[graph]`

What picard does with a dependency cycle between packages. Optional.

```toml
[graph]
cycles = "allow"   # "error" (default): refuse a cycle. "allow": accept it.
```

| Key | Default | Meaning |
|---|---|---|
| `cycles` | `"error"` | `"error"` fails `validate-config` and every command on a cycle, naming it. `"allow"` accepts cycles. |

**Only runtime edges make a cycle.** A package that lists a sibling in a dev or test group while
that sibling depends on it for real is not a cycle: the edge from the first package to the sibling
is not something it needs to run. Providers mark such edges as dev-only (`dev-dependencies` in uv,
`devDependencies` in npm, `[dev-dependencies]` in Cargo, including per target). They stay in the
graph, because the first package's tests do need the sibling, so a change to the sibling still
selects it. They are not counted when looking for cycles. A name that is also a runtime dependency
(or, in uv, an extra), and an edge you declare by hand in `[[packages]] depends_on`, is a runtime
edge. `picard graph` and the impact walk see every edge either way.

A real runtime cycle is still an error unless you set `cycles = "allow"`. With `"allow"` every
edge is kept: the members of a cycle depend on one another, so a change to one affects all of them
and everything that depends on any of them. `validate-config` reports each accepted cycle on
stderr (`picard: note: 1 dependency cycle(s) accepted ... a -> b -> a`) so it is never silent, and
`picard graph` marks the way back with `↺ name  (cycle)` instead of descending again. A repository
without a `[graph]` table behaves as before.

## `[git]`

| Key | Default | Meaning |
|---|---|---|
| `unknown_base` | `"full"` | What `--base` does when the base commit is not in the clone and cannot be fetched: `full` runs every package and warns, `fail` stops with exit 2. |

When the `--base` commit, or the history that joins it to `--head`, is missing (a shallow CI
clone, a branch that was never fetched), picard tries one `git fetch --no-tags origin <base>`,
with `--unshallow` in a shallow clone and only when a remote `origin` exists. If the base is
still unknown:

- `full` prints two warnings on stderr and plans as if every package were affected, at the
  strictest profile (the reason is in the plan's `forced_full`). Without the base there is no
  diff, so nothing can be narrowed:

  ```
  picard: warning: base 0123456789abcdef0123456789abcdef01234567 is not in this shallow clone and could not be fetched
  picard: warning: running every package instead (set [git] unknown_base = "fail" to stop here)
  ```

- `fail` stops with `picard: base ... could not be fetched` and exit status 2.

An abbreviated SHA can never be fetched, so in a clone that lacks it the base is unknown and
`full` runs everything: pass full SHAs, as the scaffolded workflows do. A `--head` that is not a
commit is a typo, not a missing base: it always stops with exit 2. Tiers with
`compare = "none"` never read the base.

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
`test_paths` (see `[[packages]]`) applies to every package the block finds, and is not a
provider option.

External dependencies are ignored; only edges between workspace members matter. Package names
are checked against `[@A-Za-z0-9._/-]+` because they are substituted into commands.

A command key `name@<tier name>` overrides `name` for that tier, and an empty value skips it
there. Use it for a baseline suite that differs from the default test command.

## `[impact]`

Which engine answers "what does this change affect". The graph itself always comes from
`[[discover]]` and `[[packages]]`. See [architecture](architecture.md#impact-and-engines).

```toml
[impact]
engine   = "pants"     # native (default) | pants | command | nx | turbo | moon | bazel-diff | a plugin's engine
command  = "pants"     # pants, nx, turbo, moon, bazel-diff: the launcher (default the tool's name). command engine: the program to ask
fallback = "native"    # who answers for files the tool does not own, and when it fails
debug    = false       # trace what the engines return on stderr (or --debug, PICARD_DEBUG=1)
timeout  = 600         # nx, turbo, moon, bazel-diff: seconds for each call (bazel-diff default 1800)
bazel     = "bazel"    # bazel-diff only: the Bazel or Bazelisk binary it runs
workspace = "."        # bazel-diff only: the Bazel workspace, relative to the repository root
```

| Engine | Answers with | Needs |
|---|---|---|
| `native` | the discovered graph: owner of each file, then its dependents | nothing |
| `pants` | `pants list` and `pants dependents --transitive` (import-level for Python). Files Pants does not own go to `fallback`. | Pants on `PATH`, and `picard setup` |
| `command` | the JSON an external command prints for the changed files, which arrive on stdin separated by NUL | your command |
| `nx` | `nx show projects --affected --base --head --json` with a git range and nothing uncommitted, otherwise `--files=<the files Nx owns>` (over 100,000 characters: the fallback answers). Projects match packages by name. | Nx (`command = "npx nx"` for a repo-local one) |
| `turbo` | `turbo ls --affected --output=json` with `TURBO_SCM_BASE` and `TURBO_SCM_HEAD` set. Packages match by name. Needs a git range (`--base`, `--local`): with `--files` the fallback answers. | Turborepo |
| `moon` | `moon query projects --affected --downstream deep`, the files moon owns on stdin as `{"files": [...], "options": {}}`. Projects match packages by `source` directory. | moon |
| `bazel-diff` | `generate-hashes` for the base (in a temporary `git worktree`, always removed) and the working tree, then `get-impacted-targets`. `//a/b:c` belongs to the package that owns `a/b`, `@external` targets are ignored. Needs a git range whose head is the checked-out `HEAD`. Packages with a `BUILD` or `BUILD.bazel` at their root are the ones it answers for. | bazel-diff and Bazel |

The four tool engines share one policy. Files owned by a package the tool knows go to the tool,
the rest to `fallback`. Uncommitted files (`--local`) also go to `fallback`. The tool's answer is
intersected with picard's packages, then the owners of the files it was given and all their
dependents from picard's graph are added, so a tool that reports only the changed projects still
runs their dependents. A missing tool, a non-zero exit, a timeout, an invalid `timeout` or output
of an unexpected shape hands the whole question to `fallback`, with a note such as
`nx failed (...); the native engine answered`. The turbo and moon JSON shapes, and moon's stdin
input, are not documented upstream, which is why anything unexpected counts as a failure.

An engine can make an answer wider but never narrower than the truth: if it fails, times out or
does not own a file, the fallback answers and the plan carries a note. A global file
(`[paths] global`) affects every package whatever the engine says.

`picard doctor` checks that what the engine needs is installed and `picard doctor --how-to-install`
prints the install steps. If the engine's tool (Pants, Nx, Turborepo, moon, bazel-diff) is
configured and missing, picard warns on stderr once, answers with the fallback engine and adds a
note to the plan. `picard setup` prepares the repo for the engine and `picard setup --check`
fails (exit 2) when that setup is missing or stale, which is what CI runs. `native` needs no setup. The `pants`
engine writes `pants.toml` and the build files it needs, leaves an existing `pants.toml` alone
and is idempotent. Run any command with `--debug` to see what Pants and the native engine each
returned for the same files and where they differ.

## `[executors.<name>]`

An executor builds the command for a suite (`lint`, `test`, ...) from the package and the
tier's markers, so a package need not spell its commands out. Select one per `[[discover]]`
block or per `[[packages]]` entry with `executor = "<name>"`.

| Executor | Runs | Options |
|---|---|---|
| `pytest` | `pytest <path> -m "<markers>"`; `deps`: `deptry <path>` | `launcher`, `linter`, `deps_checker`, `retries`, `selection = "impacted"`; shards `test` |
| `uv-pytest` | the same through `uv run --no-sync` | as `pytest` |
| `go-test` | `go test ./...` and `go vet ./...` in the module | |
| `cargo-test` | `cargo test` and `cargo check` for the crate | |
| `pnpm` | `pnpm --filter <package> run test` (`test:baseline` at tier 2); `deps`: `pnpm exec knip ... --workspace <path>` | `deps_checker`, `selection = "changed-since"`; shards `test` |
| `npm` | `npm run <suite> --workspace <package>`; `deps`: `npx --no-install knip ... --workspace <path>` | `deps_checker`, `selection = "changed-since"`; shards `test` |
| `make` | `make -C <path> <suite>` | `prefix` for target names |
| `pants` | `pants lint <path>::` and `pants test <path>::` (`::` at the repo root) | `launcher` (default `pants`), `retries`, `markers` |
| `turbo` | `turbo run lint\|test --filter=<package>` | `launcher` (default `turbo`) |
| `nx` | `nx run <package>:lint\|test` | `launcher` (default `nx`) |

```toml
[[discover]]
provider = "cargo"
executor = "cargo-test"

[executors.pytest]
linter = "ruff check"
```

Options the built-in executors take on top of the table. Shards, retries and selection change
the `test` suite only. picard passes each tool's own flag and never splits, retries or selects
tests itself, so the repo installs the tool.

| Option | Executors | Adds |
|---|---|---|
| `shards` (on the package) | `pytest`, `uv-pytest` | ` --splits N --group k` (pytest-split) |
| | `pnpm`, `npm` | ` -- --shard=k/N` (Jest's flag: the `test` script must run Jest) |
| `retries` | `pytest`, `uv-pytest` | ` --reruns n` (pytest-rerunfailures). An integer from 0 to 10, default 0, for every tier that uses the executor. Jest has no CLI retry flag, so `pnpm` and `npm` reject `retries`: set retries in the test runner's own config. |
| `selection` | `pytest`, `uv-pytest`: `"impacted"` | ` --impacted --impacted-module=<module> --impacted-git-mode=branch --impacted-base-branch=<base>` (pytest-impacted). `<module>` is the package name with `-` and `.` replaced by `_`. |
| | `pnpm`, `npm`: `"changed-since"` | ` --changedSince=<base>` (Jest), after the same single `--` as the shard flag |
| `deps_checker` | `pytest`, `uv-pytest` | The `deps` suite: `<launcher> <deps_checker> <path>`, default `deptry` |
| | `pnpm`, `npm` | The `deps` suite through `pnpm exec` or `npx --no-install`, with `--workspace <path>`; default `knip --include dependencies,unlisted` |

`selection` takes effect only in a tier with `selection = true` and a committed git range (see
`selection` under `[tiers.<N>]`); anywhere else the command runs the whole suite. `<base>` is the
merge-base commit. The `deps` suite runs only where a tier lists `deps` in its `run`. A Vitest
repo writes its `test` command by hand, because Vitest's flags were not verified.

```toml
[executors.uv-pytest]
retries   = 2
selection = "impacted"

[executors.pnpm]
selection = "changed-since"

[tiers.1]
run       = ["lint", "test", "deps"]
selection = true
```

`pants`, `turbo` and `nx` hand the suite to the tool, so its local and remote cache and its
ordering between packages apply. picard still decides which packages run at each tier. They
build `lint` and `test` only. turbo and nx ignore the tier's markers, like `pnpm`: a task that
should filter by tier reads its own configuration. Only Pants shards (`--test-shard=k/N`, k
counted from 0, right after `test`) and retries (`retries = n` adds
`--test-attempts-default=n+1`).

Pants also ignores the markers unless `markers = true`, which appends `-- -m "<markers>"`.
Pants runs pytest once per test file, and a file whose tests the expression all deselects exits
with code 5, which Pants counts as a failure. A repo that sets `markers = true` must make exit
code 5 a success itself, with a `pytest_sessionfinish` hook in `conftest.py` or the
`pytest-custom-exit-code` plugin. picard ships neither. Ignoring the markers only runs more tests.

```toml
[[discover]]
provider = "uv"
executor = "pants"

[executors.pants]
launcher = "pants"
retries = 1         # --test-attempts-default=2
markers = false     # the default: the tier's markers are not passed to pytest
```

`picard validate-config` builds each `[executors.<name>]` once: a table for an executor nothing
registered, or an option it does not accept, fails there and names the table.

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

Declares a package by hand, or **merges over a discovered one by name**: extra `depends_on`
edges (for dependencies the manifests cannot express) and `commands` are added to what was
discovered, and `path`, `baseline`, `executor`, `always`, `shards` and `test_paths` replace it
when given. A merged package keeps its provider.

```toml
[[packages]]
name = "api"
path = "apps/api"          # "." for a single-package repo
depends_on = ["core"]      # changes in core also affect api
baseline = true            # always part of tier 2
always = false             # true: in the affected set of every tier that compares a diff
shards = 1                 # >1: a shardable `test` suite runs as this many matrix jobs
test_paths = ["tests/**"]  # globs relative to path that hold only tests
[packages.commands]
lint = "ruff check {path}"
test = 'pytest {path} -m "{markers}"'
```

| Key | Default | Meaning |
|---|---|---|
| `always` | `false` | `true` adds the package to the affected set of tiers 0 to 2 (`always = true` in the plan's why), for a package whose inputs no graph can see. It does not stop a tier that has no changed files from skipping. |
| `shards` | `1` | An integer from 1 to 256. The suites the package's executor can split, or an explicit command with a `{shard}` placeholder, become N jobs with ids `<package>:<suite>#k/N`. Anything else runs as one job, with a warning for `test`. `picard run` and `simulate --run` run each suite once, whole. |
| `test_paths` | `[]` | Globs relative to the package `path` that only hold test files. No `{captures}`. A `[[packages]]` entry's own `test_paths` replaces the one from `[[discover]]`. Under the native engine, a change whose files in the package all match affects the package and not its dependents; a mixed change, or a file that matches nothing, walks the dependents as before. |

Command placeholders: `{package}`, `{path}`, `{markers}`, and `{shard}` / `{shards}` (the
job's 1-based shard and the total; both `1` when the suite is not split). Commands run from the
repo root through `bash -euo pipefail -c`. Only the names a tier lists in `run` execute. A tier
over 256 jobs gets a warning: GitHub's matrix cannot hold more.

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
| `selection` | `true` lets executors select, inside a suite, the tests changed since the base commit. Default `false`, and not allowed with `compare = "none"`. Only with a committed git range (`--base`, or `--local` with no uncommitted files); otherwise whole suites run and the plan warns. A forced full run (`[labels] full`) never selects. |

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
