# 0005 · Providers, engines and executors are plugins

Status: accepted

## Context

picard is an affected-based CI/CD tool and has to work for Go, Rust, Python, TypeScript or
anything else, without locking a repo into one build system. ADR 0003 read the graph from
`uv.lock` and the npm manifests. Adding cargo, go and Pants to that as more branches in one
module would have mixed three concerns: discovering packages, deciding what a change affects,
and building the command that runs a package's tests.

We also want to reuse a tool that already does the job (Pants for import-level impact) and not
rebuild it, while keeping a repo that has no such tool working with nothing installed.

## Decision

Three small interfaces, each a registry of named implementations, wired in one composition root
(`bootstrap.py`) so no module depends on a concrete one and no state is global.

| Interface | Question | Built in |
|---|---|---|
| `GraphProvider` | What packages exist and what depends on what? | `uv`, `npm`, `cargo`, `go`, `command` |
| `ImpactEngine` | What does this change affect? | `native`, `pants`, `command` |
| `Executor` | What command runs this package's suite? | `pytest`, `uv-pytest`, `go-test`, `cargo-test`, `pnpm`, `npm`, `make` |

- **Provider and engine are separate.** The provider always builds the package graph. The
  engine decides the impact and may use that graph (`native`) or ask a tool (`pants`,
  `command`).
- **Pants is an engine, not a requirement.** The default stays `native`. A bake-off showed Pants
  misses non-Python files, lockfiles and contracts, so the `pants` engine hands every file it
  does not own to a fallback engine (Decorator over `native`).
- **Template method for the shared policy.** `ImpactEngine.impact` applies the global-file rule
  (a lockfile affects everything) and calls the engine's `compute` for the rest, so no engine
  can forget it.
- **An engine can widen the answer, never narrow it.** A failure, a timeout or an unowned file
  goes to the fallback and adds a note to the plan.
- **Engines can prepare the repo.** `picard setup` and `picard setup --check` call the engine's
  `setup` hook. The command name and its messages do not mention any tool, so the choice of
  engine stays an implementation detail. `native` needs no setup.
- **Every affected package records who decided it** (`decided_by`: `native`, `pants`,
  `command`, `rule`, `global file`), shown as `via X`, so a surprising plan can be explained.
- **Plugins come from `[plugins]`**, a module or a file in the repo, with a
  `register(registries)` function. They carry the same trust as the repo's workflows.

## Consequences

- Supporting a new language is a provider and an executor. Supporting a new build system is a
  provider, an engine, or just the `command` forms of both, with no change to the planner.
- The graph stays as precise as the engine allows. Under `native` a test-only change runs the
  dependents of its package, and under `pants` it does not. Both are correct, and `--debug`
  shows the difference.
- Pants is verified for Python only. Its Go backend dropped a local cross-module edge in a
  trial, so `picard setup` does not enable it. Rust and JavaScript have no stable Pants
  backend and stay on the fallback.
- Rule actions (`none`, `full`, `package`, `own`, `require-review`, `run:`) are still fixed in
  `rules.py`. Their registry exists and is not used yet.
- Plugins run arbitrary code at planning time, so a change to `[plugins]` needs the same
  review as a workflow change (`CODEOWNERS` covers `.pipeline/`).
