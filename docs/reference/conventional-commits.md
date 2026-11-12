# Conventional Commits as pipeline control

## Why the PR title

Squash merge is on and the squash commit message is the PR title. So the title is the commit
on `main`, the line Release Please reads, and the one place anyone has to think about format.
`pipeline-gate` requires a valid title (job `pr-title`) and the plan is built from it. Local commit messages
are checked for format only (`commit-msg` hook), because they disappear at squash.

## Format

```
<type>(<scope>)<!>: <subject> (<TICKET-ID>)
```

| Part | Rule |
|---|---|
| type | one of `commits.types` in `pipeline.toml` |
| scope | optional, lowercase area name. A ticket-like token such as `gpu-01` is rejected, the ticket goes at the end |
| `!` | after the type or scope, marks a breaking change |
| subject | starts lowercase |
| ticket | ticket ID in parentheses at the end, e.g. `(ABC-123)`. Required unless the title matches `ticket_exempt` (release PRs, Dependabot) |

A breaking change can also be declared with a `BREAKING CHANGE: <what and why>` footer in the
PR body.

```
feat(config): move settings into the database (ABC-103)
fix(auth): reject expired refresh tokens (ABC-212)
feat(api)!: drop the v1 search endpoint (ABC-300)
chore(deps): bump aiohttp                       # exempt from the ticket rule
chore: release 0.8.0                            # exempt: the Release PR
```

## From title to plan

For a pull request the planner takes the strongest of three signals.

1. **Type.** `[types.<type>]` gives a profile and a release effect. `[[overrides]]` can
   replace both for titles matching a regex, first match wins (that is how
   `chore(deps)` becomes the `deps` profile).
2. **Breaking.** `!` or a `BREAKING CHANGE:` footer raises the profile to at least
   `[breaking] profile`, sets the release effect, forces the tiers in `[breaking] tiers` and
   adds `[breaking] extras`.
3. **Paths.** Each changed file demands a minimum profile: `[paths] docs` globs demand the
   lowest, `[[paths.floor]]` rules demand theirs, anything else demands `[paths] default`.

The effective profile is the one with the highest `rank`. Then the profile decides:

- `run_suites`: whether test suites run at all.
- `tiers`: which PR tiers apply (a profile without tier 2 skips the baseline).
- `extras.N`: which extra jobs run at tier N.

### Titles can raise the bar, never lower it

| Situation | Result |
|---|---|
| `docs:` title, PR edits a source file | profile becomes `full`, warning shown |
| `feat:` title, PR edits only markdown | profile stays `full` (title wins upward), warning notes the release effect is still `minor` |
| `chore:` title, PR edits `.github/workflows` | `infra`: workflow lint and tier 2 run |
| title cannot be parsed | `commits.fallback_profile` (default `full`), release `none`, title check fails |
| title valid but ticket missing | plan unchanged, only the title check fails |

The warnings and reasons are written to the job summary so a reviewer sees why a PR ran what
it ran.

## Release effect

Release Please, not picard, performs the release. picard only reports the effect so
you can see it in the PR before merging.

| Type | Effect | Notes |
|---|---|---|
| `feat` | minor | |
| `fix`, `perf` | patch | |
| breaking | major | minor while below 1.0 with `bump-minor-pre-major: true` |
| `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `style`, `revert` | none | do not cut a release on their own |

Set `[breaking] release = "minor"` while a repo is below 1.0 so the reported effect matches
what Release Please will do.

## Tuning

- Stricter for a type: change `[types.chore] profile = "infra"`.
- A new bot with its own title shape: add an `[[overrides]]` entry and a `ticket_exempt`
  pattern.
- Stop `perf` from releasing: `[types.perf] release = "none"` (and make Release Please agree
  with `changelog-sections` and `release-as`, since it owns the actual bump).
- Run `picard validate-config` after any edit, and
  `picard plan --tier 1 --base main --title "..." --format text` to preview.
