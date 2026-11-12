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
| `--base REF` | `git diff REF...HEAD` | CI: the PR base commit |
| `--pr [N ...]` | the files, title and body of merged pull request(s); bare `--pr` lists recent ones to pick from | "what would CI have run for that PR?" |

The first match wins. Without `--title`, a change with no commit yet is planned from its paths
alone: a source file demands `full`, a markdown file demands `docs`, and a title you add can
only raise that.

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

# Pick from the recently merged PRs and see what CI would have run (needs `gh`)
picard simulate --pr                 # lists the last 30, type one or more numbers
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
```

`simulate` and `run --tier N` use the same planner and the same commands as CI, so a green
local run predicts a green PR. They differ from CI only in the environment: CI installs
dependencies from `[runtime] setup_command` first, locally you set the repo up yourself.

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
  affected. Other values are `command` (an external engine), `rule` (a `[[rules]]` entry) and
  `global file` (a lockfile or config change affects everything). Here Pants owns the Python
  files and the native engine answers for the JavaScript one, which is the `!` note at the end.
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

With `engine = "pants"` and no Pants on the PATH, every command warns once on stderr and answers
with the native engine, so a plan is still correct. The plan carries the same note.

## Preparing the repo for an engine

```sh
picard setup           # let the engine prepare the repo (writes its own files)
picard setup --check   # verify it, exit 2 if missing or stale: what CI runs
```

The native engine needs no setup. The Pants engine writes `pants.toml` and the build files for
the code it finds, never overwrites an existing `pants.toml`, and is idempotent. Run `setup`
again after adding a package or a test directory.
