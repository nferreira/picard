# picard documentation

| Section | For | Start with |
|---|---|---|
| [tutorial](tutorial/README.md) | learning picard by doing: install, configure, preview, roll out | [1. Install and scaffold](tutorial/01-install-and-init.md) |
| [reference](reference/README.md) | looking something up: commands, every config key, rules, tiers, the plan | [cli.md](reference/cli.md), [configuration.md](reference/configuration.md) |
| [dev](dev/README.md) | changing picard, or understanding why it works this way | [concepts.md](dev/concepts.md), [architecture.md](dev/architecture.md) |

## Quick answers

| I want to | Go to |
|---|---|
| try picard in a repo today | [tutorial](tutorial/README.md) |
| know what a change would run | [tutorial step 3](tutorial/03-preview-a-change.md), [simulation](reference/simulation.md) |
| run a command only when some YAML or data changes | [tutorial step 5](tutorial/05-rules-for-non-code-files.md), [rules](reference/rules.md) |
| understand why nothing (or everything) ran | [rules: tiers an action honours](reference/rules.md#which-tiers-an-action-honours), [concepts](dev/concepts.md#never-run-less-than-the-truth) |
| add a provider, engine, executor or extra | [contributing](dev/contributing.md#recipes) |
