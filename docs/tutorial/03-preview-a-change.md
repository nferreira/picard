# 3. Preview a change

The most useful habit with picard is to ask what a change would run before you push it.
Nothing here needs CI, and nothing runs unless you add `--run`.

## Simulate a change you have not made

```sh
picard simulate --tiers 0,1 \
    --title "fix(core): handle empty input (ABC-1)" \
    --files libs/core/core/encoder.py \
    --commands
```

The output has four parts: the **changed files** and the profile the title and paths demand,
the **dependency graph** with the impact marked, **why** each package is affected, and a
**tiers** table. `--commands` adds the exact command each tier would run.

```
TIERS
  tier name          packages        markers               work
  0    pre-push      core, api       unit                  4 suite(s)
  1    pr-fast-gate  core, api       unit or integration   4 suite(s)  secrets

tier 0 (pre-push) commands:
  core:lint   ...
  core:test   uv run --no-sync python -m pytest libs/core -m "unit" ...
```

## Simulate what you have locally

```sh
picard changed                       # what differs locally, and against which branch
picard simulate --local              # every tier, for all of it
picard simulate --local --commands   # plus the commands
```

`--local` compares with the branch yours grew out of, plus uncommitted work. See
[where the changed files come from](../reference/simulation.md#where-the-changed-files-come-from).

## Replay a merged pull request

```sh
picard simulate --pr                 # pick from the last 30 merged PRs (needs gh)
picard simulate --pr 707 699
```

This plans each PR as if it had just been opened, from its real files, title and body. It is
the fastest way to check a new rule: replay the PRs that should have triggered it.

## Actually run it

```sh
picard simulate --local --tiers 0,1 --run
picard run --tier 0 --local            # what the pre-push hook does
picard run --tier 1 --local --dry-run  # print the commands, run nothing
```

Local runs use the same planner and commands as CI. They differ only in the environment: CI
installs dependencies from `[runtime] setup_command`; locally you set the repository up
yourself.

## Check it

Pick a change you know should touch one package, simulate it, and confirm the **work** column
lists that package and its dependents and nothing else. If everything runs, a global file or
an unmapped file is widening the plan; the **why** section names it.

**Next:** [mark your tests](04-mark-your-tests.md).
