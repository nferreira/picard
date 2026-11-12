# 0009 · Dev-group edges are kept for impact and not counted as dependency cycles

Status: accepted. Narrows [0008](0008-dependency-cycles-are-opt-in.md).

## Context

ADR 0008 made a dependency cycle opt-in (`[graph] cycles = "allow"`). The cycle that prompted it
is not a design problem. Package `a` depends on `b` at runtime, and `b` lists `a` only in a dev or
test group because its tests use `a`. Every ecosystem keeps that out of what a package needs to
run, and the repository's own metadata can say it is deliberate. Making each such repository
write `cycles = "allow"` would also make it accept real runtime cycles it never meant to allow.

Dropping the dev edge is not an option: `b`'s tests need `a`, so a change to `a` must select `b`.
Selecting less than the truth is the one failure this tool must not have.

## Decision

- Providers mark the edges that exist only in a development group. `Discovered` gains
  `dev_depends_on`, a subset of `depends_on`, with an empty default so every existing provider and
  plugin keeps working. uv marks `dev-dependencies`, npm `devDependencies`, Cargo
  `dev-dependencies` (including per target). Extras and every other table stay runtime. A name
  that is also a runtime dependency is a runtime edge.
- `Package.depends_on` is unchanged: it is every edge, and the impact walk uses all of them. A new
  `Package.runtime_depends_on` is the part a package needs to run.
- The cycle check, and the cycles `[graph] cycles = "allow"` reports, use runtime edges only.
  An edge declared by hand in `[[packages]] depends_on` is a runtime edge, also when it names a
  discovered dev-only edge.
- `[graph] cycles` keeps its meaning for the cycles that remain, so a real runtime cycle is still
  refused by default, with the same message plus a note that dev edges are not counted.

## Consequences

- A repository with only dev-edge loops needs no configuration and sees no note.
- Selection is unchanged: the same edges are walked, so no package that was selected stops being.
- A dev edge is still transitive. Making dev edges stop the walk would narrow selection, so it
  needs its own opt-in and ADR.
- Engines that bring their own graph are unaffected.
