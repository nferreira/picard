# 0010 · `--pr` plans pull requests, read with the GitHub CLI

Status: accepted

## Context

CI plans a pull request from its title, body, labels and the files it changes. A local run has the
commits but not the rest, so previewing "what will CI do for PR 123" meant copying the title and
labels by hand into `--title` and `--label`, and that is where a preview and the real run drift
apart. picard assumes no hosting platform and needs only git.

Two designs for this existed side by side: one planned **merged** pull requests, several at a time,
from the platform's file list, on every command that takes a change; the other planned **one** pull
request of any state with its labels and its commits, on `simulate` only. Neither alone is enough.

## Decision

One `--pr [N ...]`, on every command that takes a change (`plan`, `run`, `simulate`, `affected`,
`graph`), with `--pr-limit N` for the list.

- The platform is reached through one module, `picard.prs`, which runs `gh`. Nothing else knows
  about it. picard gains no dependency: without `gh` the option stops and says to describe the
  change with `--files`, `--title` and `--label`.
- The file list is the platform's own, every page, with both paths of a rename. It works for a
  pull request in any state and needs no local commits.
- Title, body and labels come from the pull request. `--title` and `--body` replace them, `--label`
  adds to the labels, as for every other source. Several pull requests are planned together: the
  union of their files, titles and labels.
- A **single** pull request also brings its commits when they are in the clone (the head is fetched
  once as `pull/<N>/head`), so a lockfile change can be narrowed. They are used only when the git
  range gives exactly the platform's file list. Several pull requests, a missing commit, a rename or
  any mismatch give no range, so a lockfile change is global again: more runs, never fewer.
- `--pr` cannot be combined with `--files`, `--files-from`, `--staged`, `--working`, `--base` or
  `--local`. The pull request says what changed, and silently preferring one source hides mistakes.
- With no number it lists the recent pull requests of **every state** (`gh pr list --state all`),
  newest activity first, with day, state, title and branch, and asks for one or several numbers.
  The checked-out branch's pull request is starred and Enter takes it. The prompt is on stderr so
  structured output stays clean. A number outside the list is refused (raise `--pr-limit` or name
  it). With no terminal it stops and says to name the pull request; piping the answer works.
- The argument is a number, optionally with `#`, validated before it reaches `gh`.

## Consequences

- A preview matches CI for the inputs that decide the plan, including the `ci:full` label.
- Whoever runs it needs `gh` logged in; it only reads.
- The source line names the state (`merged PR #9`, `open PR #7`, `PR #8, #9` for a mix).
- `plan --pr` is available, though in CI those commands already receive the real values.
