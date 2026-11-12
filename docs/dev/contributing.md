# Contributing

## Set up and check

```sh
uv sync                  # picard editable, plus ruff and pytest
just check               # ruff, tests, starter-config validation, actionlint
uv run pytest            # tests only
uv run pytest tests/test_rules.py -k own
```

`just check` is what picard's own CI runs (`.github/workflows/ci.yml`). Run it before you hand
anything over.

## Code map

```
src/picard/
  cli.py          argument parsing and the commands
  config.py       pipeline.toml loading and validation (KNOWN_EXTRAS, PLACEHOLDERS, SAFE_VALUE)
  planner.py      (config, tier, titles, body, files) -> plan
  rules.py        [[rules]] resolution: actions, captures, inventories
  commits.py      Conventional Commit parsing
  globs.py        glob matching and captures
  gitutil.py      changed files, merge base, the "origin branch" heuristic
  prs.py          pull requests through gh, for --pr
  graph/          providers/ (read manifests), engines/ (answer impact), render.py
  executors/      build a suite command for a package
  scaffold.py     init, update, install-hooks
  bootstrap.py    the composition root: registers built-ins and plugins
  registry.py     name -> provider, engine, executor
  templates/      package data that init writes into a host
tests/            one file per area; cli and simulate tests build scratch git repos
```

## Rules for changes

1. **Plan first.** A new behaviour is a field in the plan (`planner.py`), a test, and only then
   workflow wiring. If a workflow needs an `if:` that is not `plan.skip`, the logic is in the
   wrong place.
2. **Fail closed.** If a change can make a plan smaller, ask what happens when the input is
   wrong, missing or hostile. The answer must be "it runs more" or "the gate fails".
3. **No runtime dependencies.** Use the standard library. Read package data with
   `importlib.resources`, never a path relative to `__file__`.
4. **Keep the ownership split.** `init` and `update` replace what picard owns and never
   overwrite what the repository owns. `tests/test_scaffold.py` enforces it.
5. **Docs in the same PR.** See the table below.
6. **Commits and PR titles:** `type(scope): lowercase subject (TICKET)`.

## Where a change is documented

| You changed | Update |
|---|---|
| a command or flag | [reference/cli.md](../reference/cli.md) |
| a `pipeline.toml` key | [reference/configuration.md](../reference/configuration.md), and the starter `src/picard/templates/pipeline.toml` if it is a default |
| rule or action behaviour | [reference/rules.md](../reference/rules.md), and the tutorial step 5 if the example changes |
| a tier | [reference/tiers.md](../reference/tiers.md) |
| the plan JSON | [reference/plan.md](../reference/plan.md) |
| title parsing or profiles | [reference/conventional-commits.md](../reference/conventional-commits.md) |
| a workflow, action or hook | [reference/github-integration.md](../reference/github-integration.md) |
| a design decision | a new ADR in [adr/](adr) |

## Recipes

### Add a graph provider

1. Subclass `GraphProvider` (`graph/providers/base.py`) in a new module: set `name` and
   implement `discover(root) -> list[Discovered]`, returning packages, paths and edges between
   workspace members only. Raise `DiscoveryError` for anything unusable. Options come from the
   `[[discover]]` block.
2. Register it in `bootstrap.py`.
3. Tests in `tests/test_providers.py` with a scratch workspace: package discovery, an edge, an external dependency that
   must be ignored, an unsafe name that must be rejected.
4. Add a row to the provider tables in [configuration](../reference/configuration.md#discover)
   and the [tutorial](../tutorial/02-describe-your-repo.md).

### Add an impact engine

Subclass `ImpactEngine` and implement only the engine-specific part. The global-file policy
lives in the base class and must not be re-implemented. On any failure, raise so the fallback
answers: an engine can widen an answer and never narrow it. Add `doctor` and `setup` support
if it needs an external tool.

### Add an executor

Subclass `Executor` (`executors/base.py`), set `name` and implement
`command(ctx: SuiteContext) -> str | None`. `ctx` carries the package, the suite name, the tier
name and the markers. Return `None` for a suite the toolchain has no command for (it is
skipped). The planner only asks when the package gives no explicit command. Register it. Document it in the executors
table of [configuration](../reference/configuration.md#executorsname).

### Add an extra

Three edits, and a test checks the first two agree:

1. `KNOWN_EXTRAS` in `config.py`
2. a step in `src/picard/templates/github/actions/pipeline-extra/action.yml`
3. the extras list in [configuration](../reference/configuration.md#profilesname) and
   [github-integration](../reference/github-integration.md#composite-actions)

### Add a rule action

Add the name to `BUILTIN_ACTIONS` in `config.py`, handle it in `rules.resolve` (does it add to
`graph_files`, `own` or `full`?), and decide which tiers it honours. Document that last point
in [rules.md](../reference/rules.md#which-tiers-an-action-honours): it is the one users trip on.

### Add a plugin from a repository

A plugin is a module with `register(registries)`, loaded from `[plugins] modules` or `paths`.
It carries the same trust as the repository's workflows.

## Releasing picard

1. `just check` is green.
2. After touching `templates/`: `uv build --wheel`, install the wheel into a clean venv and run
   `picard init` in a scratch repository. A wheel hides what an editable install shows.
3. Tag. Hosts pin a tag or commit through `PICARD_REF` and adopt it with `picard update`.
