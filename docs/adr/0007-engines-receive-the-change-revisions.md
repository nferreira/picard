# 0007 · Engines receive the change's revisions as an attribute

Status: accepted

## Context

Until now an impact engine was given a list of changed files and nothing else. That is enough
for the native graph walk, Pants and the `command` engine. It is not enough for tools that
answer from git refs (Turborepo, Nx with `--base`/`--head`, bazel-diff, which hashes both
revisions), or for lockfile narrowing, which must read the old and the new lockfile. Those need
the commits the change lies between, when picard knows them: `--base` and `--local` have them,
while `--files`, `--staged` and `--working` do not.

Engines are also plugins (ADR 0005). Changing the abstract `compute(cfg, files)` signature
would break every engine written outside picard.

## Decision

- A frozen `Revisions(base, head, uncommitted)` describes the range: `base` is the merge-base
  commit, `head` the head as given, `uncommitted` the working-tree files `--local` adds on top.
  It is `None` when the change has no git range.
- `ImpactEngine.impact(cfg, files, revisions=None)` stores it as `self.revisions` before it calls
  `compute(cfg, files)`. `compute` keeps its signature, so existing engines work unchanged, and
  an engine that needs the commits reads the attribute.
- The memo key of `compute_impact` includes the revisions, so two ranges over the same files
  never share an answer.
- `impact` also splits off the files listed under `[lockfiles] narrow` and asks
  `lockfile_impact` about them, then merges that answer into the rest (union, existing chains
  and labels win).

## Fail-closed rules

- `revisions is None` means "no git range". An engine that needs one answers through its
  fallback for everything it would have asked the tool, and says so in a note.
- Uncommitted files are not in any commit, so a git-based tool cannot see them: they are always
  also answered from the files (the fallback), never dropped.
- Test selection inside a suite (`[tiers.N] selection`) gets a base only with a committed range,
  that is `revisions` is set and `uncommitted` is empty. Otherwise whole suites run and the plan
  warns.
- Until a lockfile change can be attributed to packages, `lockfile_impact` affects every
  package. An answer can widen, never narrow.

## Consequences

- Plugin engines keep working; one that wants the range reads `self.revisions`.
- The attribute is per call: an engine instance is not shared between two `impact` calls that
  could run at once, because `compute_impact` builds one per question.
