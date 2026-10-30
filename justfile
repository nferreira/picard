# Tasks for developing picard. Run from the repo root: just <task>
set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Install picard (editable) and its dev tools.
sync:
    uv sync

# Everything CI runs.
check: lint test validate workflows

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff format .
    uv run ruff check --fix .

test *args:
    uv run pytest {{args}}

# The starter pipeline.toml must stay valid.
validate:
    uv run picard validate-config --config src/picard/templates/pipeline.toml

# Lint the workflows picard scaffolds into other repos, and its own CI.
workflows:
    actionlint src/picard/templates/github/workflows/*.yml .github/workflows/*.yml

# Preview what a tier would do for the current branch: `just plan 1`.
plan tier="0":
    uv run picard plan --config src/picard/templates/pipeline.toml --tier {{tier}} --local --format text
