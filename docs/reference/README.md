# Reference

Look-up documentation. For a guided path see the [tutorial](../tutorial/README.md); for why it
is built this way see [dev](../dev/README.md).

| Page | Covers |
|---|---|
| [cli.md](cli.md) | every command, flag, exit code and environment variable |
| [configuration.md](configuration.md) | every `pipeline.toml` section except rules and tiers |
| [rules.md](rules.md) | `[[rules]]`, actions, `[actions.*]`, inventories, `[rules_policy]`, and exactly which tiers each action honours |
| [tiers.md](tiers.md) | tiers 0 to 5, `[tiers.N]`, the baseline list, markers and the gate |
| [conventional-commits.md](conventional-commits.md) | title format, profiles, breaking changes, release effect |
| [simulation.md](simulation.md) | where changed files come from, reading a simulation, `--debug`, `doctor`, `setup` |
| [plan.md](plan.md) | the plan JSON every workflow and the CLI read |
| [github-integration.md](github-integration.md) | the scaffolded workflows, actions, hooks, variables and secrets |

## Where each question is answered

| Question | Page |
|---|---|
| Why did this file run everything? | [rules.md](rules.md#resolution), [simulation.md](simulation.md#reading-a-simulation) |
| Why did a tier run nothing for my YAML change? | [rules.md](rules.md#which-tiers-an-action-honours) |
| Which command overrides the test command at one tier? | [configuration.md](configuration.md#discover) |
| What does `exit 2` mean? | [cli.md](cli.md#exit-codes) |
| What can I put in a profile's `extras`? | [configuration.md](configuration.md#profilesname) |
| Which GitHub settings are required? | [github-integration.md](github-integration.md#github-settings) |
