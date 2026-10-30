# 0004 · Rules for what the code graph cannot see

Status: accepted

## Context

Affected-package analysis (ADR 0003) covers source code. A large part of what changes in a repo
has no import graph: a JSON Schema, database migrations, a dataset that defines a model,
baseline expected outputs, config files, docs, workflows. Each still has a clear answer to "what
must run": validate the schema with a schema tool, replay the migrations, test and retrain that
one model. Without a way to say so, those changes either run nothing or run everything.

## Decision

Add declarative `[[rules]]`: a path pattern with captures, mapped to a small action vocabulary
(`none`, `full`, `package`, `own`, `require-review`, `run:<name>`). Actions are named commands
defined once. Resolution is explicit: first match wins per file, results are merged, `full`
absorbs everything, and a file nobody mapped runs everything. An inventory file resolves a
captured directory to an id and a status, so a missing or not-runnable target is a visible
warning, never a silent skip.

Rules are configuration plus about 200 lines of matching, and they call existing tools
(check-jsonschema, sqlfluff, pytest, a training script). No tool that I found maps paths to
named actions with captures and also simulates locally and explains itself: path-filter actions
only set booleans inside CI.

## Consequences

- Owners of an area change what runs for it by editing configuration.
- Every plan explains which rule matched which files.
- Captured names are validated because they reach shell commands, and unresolved names fail safe.
- `require-review` is recorded but not enforced yet; that is a CI policy decision.
- Rules can grow stale, so a verification harness should cover each rule (the demo's
  `verify/verify.py` does).
