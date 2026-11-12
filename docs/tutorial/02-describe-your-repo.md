# 2. Describe your repository

picard needs three facts about your repository: **what the packages are**, **what depends on
what**, and **how to lint and test each one**. The first two are read from your package
manager, so there is no hand-kept list to drift.

## Discover the packages

Add one `[[discover]]` block per ecosystem:

```toml
[[discover]]
provider = "uv"                 # uv workspace: members and edges come from uv.lock
[discover.commands]
lint = "uv run --no-sync ruff check {path}"
test = 'uv run --no-sync python -m pytest {path} -m "{markers}" --no-cov -q'
```

| Provider | Reads |
|---|---|
| `uv` | `uv.lock` (run `uv lock` and commit it) |
| `npm` | `package.json` workspaces, `pnpm-workspace.yaml` |
| `cargo` | the Cargo workspace and its path dependencies |
| `go` | `go.work`, or every `go.mod` |
| `command` | any command that prints the graph as JSON (Bazel, Gradle, Nx, ...) |

Placeholders in a command: `{package}`, `{path}` and `{markers}`. Commands run from the repo
root through `bash -euo pipefail -c`.

## See the graph

```sh
picard graph
```

```
○ etl  projects/etl  [uv]
├─ ○ foundation  libs/foundation  [uv]
└─ ○ storage     libs/storage     [uv]
```

Read an indented line as "depends on". Check that every package and edge you expect is there.
If an edge lives in no manifest (a service that consumes another's schema file), declare it:

```toml
[[packages]]
name = "web"
depends_on = ["api"]            # a change in api also affects web
```

`[[packages]]` merges over a discovered package by name, so you only write what differs.

## Give a package a different command

Some packages need their own commands, or a different command at one tier. A command key
`name@<tier name>` overrides `name` for that tier, and an empty value skips it there:

```toml
[[packages]]
name = "etl"
[packages.commands]
"test@pr-baseline" = "make baseline-check-all"   # tier 2 runs this instead of pytest

[[discover]]
provider = "uv"
[discover.commands]
"test@pr-baseline" = ""                          # no other package runs anything at tier 2
```

Only the names a tier lists in its `run` execute (`run = ["lint", "test"]`).

## Lint without breaking the build

While a repository still has findings, make the `lint` command report them and exit 0. You see
the problems in every plan and nothing is blocked until they are cleaned up:

```toml
[discover.commands]
lint = '''rc=0
black --check {path} || rc=1
ruff check {path} || rc=1
if [ "$rc" -ne 0 ]; then echo "lint: findings above are report-only"; fi
exit 0'''
```

Switch to `exit $rc` once the backlog is gone.

## Choose the impact engine (optional)

The default `native` engine walks the graph you just printed and needs nothing installed.
`pants` adds import-level precision for Python. You can start with `native` and switch later
with `[impact] engine = "pants"` and `picard setup`. See
[configuration](../reference/configuration.md#impact).

## Check it

```sh
picard validate-config
picard graph --files libs/foundation/foundation/__init__.py
```

The second command marks the changed package and everything above it. If a dependent is
missing, an edge is missing.

**Next:** [preview a change](03-preview-a-change.md).
