# 6. Titles and releases

The pull request title is the commit on `main` (squash merge). picard validates it and uses
it twice: to decide **how much to verify** and **what release the change produces**.

## Write a title

```
<type>(<scope>)<!>: <subject> (<TICKET-ID>)
```

```
feat(config): move settings into the database (ABC-103)
fix(auth): reject expired refresh tokens (ABC-212)
feat(api)!: drop the v1 search endpoint (ABC-300)
```

Check one:

```sh
picard check-title "fix: handle nil (ABC-2)"
```

## See what a type demands

```sh
picard simulate --files src/app.py --title "docs: tidy (ABC-1)"      # warns: source edited under docs
picard simulate --files README.md  --title "docs: tidy (ABC-1)"      # light: no test suites
```

A title can **raise** the bar and never lower it. A `docs:` title on a PR that edits source
runs the full profile and warns. An unparseable title gets the strictest profile.

## Tune a type

```toml
[types.chore]
profile = "infra"        # chores now run workflow lint and tier 2
release = "none"
```

Profiles set which tiers apply and which extras run:

```toml
[profiles.light]
rank = 1
tiers = [0, 1]           # no baseline for light changes
[profiles.light.extras]
1 = ["secrets"]
```

The strongest profile among the title's type, an `[[overrides]]` match, a breaking change
and the changed paths wins.

## Bots and release PRs

Titles you do not write (Dependabot, the release PR) need an exemption from the ticket rule
and, usually, their own profile:

```toml
[commits]
ticket_exempt = ['^chore: release ', '^(chore|build)\((deps|deps-dev)\)']

[[overrides]]
match = '^(chore|build)\((deps|deps-dev)\)'
profile = "deps"
```

## Releases

picard only **reports** the release effect (`patch`, `minor`, `major`, `none`) so you see it in
the PR. Release Please performs the release from the same titles. Set
`[breaking] release = "minor"` while the repository is below 1.0 so the two agree.

**Next:** [CI, hooks and rollout](07-ci-and-rollout.md).
