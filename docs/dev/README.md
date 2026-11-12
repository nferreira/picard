# Developing picard

For people who change picard itself, or who need to know why it behaves as it does.

| Page | Covers |
|---|---|
| [concepts.md](concepts.md) | affected-based, change-based, incremental, multitier: the ideas and the fail-closed rules |
| [architecture.md](architecture.md) | components, flow, impact engines, decisions, security model, limits |
| [contributing.md](contributing.md) | set up, test, add a provider, engine, executor, extra or action, release checklist |
| [roadmap.md](roadmap.md) | ideas that are not built yet, by what they buy |
| [adr/](adr) | architecture decision records: why each big choice was made |

## Architecture decision records

| ADR | Decision |
|---|---|
| [0001](adr/0001-plan-driven-tiers.md) | tiers are driven by a plan computed in code, not by `if:` expressions in YAML |
| [0002](adr/0002-vendored-template.md) | vendored template (superseded by 0006) |
| [0003](adr/0003-graph-from-ecosystem-manifests.md) | the graph is read from each ecosystem's manifests |
| [0004](adr/0004-rules-complement-the-graph.md) | rules cover files the graph cannot see; they do not replace it |
| [0005](adr/0005-plugin-architecture.md) | providers, engines and executors are plugins behind small interfaces |
| [0006](adr/0006-picard-is-a-python-package.md) | picard is an installable package that a host pins |

## Rules of the house

These are also in `CLAUDE.md` at the repository root.

- **Standard library only at runtime**, Python 3.11 or newer. picard is a dependency of every
  repository's CI. Dev tools go in `[dependency-groups] dev`.
- **Fail closed.** Anything that narrows what runs must err on the side of running more. An
  engine, provider or rule may widen an answer and never narrow it.
- **Workflows decide nothing.** New behaviour is a plan field and a test, then workflow wiring.
- **Untrusted text never reaches a shell.** PR titles and bodies go through `env:`.
- **Keep `templates/` generic.** Nothing picard scaffolds may name an organisation, team,
  company repository or ticket prefix.
- **A behaviour change updates the docs in the same PR.** User-facing behaviour belongs in
  [reference](../reference/README.md), a new step in the [tutorial](../tutorial/README.md), a
  new reason in an ADR.
