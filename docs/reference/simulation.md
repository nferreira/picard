# Running it locally and simulating changes

picard is an [affected-based](../dev/concepts.md) pipeline: what runs is a function of the change. So
you can ask what a change would do before you push it, or without making it at all. Every command runs from the repo root and
needs nothing installed (`picard`).

## Where the changed files come from

| Option | Changed files | Use it for |
|---|---|---|
| `--files a b,c` | exactly these paths, no git needed | "what if I changed a, b and c?" |
| `--staged` | the git index | a pre-commit check |
| `--working` | every uncommitted change: staged, unstaged, untracked | work in progress |
| `--local` | **everything that differs locally**: commits since the branch this one grew out of, plus uncommitted work | the normal local run |
| `--local --against upstream` | commits not pushed yet, plus uncommitted work | a pre-push check |
| `--local --against <ref>` | commits since any ref, plus uncommitted work | comparing with a release branch |
| `--local --no-working` | committed changes only | what a PR would contain |
| `--base REF` | `git diff REF...HEAD` (a missing base is fetched once, then [`[git] unknown_base`](configuration.md#git) decides) | CI: the PR base commit |
| `--pr [N ...]` | the files, title, body and labels of pull request(s), merged, closed or open (GitHub, read with `gh`); bare `--pr` lists recent ones to pick from | "what would CI do for that PR?" |

The first match wins. Without `--title`, a change with no commit yet is planned from its paths
alone: a source file demands `full`, a markdown file demands `docs`, and a title you add can
only raise that. `--label NAME` (repeatable; `PICARD_LABELS` when absent) adds PR labels, so
`--label ci:full` previews a forced full run.

### Simulating a pull request

`picard simulate --pr 123` plans a pull request the way CI does. A local run has your commits but
not the other inputs CI uses: the PR title (it decides the profile and the release), its body (a
`BREAKING CHANGE:` footer) and its labels (`ci:full`). `--pr` reads them, and the files the pull
request changed, with the GitHub CLI (`gh`, logged in), then plans every tier as `pipeline-pr` would.
The pull request can be merged, closed or still open.

```sh
picard simulate --pr 123                 # a pull request by number
picard simulate --pr 123 124             # several, planned together
picard simulate --pr                     # list recent pull requests (any state) and pick
picard simulate --pr 123 --label ci:full # what if it were labelled ci:full
picard simulate --pr 123 --title 'feat(x): y (ABC-1)'   # what if the title were this
picard simulate --pr --pr-limit 100      # list more of them
picard affected --pr 123                 # the packages it affects; plan, run and graph take it too
```

- Without a number, picard lists the 30 most recent pull requests (`--pr-limit` changes that) of any
  state, newest activity first, each with its day, state, title and branch. The checked-out branch's
  is starred and Enter takes it. Type one or several numbers (`707 699` or `707,#699`), blank or
  `q` cancels. A number that is not in the list is refused: raise `--pr-limit`, or name it directly.
  The list and the prompt go to stderr, so `--format json` stays clean. With no terminal (CI, a
  closed pipe) it stops and says to name the pull request; `echo 707 | picard simulate --pr` works.
- `--title` and `--body` **replace** the pull request's. `--label` **adds** to its labels.
- It cannot be combined with `--files`, `--files-from`, `--staged`, `--working`, `--base` or
  `--local`: the pull request says what changed.
- The file list comes from the platform (every page, a rename counts both paths). For one pull
  request whose commits are in the clone (the head is fetched from `origin` once, as
  `pull/<N>/head`) picard also uses them as a git range, so a lockfile change can be narrowed, but
  only when the range gives exactly the platform's file list. Several pull requests, a missing
  commit or any mismatch give no range, so a lockfile change runs every package: more, never less.
- Without `gh`, `--pr` stops and says to install it, or to describe the change with `--files`,
  `--title` and `--label`. picard itself needs only git; `gh` is used by this option alone.

### Several tiers in one go

`picard simulate --tiers 0,1,2` plans and (with `--run`) executes the tiers in order. A command that
an earlier tier of the same invocation already ran is **not run again**. It is skipped in a real run
(`== rule:x: skipped, already ran in tier 0`), and in a simulation (`--commands`) or a dry run it is
shown as one that would be skipped (`skip, already ran in tier 0: <command>`). The tier table counts
only the new work (`2 suite(s) (1 new)`), and `--format json` adds a `repeats` object that maps each
later tier to the suites it repeats and the tier that ran them first.

A command counts as a repeat only when its suite id **and** its command line are identical. A test
command whose markers differ between tiers (`unit`, then `unit or integration`) is different work
and always runs; picard does not assume that one marker expression contains another. A command that
failed stops the run before it could be recorded as done.

### Which branch is "the origin branch"

`--local` needs the branch your branch grew out of. It looks, in order, at:

1. a merge base recorded by `gh` or VS Code (`branch.<name>.gh-merge-base`,
   `branch.<name>.vscode-merge-base`),
2. the branch named when yours was created (`git checkout -b feature release` is recorded in the
   reflog as "Created from release"),
3. the **nearest other branch**: the one your branch has the fewest own commits on top of. A
   stacked branch (`feature-b` made from `feature-a`) therefore compares with `feature-a`, not
   with `main`. The remote copy of your own branch is never a candidate,
4. the upstream, when there is no other branch (for example on `main`).

Because the diff starts at the merge base, `main` moving on after you branched does not leak
other people's changes into yours. `picard changed` shows exactly what was chosen and why:

```
$ picard changed
feature/empty-input against main  (nearest ancestor branch, 2 commit(s) ahead of it)
merge base: 61afe6597d

commits (2):
  feat(some-api): log requests (DEMO-2)
  fix(component-abc): guard empty input (DEMO-1)

committed files (2):
  apps/some-api/src/some_api/handlers.py
  packages/component-abc/src/component_abc/checksum.py

uncommitted files (staged, unstaged, untracked) (3):
  apps/admin-frontend/src/dashboard.js
  apps/landing-page/NOTES.md
  packages/ui-kit/src/index.js
```

## Commands

```sh
# What would CI do for what I have locally? (every tier, with the graph)
picard simulate --local

# Simulate a change I have not made
picard simulate --files packages/abc/src/abc/encoder.py \
    --title "fix(abc): handle empty input (ABC-1)"

# Pick from the recent pull requests and see what CI would have run (needs `gh`)
picard simulate --pr                 # lists the last 30 of any state, type one or more numbers
picard simulate --pr 707 699         # name them, no prompt
picard simulate --pr --pr-limit 100

# Show the commands each tier would run
picard simulate --local --commands

# Actually run tiers 0 to 2, the way CI would, stopping at the first failure
picard simulate --local --tiers 0,1,2 --run

# One tier: print the plan, or run it (--dry-run only prints)
picard plan --tier 1 --local --format text
picard run  --tier 1 --local
picard run  --tier 1 --local --dry-run

# The dependency graph, plain or with an impact marked
picard graph
picard graph --files packages/ui-kit/src/index.js
picard graph --format mermaid     # paste into a README or PR

# Just the affected packages, for another step (build, deploy, lint only what changed)
picard affected
picard affected --base origin/main --format matrix
```

`simulate` and `run --tier N` use the same planner and the same commands as CI, so a green
local run predicts a green PR. They differ from CI only in the environment: CI installs
dependencies from `[runtime] setup_command` first, locally you set the repo up yourself.

## Listing what is affected

`picard affected` prints the affected set: what the rules, the impact engine, `always = true`
and a forced full run (`[labels] full`, an unknown base) put in, the same set a tier with
`packages = "affected"` runs. It takes the same change arguments as `simulate`, and a bare
`picard affected` means `--local`. It exits 0, also when nothing is affected.

| `--format` | Output |
|---|---|
| `names` (default) | One package name per line, in config order. Nothing at all when empty. |
| `paths` | One package path per line, in config order, without duplicates. Nothing when empty. |
| `matrix` | One line, GitHub's matrix shape: `{"include":[{"name":...,"path":...}]}`, `{"include":[]}` when empty. |
| `json` | Versioned (`schema` 1): `source`, `everything`, `packages` (name, path, provider, `decided_by`, `chain`) and `notes`. |

```sh
# real output, a scratch repo with three packages, a commit that changes component-def
picard affected --base main
# component-def
# some-api

picard affected --base main --format json
# {
#   "schema": 1,
#   "source": "main...HEAD",
#   "everything": false,
#   "packages": [
#     {"name": "component-def", "path": "packages/component-def", "provider": "manual",
#      "decided_by": "native", "chain": ["component-def"]},
#     {"name": "some-api", "path": "apps/some-api", "provider": "manual",
#      "decided_by": "native", "chain": ["some-api", "component-def"]}
#   ],
#   "notes": []
# }
```

## Reading a simulation

This one is from the demo repo, on the Pants engine, for two changed files: a Python module and
a JavaScript page.

```
changed files (2):
  packages/component-abc/src/component_abc/encoder.py
  apps/landing-page/src/page.js

title: fix(component-abc): handle empty input (DEMO-1)
verdict: profile full, release patch

DEPENDENCY GRAPH
○ churn-model  models/churn_model  [uv]
◐ some-api  apps/some-api  [uv]  via pants
└─ ◐ component-def  packages/component-def  [uv]  via pants
   └─ ● component-abc  packages/component-abc  [uv]  via pants
○ @demo/admin-frontend  apps/admin-frontend  [npm]
└─ ○ @demo/ui-kit  packages/ui-kit  [npm]
● @demo/landing-page  apps/landing-page  [npm]  via native
└─ ○ @demo/ui-kit  packages/ui-kit  [npm]

[provider] read the package from its manifest   via X: what decided it is affected

● changed   ◐ affected (depends on a change)   ○ untouched   (indent = depends on)

WHY
  component-def is affected through component-def -> component-abc (via pants)
  some-api is affected through some-api -> component-def -> component-abc (via pants)
  component-abc owns a changed file (via pants)
  @demo/landing-page owns a changed file (via native)

TIERS
  tier name          packages                                          markers               work
  0    pre-push      component-abc, component-def, some-api (+1 more)  unit                  7 suite(s)
  1    pr-fast-gate  component-abc, component-def, some-api (+1 more)  unit or integration   7 suite(s)  secrets
  2    pr-baseline   component-abc, component-def, some-api (+1 more)  baseline              3 suite(s)  semgrep, ...
  3    merge-queue   all 7 packages                                    not slow              11 suite(s)
  4    nightly       all 7 packages                                    (all)                 7 suite(s)  secrets, semgrep
  ! 1 file(s) are not owned by pants: answered by the native engine
```

- **Dependency graph:** read an indented line as "depends on". A change travels *up* the tree:
  everything above a changed package is affected.
- **Why:** the chain from each affected package down to the package that owns a changed file.
- **`[uv]`, `[npm]`:** the graph provider that read the package from its manifest.
- **`via pants`, `via native`:** the impact engine that decided the package is changed or
  affected. Other values are `command` (an external engine), `nx`, `turbo`, `moon` and
  `bazel-diff` (tool engines), `rule` (a `[[rules]]` entry), `global file` (a lockfile or config
  change affects everything), `lockfile` (a narrowed lockfile reached it), `always`
  (`always = true`) and `forced` (a full-run label or an unknown base). Here Pants owns the
  Python files and the native engine answers for the JavaScript one, which is the `!` note at
  the end.
- **Tiers:** tiers 0 to 2 follow the graph, tiers 3 and 4 ignore the diff and run everything.
- Warnings, for example a `docs:` title on a source change, are listed under the table.

`--format json` gives the same data for scripts, and `--format mermaid` the graph as a
flowchart where red is changed, amber is affected and grey is untouched.

## Seeing what the engines return

`--debug` works on any command (or set `PICARD_DEBUG=1`, or `debug = true` under `[impact]`).
It prints on **stderr**, so the JSON a command prints on stdout stays clean. With the Pants
engine it shows what Pants returned, what the native engine returns for the same files, and
where they differ:

```
$ picard graph --debug --files packages/component-def/tests/unit/test_formatter.py
[picard:debug:pants] pants list -> 1 owned file(s): packages/component-def/tests/unit/test_formatter.py
[picard:debug:pants] pants dependents --transitive -> 1: packages/component-def/tests/unit:tests
[picard:debug:pants] result (pants + fallback): changed=['component-def'] affected=['component-def']
[picard:debug:pants] native engine:             changed=['component-def'] affected=['component-def', 'some-api']
[picard:debug:pants] DIFFERENCE only in pants: []; only in native: ['some-api']
```

Nothing imports a test file, so Pants does not run `some-api` for a test-only change and the
native engine, which is package-level, does. That difference is expected.

## Is the engine installed?

```sh
picard doctor                    # ready, or a warning and exit 1
picard doctor --how-to-install   # install steps for whatever is missing
```

With `engine = "pants"` (or `nx`, `turbo`, `moon`, `bazel-diff`) and the tool not on the PATH,
every command warns once on stderr and answers with the fallback engine, so a plan is still
correct. The plan carries the same note.

## Preparing the repo for an engine

```sh
picard setup           # let the engine prepare the repo (writes its own files)
picard setup --check   # verify it, exit 2 if missing or stale: what CI runs
```

The native engine needs no setup. The Pants engine writes `pants.toml` and the build files for
the code it finds, never overwrites an existing `pants.toml`, and is idempotent. Run `setup`
again after adding a package or a test directory.
