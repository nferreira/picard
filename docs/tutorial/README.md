# Tutorial: put a repository under picard

A step-by-step path from an empty `.picard/` folder to a pipeline that runs only what a change
affects. Each step ends with a command that proves it worked, so you can stop at any step and
still have something useful.

| Step | You will | Takes |
|---|---|---|
| [1. Install and scaffold](01-install-and-init.md) | install `picard`, run `picard init`, read what it wrote | 10 min |
| [2. Describe your repository](02-describe-your-repo.md) | tell picard where packages are and how to test them | 20 min |
| [3. Preview a change](03-preview-a-change.md) | see what any change would run, before pushing it | 10 min |
| [4. Mark your tests](04-mark-your-tests.md) | give tests the markers that tiers select by | varies |
| [5. Rules for files with no code](05-rules-for-non-code-files.md) | run a check when a config, dataset or schema changes | 20 min |
| [6. Titles and releases](06-titles-and-releases.md) | let the PR title decide how much is verified | 10 min |
| [7. CI, hooks and rollout](07-ci-and-rollout.md) | wire GitHub, install the hooks, roll it out safely | 1 hour |

New to the ideas? Read [concepts](../dev/concepts.md) first. It is five minutes and explains
why picard fails closed. Looking something up rather than learning? Go to the
[reference](../reference/README.md).

## What you need

- Python 3.11 or newer and `git`.
- A repository with at least one package whose tests you can run from the command line.
- `gh` (the GitHub CLI) only for the optional `--pr` (`picard simulate --pr`, `picard affected --pr`).

## The shape of the whole thing

```
change (title + files) ─▶ picard ─▶ plan ─▶ the commands to run, per tier
                            ▲
              .picard/pipeline.toml  (the only file you edit)
```

You never edit a workflow to change what runs. You edit `pipeline.toml`, validate it, and
simulate. The same plan drives your laptop and CI.
