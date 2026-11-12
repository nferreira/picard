# 0011 · A lockfile picard recognises is global unless narrowed or covered by a rule

Status: accepted

## Context

picard's documentation says a lockfile change runs everything unless the lockfile is listed under
`[lockfiles] narrow`. The code did not do that: a lockfile was global only if `[paths] global`
listed it, and the starter config does not. A lockfile that matched nothing was then a file no rule,
package or global glob covered, and with the default `[rules_policy] unmapped = "ignore"` it planned
nothing. On a real repository, bumps of `certifi`, `filelock` and a type-stub package all planned zero
suites and the pull requests went green untested; only the merge queue, which runs everything, would
have caught a break. That is the failure picard is built never to have.

## Decision

- A lockfile picard recognises by file name is global (every package, labelled `global file`) when it
  changes, unless it is under `[lockfiles] narrow`, or a `[[rules]]` entry matches it. Rules are
  resolved first, as for any file, so a rule can send a lockfile to specific actions.
- The recognised names are the `SUPPORTED` ones (`uv.lock`, `Cargo.lock`, `package-lock.json`) plus
  `pnpm-lock.yaml`, `yarn.lock`, `poetry.lock`, `Pipfile.lock`, `go.sum`, `composer.lock` and
  `Gemfile.lock`, in any directory. Only `SUPPORTED` ones can be narrowed.
- Nothing else changes. Narrowing still fails closed when a change cannot be attributed, listing a
  lockfile under `[paths] global` behaves as before, and the `unmapped` report is unchanged: an
  unlisted lockfile is still reported as unmapped when the policy is not `ignore`, now in addition to
  being global. That is a redundant note under `warn`, kept so that an existing test and an existing
  behaviour stay as they were.

## Consequences

- A repository that relied on lockfile changes being ignored runs every package on a dependency bump
  until it adds `[lockfiles] narrow` or a rule. That is the documented behaviour and the safe one.
- The opt-out is one line, and it is precise: narrowing selects the packages a bump reaches.
- A lockfile picard cannot read (`pnpm-lock.yaml`, `yarn.lock`) is global until a rule says what it
  affects.
