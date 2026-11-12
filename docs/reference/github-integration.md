# GitHub integration

What `picard init` writes, what each file does, and what the repository must configure.

## What `init` writes

```
.github/workflows/pipeline-*.yml    one workflow per tier, plus the rc tag     picard owns
.github/actions/pipeline-*/         composite actions the workflows call       picard owns
.picard/hooks/                      commit-msg, pre-push                       picard owns
.picard/templates/pytest/           marker plugin to copy into a test root     picard owns
.picard/pipeline.toml               the one file a repo edits                  you own
.github/release-please/             release-please config and manifest         you own
CODEOWNERS, dependabot.yaml, pull_request_template.md                          you own
```

`picard update` replaces what picard owns, writes what you own only when it is missing, and
touches nothing else. `picard update --dry-run` lists what would change.

## Workflows

| Workflow | Trigger | Tier |
|---|---|---|
| `pipeline-pr.yml` | `pull_request` | 1 and 2, plus the `pipeline-gate` job |
| `pipeline-merge-queue.yml` | `merge_group` | 3, reports `merge-gate` |
| `pipeline-nightly.yml` | `schedule` | 4, opens or updates one tracking issue on failure |
| `pipeline-release.yml` | push to `main` | 5, Release Please |
| `pipeline-rc-tag.yml` | open Release PR | pushes `vX.Y.Z-rc.N` for pre-production deploys |

Workflows decide nothing. Each has a plan job, matrix jobs, extras and a gate.

## Composite actions

| Action | Responsibility |
|---|---|
| `pipeline-setup-picard` | installs the pinned picard (`PICARD_REPOSITORY`, `PICARD_REF`, `PICARD_TOKEN`) |
| `pipeline-plan` | runs picard, exports `plan`, `matrix`, `extras`, `runtime`, `skip`, writes a job summary |
| `pipeline-check-title` | validates the PR title with the same parser the plan uses |
| `pipeline-run-suite` | sets up Python and Node from `[runtime]`, then runs one suite command |
| `pipeline-extra` | runs one named extra |

Extras: `secrets` (gitleaks), `workflow-lint` (actionlint), `semgrep`, `dependency-review`
(PRs only), `link-check` (local links), `breaking-change-checks` (migration section and
footer).

## Hooks

| Hook | Runs |
|---|---|
| `commit-msg` | `picard check-title --file ... --no-ticket`: format only, the ticket rule applies to the PR title |
| `pre-push` | `picard run --tier 0 --local` |

Install them once per clone with `picard install-hooks`. They need `picard` on the PATH. When it
is missing they say how to install it and exit non-zero. Bypass once with `--no-verify`.

## Repository variables and secrets

| Name | Kind | Purpose |
|---|---|---|
| `PICARD_REPOSITORY` | variable | `owner/name` of the picard repository |
| `PICARD_REF` | variable | a tag, or better a full commit SHA, so CI runs the version you tested |
| `PICARD_TOKEN` | secret | read access to a private picard repository. Reaches only the install step |
| `RELEASE_APP_ID` | variable | the GitHub App that tags releases |
| `RELEASE_APP_PRIVATE_KEY` | secret | its private key |

Fork PRs get no secrets, so keep picard readable for them.

## GitHub settings

- **Branch protection on `main`:** require the checks `pipeline-gate` and `merge-gate`, a
  code-owner review from someone other than the author, and signed commits.
- **Merge queue:** enable it for `main`.
- **Squash merge only,** with the commit message set to **Pull request title**.
- **Actions:** allow Actions from the organization, and pin third-party actions to SHAs.
- **Security:** enable the dependency graph (needed by dependency review), secret scanning and
  Dependabot alerts.
- **Release App:** contents and pull-requests write.

## Placeholders to fill

| File | Replace |
|---|---|
| `.github/CODEOWNERS` | `@REPLACE_ME` with the real owner |
| `.github/release-please/config.json` | `release-type` if the repo is not language-agnostic (`python`, `node`, `go`, ...) |

## Why one gate check

Required checks plus workflow-level `paths:` filters deadlock: a skipped workflow never reports
its required check and the PR waits forever. Here every job always exists, the plan decides
whether it has work, and a single `pipeline-gate` (tiers 1 and 2) or `merge-gate` (tier 3)
turns the whole run into one pass or fail. A job the plan said must run that did not succeed
fails the gate, so a silent skip cannot go green.

## Security notes for editors of the workflows

- PR titles and bodies are passed as action inputs, mapped to `env:` and read as
  `$PICARD_TITLE`. Never interpolate `${{ github.event... }}` in a `run:` step.
- Workflows default to `contents: read`. Only release and the nightly failure report raise it.
- Checkout with `persist-credentials: false` in every job that does not push.
- Do not add `pull_request_target` and run PR code in it.

Full model: [architecture](../dev/architecture.md#security-model).
