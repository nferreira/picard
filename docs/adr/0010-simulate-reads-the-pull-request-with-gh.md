# 0010 · `simulate --pr` reads the pull request with the GitHub CLI

Status: accepted

## Context

CI plans a pull request from its title, body, labels and the commits it adds to its base. A local
run has the commits but not the rest, so previewing "what will CI do for PR 123" meant copying the
title and labels by hand into `--title` and `--label`, and that is exactly where a preview and the
real run drift apart. picard assumes no hosting platform and needs only git.

## Decision

- `picard simulate --pr [N|URL]` reads the pull request and plans every tier from it. It is limited
  to `simulate`, which is where a preview belongs; `plan` and `run` stay driven by CI's own inputs.
- The platform is reached through one module, `picard.pr`, which runs `gh pr view --json ...`.
  Nothing else in picard knows about it, so another platform means another function there. picard
  does not gain a dependency: without `gh` the option fails with what to pass by hand.
- The pull request supplies title, body, labels and the two commits, then the ordinary `--base`
  path does the diff, so a base missing from the clone fails closed in the same way. `--title` and
  `--body` replace the pull request's, `--label` adds to its labels, and the options that name a
  change (`--files`, `--staged`, `--working`, `--base`, `--local`) are refused.
- A pull request head that is not in the clone is fetched once (`pull/<N>/head`). If that fails the
  platform's file list is used, which cannot narrow a lockfile change, so it runs more, never less.
- The argument is a number or a pull request URL, validated before it reaches `gh`. Without a
  value picard lists recent pull requests of every state (`gh pr list --state all`, 30 newest) and
  asks which one, because the useful ones to replay are often merged or closed. The prompt is on
  stderr so structured output stays clean, Enter takes the checked-out branch's pull request, and
  with no terminal it prints the list and stops asking for a number rather than waiting.

## Consequences

- A preview now matches CI for the inputs that decide the plan.
- Whoever runs it needs `gh` logged in; it only reads.
- `affected` and `plan` do not take `--pr`. Adding it there is one line, but it should wait for a
  reason, since in CI those commands already receive the real values.
