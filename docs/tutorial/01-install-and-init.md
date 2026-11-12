# 1. Install and scaffold

## Install picard

picard is a Python package with a `picard` command. It has no runtime dependencies.

```sh
git clone <the picard repository> picard
uv tool install --editable ./picard        # or, inside a virtualenv: pip install -e ./picard
picard --version
```

To work on a repository that keeps picard as a nested checkout, run it through that
repository's environment instead (`uv run picard ...`). Everything below is the same either
way.

## Scaffold the repository

From the root of the repository you want to put under picard:

```sh
picard init
git status
```

`init` writes two kinds of files:

| Kind | Files | On `picard update` |
|---|---|---|
| **picard owns** | `.github/workflows/pipeline-*.yml`, `.github/actions/pipeline-*/`, `.picard/hooks/`, `.picard/templates/` | replaced |
| **you own** | `.picard/pipeline.toml`, `.github/release-please/*`, `CODEOWNERS`, `dependabot.yaml`, the PR template | written only when missing |

It touches nothing else. Review the diff and commit it.

> `.picard/templates/pytest/conftest.py` is a file you copy into your test root later
> ([step 4](04-mark-your-tests.md)). Until then, exclude `.picard/templates` from linters
> that scan dot-folders.

## Read the starter config

Open `.picard/pipeline.toml`. You do not need to understand all of it yet. The parts you will
touch in the next steps are:

| Section | Question it answers |
|---|---|
| `[runtime]` | which runner, which Python or Node, how to install dependencies |
| `[[discover]]` / `[[packages]]` | where are the packages and what do they depend on |
| `[tiers.N]` | what does each moment (pre-push, PR, merge queue, nightly) run |
| `[[rules]]` | what runs when a file with no code changes |
| `[types.*]` and `[profiles.*]` | how much a `fix:` or a `docs:` title must prove |

## Check it

```sh
picard validate-config
```

Validation is strict. It names the key when a profile, extra, package reference, regex or
unsafe character is wrong. Run it after every edit, and make it part of CI (the scaffolded
`picard-validate-config` pattern in the [reference](../reference/rules.md) shows how).

**Next:** [describe your repository](02-describe-your-repo.md).
