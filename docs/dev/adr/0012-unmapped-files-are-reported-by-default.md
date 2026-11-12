# 0012 · A file nothing covers is reported by default

Status: accepted

## Context

`[rules_policy] unmapped` decides what happens to a changed file that no rule, package, docs glob or
global glob covers. Its default was `ignore`: the plan was empty for that file and said nothing. On a
real repository that hid whole areas (`justfile`, `.importlinter`, `openspec/`, `data/`, the root test
directories) for as long as nobody went looking, which is how the gaps were found only by replaying
pull requests by hand.

## Decision

The default is `warn`. A plan lists the uncovered files in its warnings (`N file(s) match no rule and
no package: ...`) and in the plan's `unmapped` field, and runs exactly what it ran before. `full`
(everything runs) and `ignore` (silence) are still one line in `[rules_policy]`.

`full` is not made the default: it changes what runs for every repository on upgrade, and a
repository should choose it once its paths are mapped.

## Consequences

- No behaviour change, only visibility: the set of packages and suites a plan selects is identical.
- No gate reads warnings (the planner prints them; workflows decide nothing), so this cannot turn a
  green pipeline red.
- Repositories with uncovered paths see a warning on every plan that touches one, which is the
  point; the fix is a rule, a package, a docs glob or `unmapped = "ignore"`.
