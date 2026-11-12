# The plan

A plan is the one JSON document everything reads: the workflows (`suites` becomes the job
matrix, `extras` a second matrix, `runtime` the runner and toolchains, `skip` short-circuits a
tier), the local CLI, and `simulate`. Get one with `picard plan --tier N`.

```jsonc
{
  "schema": 1,
  "tier": 1, "name": "pr-fast-gate", "compare": "pr-base",
  "skip": false, "skip_reason": null,
  "profile": "full",           // docs | light | infra | deps | full (config-defined)
  "release": "minor",          // none | patch | minor | major
  "commits": [{ "title": "...", "valid": true, "type": "feat", "scope": "api",
                "breaking": false, "ticket": "ABC-1", "errors": [] }],
  "changed_files": ["apps/api/x.py"],
  "changed_files_total": 1,    // the list above is capped at 200 entries
  "packages": ["api", "worker"],
  "markers": "unit or integration",
  "suites": [{ "id": "api:test", "package": "api", "suite": "test",
               "command": "pytest apps/api -m \"unit or integration\"" },
             { "id": "rule:baseline-yardi", "package": null, "suite": "baseline-yardi",
               "command": "make baseline-check-yardi", "rule": "yardi baseline" }],
  "extras": ["secrets"],
  "runtime": { "runs_on": "ubuntu-24.04", "python_version": "3.12",
               "node_version": "", "setup_command": "..." },
  "reasons": ["type 'feat' -> profile 'full'"],
  "warnings": []
}
```

| Field | Meaning |
|---|---|
| `tier`, `name`, `compare` | the `[tiers.N]` this plan is for |
| `skip`, `skip_reason` | the tier has nothing to do (a profile without this tier, a docs-only change) |
| `profile`, `release` | the strongest profile demanded, and the release effect Release Please will see |
| `commits` | each title parsed: type, scope, breaking flag, ticket, errors |
| `changed_files` | at most 200 paths; `changed_files_total` is the real count |
| `packages` | the packages selected for this tier (empty for rule-only changes) |
| `markers` | the tier's marker expression, substituted into `{markers}` |
| `suites` | one entry per command to run. Package suites have `package` set; rule actions have `package: null` and a `rule` |
| `extras` | extra jobs the profile enables at this tier |
| `runtime` | runner label, Python and Node versions, the setup command |
| `reasons` | why the plan looks like this (shown in the job summary) |
| `warnings` | for example a `docs:` title on a source change, an engine fallback, an unmapped file |

A rule action's suite id is `rule:<action>` followed by `[capture=value]` for each capture, so
one command per action and captures runs once even when several files or rules asked for it.

Tiers 3 and 4 set `compare = "none"`: they ignore the diff and the title and select every
package. Rule actions are not added at those tiers.

`schema` is bumped when a field changes meaning. Adding a field does not bump it.
