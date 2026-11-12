# 0008 · A dependency cycle is refused unless the repository accepts it

Status: accepted. Narrowed by [0009](0009-dev-group-edges-are-not-dependency-cycles.md): only runtime edges make a cycle.

## Context

picard refused any dependency cycle between packages, because it read the graph as a DAG. Real
workspaces have cycles that are not mistakes: package `a` lists `b` in a dev or test group (its
tests use `b`) while `b` depends on `a` in its runtime dependencies. The ecosystem allows it and
the build works. picard's uv provider reads dev and optional edges on purpose, because the tests of
`a` do need `b`, so the repository could not use picard at all.

The two ways out inside the old behavior were both wrong. Removing the dev edge runs less than the
truth (a change to `b` would not select `a`'s tests), which breaks the rule this tool is built
on. Failing the whole configuration makes picard unusable for any such workspace.

## Decision

- A new optional table, `[graph] cycles = "error" | "allow"`, default `"error"`. A repository that
  does not set it behaves exactly as before, down to the error message, which now also names the
  option.
- With `"allow"` every edge is kept. The impact walk was already breadth first with a visited set,
  so a change reaches every member of a cycle and everything that depends on any of them, and the
  lockfile narrowing walk was already finite. No engine changes.
- The tree renderer, the only recursion over `depends_on`, stops at a package already on the
  current path and prints `↺ name  (cycle)`.
- Cycles are found by `graph.find_cycles`, an iterative strongly-connected-components pass, so a
  long chain cannot exhaust the interpreter's stack. `validate-config` prints each accepted cycle
  on stderr, so allowing cycles is never silent.
- An unknown key under `[graph]`, or a value other than `error` or `allow`, is a configuration
  error that names the key.

## Consequences

- Opt-in widening only: the option can make picard select more packages than it would have, never
  fewer, so it cannot cause a skipped test.
- Cycles inside a cycle-heavy workspace make its members affect each other, so a change to one
  runs all of them. That is the true answer, and it is cheap to correct by removing the cycle.
- Engines that bring their own graph (Pants, Nx, Turborepo, Bazel) are unaffected.
