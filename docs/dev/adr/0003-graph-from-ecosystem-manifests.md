# 0003 · The dependency graph comes from the package managers

Status: accepted

## Context

In a monorepo a change in a library must run the tests of everything that depends on it:
change `component-abc`, and `component-def` and `some-api` run too. The first version of
this pipeline took the graph from `depends_on` lists in `pipeline.toml`, which duplicates what
`pyproject.toml` and `package.json` already say, and drifts the day someone adds a dependency
and forgets the second list.

We want to reuse existing tools rather than reinvent them.

## Options

| Option | Reuses | Cost |
|---|---|---|
| Hand-written `depends_on` | nothing | duplicated truth, silent drift |
| **Read `uv.lock` and the npm/pnpm workspace manifests** | uv and pnpm own the graph; we read their files | package-level granularity, two small parsers |
| Nx (`nx affected`) | its project graph, caching, plugins | a build system to adopt, Python support is through a community plugin |
| Pants (`--changed-dependents=transitive`) | import-level inference for Python | a build system to adopt, JavaScript support is immature |
| Turborepo / `pnpm --filter "...[origin/main]"` | JavaScript only | does not see Python |
| Bazel | everything | an order of magnitude more setup |

## Decision

Discover the graph from the manifests the package managers already maintain: `uv.lock` for
uv workspaces and `package.json` workspace dependencies for pnpm, npm and yarn. Explicit
`[[packages]]` entries merge over discovered ones, for edges the manifests cannot express.

The planner keeps only what no build tool provides: mapping a Conventional Commit and the
changed paths to a profile, the five tiers, markers, the gate, and the release effect.
Everything below that line is reused: uv and pnpm to install and run, pytest markers and
`node --test` to select tests, ruff, actionlint, gitleaks, semgrep, release-please.

## Consequences

- Adding a package or a dependency the normal way is enough, the pipeline follows it.
- The graph is as precise as the declared dependencies. It does not see an import a package
  forgot to declare, and it is coarser than Pants' import graph: a change to one module runs
  the whole package's suite. Test-level selection is on the roadmap.
- Cross-ecosystem or contract edges (a frontend consuming an API's OpenAPI file) are not in any
  manifest. Express them with `depends_on` on a package, or with a `[[path_rules]]` entry that
  adds a package to tier 2 when a path changes.
- If a repo outgrows this, a provider that shells out to Nx or Pants fits the same interface
  (a list of packages, paths and edges) and the rest of the planner does not change.
  [ADR 0005](0005-plugin-architecture.md) did that: the graph (providers) and the impact
  (engines) are now separate, and Pants is an engine.
