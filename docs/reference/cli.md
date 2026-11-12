# Command line

```
picard [--version] <command> [options]
```

Run from the repository root. `picard <command> --help` is always the source of truth for flags.

## Choosing the changed files

`plan`, `run`, `simulate` and `graph` share these options. The first that matches wins.

| Option | Changed files |
|---|---|
| `--files PATH [PATH ...]` | exactly these paths, no git needed (commas also separate) |
| `--files-from FILE` | like `--files`, read from `FILE` or `-` (stdin), NUL or newline separated. Use it for thousands of paths |
| `--pr [N ...]` | the files, title, body and labels of pull request(s), merged, closed or open. With no number, pick from a list. Needs `gh`. Not with `--files`, `--staged`, `--working`, `--base` or `--local` |
| `--pr-limit N` | with `--pr`: how many recent pull requests to list (default 30) |
| `--staged` | the git index |
| `--working` | every uncommitted change: staged, unstaged, untracked |
| `--base REF`, `--head REF` | `git diff REF...HEAD` (CI uses the PR base commit) |
| `--local` | everything that differs locally: commits since the branch yours grew out of, plus uncommitted work |
| `--against REF` | with `--local`: `origin-branch` (default), `upstream`, or any ref |
| `--no-working` | with `--local`: committed changes only |

And, for all of them:

| Option | Meaning |
|---|---|
| `--title TEXT` | PR title or commit subject. Repeatable. An explicit title wins over one read from a PR |
| `--body TEXT` | PR body, scanned for a `BREAKING CHANGE:` footer |
| `--config PATH` | the `pipeline.toml` to use (default `.picard/pipeline.toml`) |
| `--debug` | trace what the impact engines return, on stderr (same as `PICARD_DEBUG=1`) |
| `--color auto\|always\|never` | colour for text output |

Details and examples: [simulation.md](simulation.md#where-the-changed-files-come-from).

## Commands

### `plan`

```sh
picard plan --tier 1 --base origin/main --title "feat(api): add x (ABC-1)" --format text
```

Computes one tier's plan ([plan.md](plan.md)).

| Option | Meaning |
|---|---|
| `--tier N` | required. The tier to plan |
| `--format json\|text` | `json` is what workflows read (default) |
| `--github-output FILE` | append the plan's outputs to this `GITHUB_OUTPUT` file |
| `--summary FILE` | append a markdown summary (the job summary) |

### `run`

```sh
picard run --tier 0 --local            # what the pre-push hook does
picard run --tier 1 --local --dry-run  # print the commands, run nothing
```

Plans a tier, then runs its suites in order from the repo root and stops at the first failure
(`picard: <suite> failed with exit code N`). `--dry-run` only prints.

### `simulate`

```sh
picard simulate --local --tiers 0,1,2 --commands
picard simulate --files a.py --title "fix: x (ABC-1)" --run
```

Shows what every tier would do for a change: the changed files, the verdict, the dependency
graph with the impact marked, why, and a table of tiers.

| Option | Meaning |
|---|---|
| `--tiers 0,1,2` | which tiers (default `0,1,2,3,4`) |
| `--commands` | also print every command each tier would run |
| `--run` | execute the selected tiers in order, the way CI would |
| `--format text\|json\|mermaid` | `json` for scripts, `mermaid` for the graph |

### `graph`

```sh
picard graph
picard graph --files libs/core/core/x.py --format mermaid
```

The dependency graph, with an impact marked when files are given. Formats: `text`, `mermaid`,
`json`.

### `changed`

```sh
picard changed [--against REF] [--no-working] [--format text|json]
```

What differs locally and against which branch (and why that branch was chosen).

### `check-title`

```sh
picard check-title "fix: handle nil (ABC-2)"
picard check-title --file .git/COMMIT_EDITMSG --no-ticket     # the commit-msg hook
```

Validates a title with the parser the plan uses. `--body` adds a body to scan for a breaking
footer, `--file` reads the first line of a file, `--no-ticket` skips the ticket-ID rule.

### `validate-config`

```sh
picard validate-config [--config PATH]
```

Lints `pipeline.toml`. Unknown profiles, extras, release values, package references,
dependency cycles, unsafe characters and bad regexes fail with a message naming the key.

### `init`, `update`, `install-hooks`

```sh
picard init            # scaffold .github/ and .picard/ into the repo
picard update          # refresh the files picard owns after upgrading picard
picard update --dry-run
picard install-hooks   # once per clone: git hooks for commit messages and pre-push
```

`--root DIR` sets the repository root (default `.`). See
[github-integration.md](github-integration.md#what-init-writes).

### `doctor` and `setup`

```sh
picard doctor                    # is what the impact engine needs installed? exit 1 if not
picard doctor --how-to-install   # print the install steps
picard setup                     # let the engine prepare the repo (no-op for native)
picard setup --check             # verify it is complete: exit 2 if not (what CI runs)
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | `doctor`: the engine is not installed. `check-title`: the title is invalid |
| the suite's own code | `run` and `simulate --run` stop at the first failing suite and exit with its exit code |
| 2 | a configuration, git, graph, setup, scaffold or `gh` error (`picard: <message>` on stderr), or `setup --check` found the setup missing or stale |

## Environment variables

| Variable | Meaning |
|---|---|
| `PICARD_TITLE`, `PICARD_BODY` | the PR title and body for `plan`, `simulate` and `check-title` when `--title` and `--body` are not given. The workflows pass them this way so untrusted text never reaches a shell. `SMARTPLAN_TITLE` and `SMARTPLAN_BODY` still work, from before the rename |
| `PICARD_DEBUG=1` | same as `--debug`: trace the engines on stderr |
| `PICARD_REQUIRE_MARKERS=1` | read by the pytest plugin in `.picard/templates/pytest/conftest.py`: unmarked tests fail collection |

In CI, the workflows install picard from the repository variables `PICARD_REPOSITORY` and
`PICARD_REF` and the secret `PICARD_TOKEN` ([github-integration.md](github-integration.md)).
