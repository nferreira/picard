# 4. Mark your tests

Tiers select tests by **marker**: tier 0 runs `unit`, tier 1 runs `unit or integration`,
tier 2 runs `baseline`, tier 3 runs `not slow`. A test with no marker is invisible to tiers 0
to 3, so this step decides what your pipeline actually checks.

> Skip this step if you select nothing by marker, for example when a tier's command is a
> `make` target that ignores `{markers}`. Tier 2 in a repository whose baseline is a single
> `make` target is the usual case.

## Register the markers

In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = ["--strict-markers"]
markers = [
    "unit: fast, no I/O",
    "integration: touches a real service",
    "baseline: must pass for any change",
    "slow: too slow for a PR",
]
```

## Mark by location, not by hand

Copy the plugin picard wrote into your test root:

```sh
cp .picard/templates/pytest/conftest.py tests/conftest.py
```

It marks tests without touching each one:

| By | Rule |
|---|---|
| directory | `tests/unit/**` is `unit`, `tests/integration/**` is `integration`, `tests/e2e/**` is `integration` and `slow` |
| fixture | a test using `db`, `postgres` or `redis` is `integration` |

## Mark the must-pass tests by hand

`baseline` is a judgement about what must always pass (contract tests, migration checks, smoke
tests, the critical path), so it is the one marker you assign yourself:

```python
@pytest.mark.baseline
def test_schema_matches_contract(): ...
```

Declare which packages always take part in tier 2 with `baseline = true`:

```toml
[[packages]]
name = "api"
baseline = true
```

or pull one in when certain files change:

```toml
[[path_rules]]
globs = ["db/migrations/**"]
packages = ["worker"]
```

## Enforce it

Once the backlog is marked, make an unmarked test an error:

```sh
PICARD_REQUIRE_MARKERS=1 uv run pytest --collect-only -q
```

Collection fails and lists the unmarked tests. Turn it on in CI when it is clean.

## Check it

```sh
uv run pytest --collect-only -q -m unit | tail -1      # how many unit tests tier 0 will run
uv run pytest --collect-only -q -m baseline | tail -1  # and tier 2
```

**Next:** [rules for files with no code](05-rules-for-non-code-files.md).
