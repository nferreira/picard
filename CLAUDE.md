# picard

Working notes for AI sessions in this repository.

- picard is a Python package (`src/picard`, command `picard`), standard library only at
  runtime and Python 3.11+. Do not add a runtime dependency: it is a dependency of every
  repo's CI. Dev tools go in `[dependency-groups] dev`.
- `src/picard/templates/` is package data that `picard init` writes into other repositories:
  workflows, composite actions, hooks and a starter `pipeline.toml`. Keep it generic: picard
  and everything it scaffolds must not name any organisation, team, company repository or
  ticket prefix. Read it with `importlib.resources`, never a path relative to `__file__`.
- Files written into a host live only under `.github/` (workflows and actions prefixed
  `pipeline-`) and `.picard/`. `picard init` and `picard update` replace the files picard
  owns and never overwrite the files the repo owns. Keep that split and its tests.
- picard is affected-based CI: anything that narrows what runs must fail closed. An engine,
  provider or rule may widen the answer and never narrow it. Docs start at `docs/README.md` (tutorial, reference, dev),
  and a behaviour change updates them in the same PR.
- Workflows decide nothing. New behaviour is a plan field plus a test, then workflow wiring.
- PR titles and bodies are untrusted. Pass them through `env:`, never `${{ }}` in `run:`.
- A new extra needs three edits: `KNOWN_EXTRAS` in `config.py`, a step in
  `src/picard/templates/github/actions/pipeline-extra/action.yml`, and docs. A test checks the
  first two agree.
- Check before you hand over: `just check` (ruff, tests, starter config, actionlint). A wheel
  hides what an editable install shows, so after touching `templates/` run
  `uv build --wheel`, install it into a clean venv and run `picard init` in a scratch repo.
- Commits and PR titles: `type(scope): lowercase subject (TICKET)`.
