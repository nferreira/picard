# 7. CI, hooks and rollout

The earlier steps work on your machine. This one wires GitHub and the git hooks, and rolls the
pipeline out without blocking anyone. Settings and file lists are in
[github-integration](../reference/github-integration.md).

## Install picard and scaffold the repo

Install picard once per machine, from a clone of its repository:

```sh
git clone <the picard repository> picard
uv tool install --editable ./picard        # or, inside a virtualenv: pip install -e ./picard
```

Then, from the root of the repo to adopt:

```sh
picard init
```

`init` adds the `pipeline-*` workflows and actions under `.github/`, the hooks under
`.picard/hooks/`, and the files you own, only if they are missing: `.picard/pipeline.toml`,
`.github/release-please/*`, `CODEOWNERS`, `dependabot.yaml` and the PR template. It touches
nothing else. Review with `git status` and commit.

## Describe your repo in `.picard/pipeline.toml`

- `[runtime]`: Python/Node version and the command that installs dependencies.
- `[[packages]]`: one per unit, with its `lint` and `test` commands. Set `baseline = true` on
  the packages whose baseline tests must always run. See [configuration](../reference/configuration.md).
- `[paths]`: adjust `docs`, `global` and the floors for your layout.
- `[breaking] release`: `minor` while below 1.0.

Check it: `picard validate-config`, then preview a branch:
`picard plan --tier 1 --local --format text`.

Affected-based selection needs the dependency graph, which comes from your package managers:
add one `[[discover]]` block per ecosystem (`uv`, `npm`, `cargo`, `go`, or `command` for
anything else) and check that `picard graph` shows the packages and edges you
expect. The default `native` impact engine needs nothing more. To use Pants for import-level
precision, set `engine = "pants"` under `[impact]` and run `picard setup`, then
add `picard setup --check` to CI. See [configuration](../reference/configuration.md#impact).

## Keep `.picard/templates/` out of your linters

`.picard/templates/pytest/conftest.py` is picard's marker plugin, a file you copy into your test
root and then own. Until you do, exclude the folder from tools that scan dot-folders, for example
`[tool.ruff] extend-exclude = [".picard/templates"]`. pytest and eslint already skip it.

## Mark your tests

Tiers select tests by marker. Copy `.picard/templates/pytest/conftest.py` (written by `picard init`) to your test root,
register `unit`, `integration`, `baseline` and `slow` in `pyproject.toml` with
`--strict-markers`, and mark the must-pass tests `@pytest.mark.baseline`. Turn on
`PICARD_REQUIRE_MARKERS=1` once the backlog is marked. See [tiers](../reference/tiers.md#the-marker-prerequisite).

## Fill the placeholders

| File | Replace |
|---|---|
| `.github/CODEOWNERS` | `@REPLACE_ME` with the real owner |
| `.github/release-please/config.json` | `release-type` if the repo is not language-agnostic (`python`, `node`, `go`, ...) |

## GitHub settings

- **Install picard in CI:** the workflows install a pinned picard. Set the repository variables
  `PICARD_REPOSITORY` (`owner/name`) and `PICARD_REF` (a tag, or better a full commit SHA) and, when
  the picard repository is private, the secret `PICARD_TOKEN` with read access to it. The token
  only reaches the install step. Fork PRs get no secrets, so keep picard readable for them.
- **Release App:** a GitHub App with contents and pull-requests write. Repository variable
  `RELEASE_APP_ID`, secret `RELEASE_APP_PRIVATE_KEY`.
- **Branch protection on `main`:** require the checks `pipeline-gate` and `merge-gate`, require
  a code-owner review from someone other than the author, require signed commits.
- **Merge queue:** enable it for `main`.
- **Squash merge only,** with the commit message set to **Pull request title**.
- **Actions:** allow Actions from the organization, and pin third-party actions to SHAs.
- **Self-hosted runners:** the PR workflow's artifact steps (`actions/upload-artifact@v7`,
  `actions/download-artifact@v8`) run on Node 24, so a self-hosted runner pool needs Actions
  Runner 2.327.1 or later.
- **Security:** enable the dependency graph (needed by dependency review), secret scanning and
  Dependabot alerts.

## Local hooks

```sh
picard install-hooks
```

The hooks need `picard` on the PATH. When it is missing they say how to install it and exit
non-zero (bypass once with `--no-verify`).

## Check it works

Open a PR whose title is `docs: try the pipeline (TICKET-1)` and confirm only the docs
checks run. Then a `fix:` PR touching code and confirm tiers 1 and 2 run. Merge both,
and confirm the Release PR appears and tracks the version.

## Rollout order for a big existing repo

1. Install with `pipeline-pr.yml` running but `pipeline-gate` **not** required. Watch plans
   in the job summaries for a week.
2. Mark tests (step 3) until `PICARD_REQUIRE_MARKERS=1` passes.
3. Require `pipeline-gate`, then enable the merge queue and require `merge-gate`.
4. Switch the release tier on last, once titles have been clean for a while.

## Updating picard

```sh
git -C ./picard pull                       # or check out the tag you want
picard update                              # refresh the scaffolded files
git diff                                   # review
```

Then bump `PICARD_REF` to the same tag or commit so CI runs the version you tested. `update`
replaces the `pipeline-*` workflows and actions and the hooks, and keeps your `pipeline.toml`,
release config and CODEOWNERS. `picard update --dry-run` lists what would change. Commit as
`chore(pipeline): update picard (TICKET)`.
