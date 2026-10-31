# Affected-based CI/CD

picard is an **affected-based** CI/CD tool, also called **change-based execution** or
**incremental CI**, built as a **multitier** pipeline. It works out what a change can affect and
runs only that. As the change gets closer to production, the tiers widen the net until nothing
is skipped.

| Term | In picard |
|---|---|
| **Affected-based** | Work is chosen from the dependency graph: the packages that own a changed file, plus everything that depends on them, transitively. |
| **Change-based execution** | The change itself decides what runs: the Conventional Commit PR title (how much to verify), the changed paths (where) and `[[rules]]` (files with no code). |
| **Incremental** | The cost of a PR follows the size of the change, not the size of the repository. |
| **Multitier** | Six tiers, from a local pre-push hook to a release. Each has its own scope, test selection and cost budget (see [tiers](tiers.md)). |

## How a decision is made

```
change ──▶ classify ──▶ impact ──▶ plan ──▶ execute
           title+paths  graph+rules  per tier   suites, extras, gate
```

1. **Read the change.** Paths come from `--files`, the git index, the working tree, the merge
   base of the branch (`--local`) or the PR base commit (`--base`). The title comes from the PR.
   See [simulation](simulation.md).
2. **Classify it.** The title's type picks a profile (`full`, `infra`, `deps`, `light`, `docs`)
   and a release effect. Paths can raise the profile and never lower it
   ([conventional commits](conventional-commits.md)).
3. **Compute the impact.** A graph provider reads each ecosystem's own manifests to list the
   packages and the edges between them. An impact engine maps each changed file to its owning
   package and walks the dependents ([architecture](architecture.md#impact-and-engines)).
   `[[rules]]` cover files the graph cannot see: datasets, schemas, migrations, configs
   ([configuration](configuration.md#rules-actions-inventory-rules_policy)).
4. **Build the plan.** One JSON document per tier says which suites, markers and extras run.
   Workflows and the local CLI read the same plan, so a green local run predicts a green PR.
5. **Execute.** Suites run as a matrix, and one gate job turns the run into a single required
   check.

## Where the narrowing happens

| Tier | Scope | Why |
|---|---|---|
| 0 · Pre-push | affected packages, `unit` tests | Seconds of feedback on your machine. |
| 1 · PR fast gate | affected packages, `unit or integration` | The main saving: most PRs touch a fraction of the repo. |
| 2 · PR baseline | affected + must-pass packages, `baseline` tests | The checks that must hold for any change. |
| 3 · Merge queue | **everything**, `not slow` | How this PR behaves together with the ones ahead of it. |
| 4 · Nightly | **everything**, slow tests included | Catches what the incremental tiers cannot. |
| 5 · Release | commits since the last tag | Release Please builds the version and the changelog. |

Tiers 3 and 4 ignore the diff on purpose. An incremental pipeline is only as trustworthy as its
safety net, and these two are it.

## Never run less than the truth

Skipping work is only safe if the answer errs on the side of running more. These rules make it
fail closed:

- **A global file runs everything.** A lockfile, the config or a workflow affects every package
  (`[paths] global`).
- **An unmapped file runs everything.** A file no rule, package or docs glob covers follows
  `[rules_policy] unmapped = "full"`.
- **A title can raise the bar, never lower it.** `docs:` on a PR that edits source still runs
  the full profile, and an unparseable title gets the strictest one.
- **A label can raise the bar, never lower it.** The `[labels] full` label (`ci:full` by
  default) runs every package at the strictest profile. No label makes a PR run less, and a
  package marked `always = true` is in every PR's affected set.
- **An engine can narrow the answer, never lose a package.** If Pants or a command engine
  fails, is not installed (picard warns, and `picard doctor --how-to-install` says how to fix it)
  or does not own a file, the fallback engine answers, and the plan says so.
- **Unsafe input runs everything.** A rule capture that is not a plain name, or is missing from
  the inventory, runs everything and warns. It is never turned into a command.
- **A job that had to run and did not fails the gate.** A silent skip cannot go green.

## Granularity and limits

- The unit is the **package**. A change inside a package runs that package's whole suite for
  the tier. Under the native engine a test-only change also runs the dependents. Pants is more
  precise because nothing imports a test file. Test-level selection is on the
  [roadmap](improvements.md).
- The graph is as precise as the dependencies a package declares. An import nobody declared
  is invisible to the native engine. Pants infers imports for Python.
- Edges that live in no manifest (a frontend that consumes an API's OpenAPI file) are written
  with `depends_on` on a package, or with a `[[path_rules]]` entry.

## What picard reuses and what it owns

picard is the decision layer, not a build system. It does not cache, build or distribute work.

| Reused | Owned by picard |
|---|---|
| The package graph (uv, pnpm, npm, yarn, cargo, go, or Pants as an engine) | Mapping a title and paths to a profile |
| Test selection (pytest markers, `go test`, `cargo test`, `node --test`) | The six tiers and the plan |
| Install and run (uv, pnpm, cargo, go) | The gate that makes skips safe |
| Linters and scanners (ruff, actionlint, gitleaks, semgrep) | Rules for files with no code |
| Releases (Release Please) | Running the same plan locally and in CI |

It sits beside tools such as Nx, Turborepo, Bazel or Pants, and can use them as the source of
the graph. It never requires one. See [ADR 0003](adr/0003-graph-from-ecosystem-manifests.md) and
[ADR 0005](adr/0005-plugin-architecture.md).
