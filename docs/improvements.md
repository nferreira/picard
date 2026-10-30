# Roadmap: what else can improve the pipeline

What is already built is in [tiers](tiers.md) and [architecture](architecture.md). These are
candidates, grouped by what they buy, with the tier each would land in. None is implemented.

## Speed and cost

| Idea | Tier | Notes |
|---|---|---|
| **Test sharding** by recorded duration (`pytest-split`, Jest `--shard`) | 1, 3, 4 | The matrix already exists. Add a `shards` key per package and expand it in the plan. |
| **Standard caching** (uv, pnpm, buildx) in `pipeline-run-suite` | all | One place to add it, every repo benefits. Key on lockfile hashes. |
| **Test impact analysis**: coverage maps from tier 4 pick tests, not just packages, for tier 1 | 1 | Biggest win for large suites. Needs a nightly job that publishes the map. |
| **Self-hosted runner pool** per `[runtime] runs_on` | all | Already supported by config. Keep fork PRs on hosted runners. |
| **Fail fast order**: lint before tests inside tier 1 | 1 | Cheap to add as a `needs` edge. |

## Trust in the signal

| Idea | Tier | Notes |
|---|---|---|
| **Flaky-test quarantine**: `@pytest.mark.flaky` runs in tier 4 only, retried with `pytest-rerunfailures`, tracked by the nightly issue | 1 to 4 | A flaky test blocking merges costs more than a missed bug it might catch. |
| **Coverage ratchet / patch coverage** on the diff | 2 | Fail on lowered coverage for changed lines, not on the total. |
| **Mutation testing** on the packages touched in the last week | 4 | Finds tests that pass without asserting anything. |
| **Contract tests** as the baseline marker for any service with consumers | 2 | The natural content of the must-pass list. |

## Supply chain and security

| Idea | Tier | Notes |
|---|---|---|
| **zizmor** as a `workflow-lint` companion: static audit of workflow security | 1 | Catches template injection and unpinned actions. |
| **SHA pinning** automated with Dependabot or `pinact` | all | Turns the "pin before relying on it" note into a check. |
| **SBOM** (syft) and **provenance attestations** (`actions/attest-build-provenance`) on release | 5 | Pairs with the Release tier's tag event. |
| **Image signing** (cosign, keyless via OIDC) and **Trivy** scan for container repos | 2, 5 | A `build-image` reusable action: buildx cache, scan, attest. |
| **OIDC to cloud providers** instead of long-lived secrets | 5 | Add to the release/deploy recipe in the docs. |
| **CodeQL** as an extra for languages it supports | 2, 4 | Alongside or instead of semgrep, per org policy. |

## Release and delivery

| Idea | Tier | Notes |
|---|---|---|
| **Auto-merge for Dependabot** when the `deps` profile is green and the bump is patch/minor | 2 | Needs the merge queue. Keeps dependency SLAs without manual PRs. |
| **Preview environments** for `feat` PRs, torn down on close | 2 | Gate on the profile so docs and chore PRs never create one. |
| **Release notes to chat and the issue tracker**: transition tickets named in the changelog on release | 5 | The ticket IDs are already in every title. |
| **Release PR check**: refuse to merge while the nightly is red | 5 | One status check read in `pipeline-release.yml`. |
| **Deploy recipe**: tag-triggered job after `release_created`, with environment approvals | 5+ | Platform-specific, so out of scope here by design. |

## Developer experience

| Idea | Tier | Notes |
|---|---|---|
| **Plan comment on the PR** (sticky): profile, packages, why, warnings | 1 | The job summary has it today. A sticky comment puts it where reviewers look. |
| **Label by type and size** (`feat`, `breaking`, `size/L`) | 1 | Derived from the plan, so labels cannot disagree with the pipeline. |
| **`picard explain`**: why did this file demand `full`? | 0 | The reasons are already collected. |
| **Scaffold command** (`picard init`) that detects packages and writes the first `pipeline.toml` | 0 | Lowers adoption friction for monorepos. |
| **DORA metrics** from tags, merge-queue and deploy events | 5 | Lead time and change failure rate fall out of data we already emit. |

## Platform

| Idea | Notes |
|---|---|
| **Reusable-workflow distribution** as an alternative to scaffolded workflows | Trade self-owned workflow files for central rollout. See ADR 0006. The planner would not change. |
| **Scheduled `picard update` PRs** | A nightly job that opens a PR when the pinned picard has a newer tag. Removes scaffold drift. |
| **`picard update --check`** | Fail CI when the scaffolded workflows differ from the pinned picard's templates. |
| **A second planner front end** (Go or Rust single binary) | Only if Python 3.11 on runners becomes a problem. |
