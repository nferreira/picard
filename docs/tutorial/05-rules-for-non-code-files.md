# 5. Rules for files with no code

The dependency graph only sees code. A YAML config, a dataset, a schema or a migration has no
imports, so a change to one would either run nothing or, with `unmapped = "full"`, everything.
`[[rules]]` say exactly what such a file triggers.

This step builds a realistic rule: **a change to one vendor's integration config runs only
that vendor's end-to-end check, and only in tier 2.**

## 1. Name the command

An action is a named command. Give it the tiers it may run in:

```toml
[actions.check-acme]
run = "make e2e-check-acme"
tiers = [2]
```

## 2. Match the files

```toml
[[rules]]
name = "acme config"
match = ["integrations/acme/**"]
actions = ["run:check-acme"]
profile = "full"
```

`profile = "full"` says these files demand the full profile, so a `docs:` title cannot skip
them. Without it, a YAML file falls to `[paths] default`.

## 3. Check what happens at each tier

```sh
for t in 0 1 2; do
  picard simulate --tiers $t --title "fix(acme): x (ABC-1)" \
      --files integrations/acme/mapping.yaml --commands
done
```

```
tier 0   packages none   0 suite(s)
tier 1   packages none   0 suite(s)  secrets
tier 2   packages none   1 suite(s)
         rule:check-acme   make e2e-check-acme
```

Nothing runs at tiers 0 and 1, and the one command runs at tier 2. That is the behaviour of a
rule whose only action is `run:`.

## 4. Decide what the code graph does

The actions in a rule decide whether the graph is involved:

| Action | Effect |
|---|---|
| `run:<name>` | runs that command; the graph is **not** consulted for these files |
| `own` | the owning package only (its lint and tests, at every tier) |
| `package` | the owning package and everything that depends on it |
| `full` | every package |
| `none` | nothing |

To also run the integration package's unit tests when its config changes, add `own`. Be aware
that `own` runs at **every tier**; neither a rule's `tiers` nor an action's `tiers` narrows it.
They only narrow `run:` actions. If you want "nothing at tier 0", leave `own` out.

## 5. Order matters

The first rule that matches a file wins. Put specific rules before general ones:

```toml
[[rules]]
name = "acme config"
match = ["integrations/acme/**"]
actions = ["run:check-acme"]

[[rules]]
name = "any integration config"
match = ["integrations/{vendor}/**"]      # {vendor} captures one path segment
actions = ["own"]
```

## 6. When the code behind it changes

A change to the code the job uses should run **every** vendor check, not one. Attach the
command to the package that owns that code, at the tier that runs it:

```toml
[[packages]]
name = "etl"
[packages.commands]
"test@pr-baseline" = "make e2e-check-all"
```

Any change to `etl`, or to a library it depends on, reaches `etl` through the graph, so tier 2
runs the whole check.

## 7. Per-item rules with an inventory

For rules like "a change to one model's dataset retrains that model", capture the name and
resolve it against a list, so a typo or a retired item cannot become a command. See
[rules reference](../reference/rules.md#inventories).

## Check it

Simulate the files of each vendor and of the shared code, and confirm each runs only what you
meant. Replaying a real merged PR with `picard simulate --pr` is the best test.

**Next:** [titles and releases](06-titles-and-releases.md).
