# Rules, actions and inventories

The code graph says which packages a code change affects. Configs, schemas, database
definitions, datasets, baselines, docs and automation have no import graph, so **rules** say
what must run when those files change. Rules complement the graph; they do not replace it
([ADR 0004](../dev/adr/0004-rules-complement-the-graph.md)).

```toml
[rules_policy]
unmapped = "full"             # ignore | warn (default) | full: a file no rule and no package covers

[inventory.model]             # resolves the captured {model} to an id and a status
file = "models/inventory.toml"
entries = "model"             # the [[model]] tables in that file
runnable = ["trainable"]      # statuses that may run actions using {model}

[actions.train-model]         # a named command; never takes a file list
run = "python -m train --data models/{model_dir}/data.csv --out build/{model}.json"
tiers = [2]                   # optional: the tiers this command may run in
needs = ["trainable"]         # optional: inventory statuses this action requires

[[rules]]
name = "model dataset"
match = ["models/{model}/data/**"]       # {model} captures one path segment
actions = ["run:test-model", "run:train-model", "require-review"]
tiers = [0, 1, 2]             # optional, default [0, 1, 2]
profile = "full"              # optional: the profile these files demand (a docs rule lowers it)
```

## Resolution

1. Each changed file uses the **first** rule whose `match` fits, top to bottom.
2. The results of all files are **merged**. Identical actions run once.
3. A file a rule matches reaches the code graph only if the rule says `package` or `own`, so a
   dataset or YAML change does not run unit suites.
4. A file no rule matches goes to the code graph, as before.
5. `unmapped` decides what happens to a file that no rule, package, docs glob or global glob
   covers: `warn` (the default) says so in the plan and runs what it ran before, `full` runs
   everything (recommended once the repo is mapped), `ignore` does nothing and says nothing. No gate
   reads a warning, so `warn` cannot fail a pipeline.
6. `full` absorbs everything.

Glob syntax for `match`: `*` within a segment, `**` across segments, `?` one character, and
`{name}` to capture one path segment.

## Rule fields

| Key | Meaning |
|---|---|
| `name` | label shown in plans and used as the `rule` of a suite |
| `match` | globs. The first rule that matches a file handles it |
| `actions` | what the file triggers (below) |
| `tiers` | optional, default `[0, 1, 2]`. Tiers in which the rule's `run:` actions may run. An action's own `tiers` overrides it |
| `profile` | optional. The profile these files demand. A docs rule can lower it |

## Actions

| Action | Meaning |
|---|---|
| `none` | nothing. Combine with `profile = "docs"` for documentation |
| `full` | every package |
| `package` | the owning package and everything that depends on it (the graph) |
| `own` | the owning package only |
| `require-review` | recorded in the plan and shown as `REVIEW REQUIRED` |
| `run:<name>` | the command of `[actions.<name>]`, with captures filled in, as one suite |

### Which tiers an action honours

This is the part that surprises people.

| Action | What decides the tiers it runs in |
|---|---|
| `run:<name>` | the action's `tiers`, else the rule's `tiers`, else `[0, 1, 2]`. Never at tiers 3 and 4 |
| `own`, `package`, `full` | **the tier's own settings only**: they select *packages*, and the tier's `run` list and the profile's `tiers` decide whether they run. A rule's or action's `tiers` does **not** narrow them |

So `actions = ["own", "run:x"]` with `tiers = [2]` on `x` runs the owning package's lint and
tests at tiers 0, 1 and 2, and `x` only at 2. To run nothing at tiers 0 and 1, leave `own`,
`package` and `full` out of the rule.

### Variables in an action's command

`{markers}` is available. `{package}` and `{path}` are not, because they belong to package
commands. A capture such as `{model}` becomes the inventory id when an inventory with that name
exists, and `{model_dir}` is always the directory name.

## Inventories

An inventory turns a captured directory name into an id and a status, so an unknown or retired
item can never become a command.

| Key | Meaning |
|---|---|
| `file` | the TOML file listing the entries |
| `entries` | the name of the `[[...]]` tables in it |
| `runnable` | statuses that may run actions that use the capture |

An action's `needs` overrides `runnable` for that action. An action whose inventory status is
not allowed is skipped **with a warning naming the status**.

## Safety

Captured names end up in shell commands and come from paths a PR controls, so they must match
`[A-Za-z0-9._-]+`. Anything else, a name missing from the inventory, or an unreadable inventory
runs **everything** and warns, and is never turned into a command.

## Large pull requests

Rules are matched inside the planner, so no file list goes through a command line or an
environment variable. The plan lists at most 200 changed files plus the total
(`changed_files_total`), because one string over 128 KiB fails at process start. Use
`--files-from FILE` to simulate thousands of paths.

## `require-review`

Today it is data: the plan lists it and `simulate` prints it. Blocking a merge until a person
approves needs a CI decision (a required label, CODEOWNERS or an environment approval) that is
not made yet.

## Validating the config itself

A rule that only validates configuration, so editing it is checked and not retested:

```toml
[actions.picard-validate-config]
run = "picard validate-config"

[[rules]]
name = "picard config"
match = [".picard/**"]
actions = ["run:picard-validate-config"]
```
