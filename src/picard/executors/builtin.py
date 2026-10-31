"""Built-in executors: pytest, uv, go, cargo, pnpm, npm and a generic make.

Each executor passes the tool's own flags and never reimplements them: shards go to
pytest-split or Jest's `--shard`, retries to pytest-rerunfailures, test selection to
pytest-impacted or Jest's `--changedSince`, and the `deps` suite to deptry or knip. Shards,
retries and selection change the `test` suite only, and `deps` runs only when a tier lists it,
so a tier that asks for none of this gets the same commands as before.

Test selection narrows inside a suite. It is added only when the `selection` option is set and
the planner passed a `selection_base`, which it does only for a committed git range: no base, no
selection, and the whole suite runs.
"""

from __future__ import annotations

import shlex

from picard.executors.base import Executor, SuiteContext

DEFAULT_PYTHON_DEPS_CHECKER = "deptry"
DEFAULT_JS_DEPS_CHECKER = "knip --include dependencies,unlisted"


def _q(path: str) -> str:
    return shlex.quote(path)


def _module(name: str) -> str:
    """The import name pytest-impacted looks for: `my-pkg.core` -> `my_pkg_core`."""
    return name.replace("-", "_").replace(".", "_")


class PytestExecutor(Executor):
    """pytest, with the tier's marker expression as `-m`.

    Options: `launcher`, `linter`, `deps_checker` (default deptry), `retries`
    (pytest-rerunfailures `--reruns`), `selection = "impacted"` (pytest-impacted in branch mode).
    Shards use pytest-split (`--splits N --group k`).
    """

    name = "pytest"
    default_launcher = ""
    shardable = frozenset({"test"})
    retryable = True
    selections = ("impacted",)

    def command(self, ctx: SuiteContext) -> str | None:
        launcher = self.options.get("launcher", self.default_launcher)
        prefix = f"{launcher} " if launcher else ""
        path = _q(ctx.package.path)
        if ctx.suite == "test":
            marker = f' -m "{ctx.markers}"' if ctx.markers else ""
            return f"{prefix}pytest {path}{marker}{self._test_flags(ctx)}"
        if ctx.suite == "lint":
            return f"{prefix}{self.options.get('linter', 'ruff check')} {path}"
        if ctx.suite == "deps":
            checker = self.options.get("deps_checker", DEFAULT_PYTHON_DEPS_CHECKER)
            return f"{prefix}{checker} {path}"
        return None

    def _test_flags(self, ctx: SuiteContext) -> str:
        flags = ""
        retries = self.options.get("retries", 0)
        if retries:
            flags += f" --reruns {retries}"
        if ctx.shard is not None:
            flags += f" --splits {ctx.shard.total} --group {ctx.shard.index}"
        if self.options.get("selection") == "impacted" and ctx.selection_base:
            flags += (
                f" --impacted --impacted-module={_q(_module(ctx.package.name))}"
                f" --impacted-git-mode=branch --impacted-base-branch={_q(ctx.selection_base)}"
            )
        return flags


class UvPytestExecutor(PytestExecutor):
    """pytest run through `uv run`, for uv workspaces. Same options as pytest."""

    name = "uv-pytest"
    default_launcher = "uv run --no-sync"


class GoTestExecutor(Executor):
    """go test and go vet inside the module directory. unit -> -short, integration -> -tags."""

    name = "go-test"

    def command(self, ctx: SuiteContext) -> str | None:
        cd = f"cd {_q(ctx.package.path)} && "
        if ctx.suite == "lint":
            return f"{cd}go vet ./..."
        if ctx.suite != "test":
            return None
        flags = ""
        if "not slow" in ctx.markers or ctx.markers.strip() == "unit":
            flags += " -short"
        if "integration" in ctx.markers and "not integration" not in ctx.markers:
            flags += " -tags integration"
        return f"{cd}go test{flags} ./..."


class CargoTestExecutor(Executor):
    """cargo test and cargo check for a crate. unit -> --lib."""

    name = "cargo-test"

    def command(self, ctx: SuiteContext) -> str | None:
        manifest = "Cargo.toml" if ctx.package.path == "." else f"{ctx.package.path}/Cargo.toml"
        manifest = _q(manifest)
        if ctx.suite == "lint":
            return f"cargo check --manifest-path {manifest}"
        if ctx.suite != "test":
            return None
        scope = " --lib" if ctx.markers.strip() == "unit" else ""
        return f"cargo test --manifest-path {manifest}{scope}"


def _jest_args(ctx: SuiteContext, selection: str | None) -> str:
    """The arguments passed to the test script after a single `--`, or nothing.

    `--shard=k/N` and `--changedSince=<base>` are Jest's own flags, so the package's `test`
    script must run Jest or a runner that accepts them.
    """
    args = []
    if ctx.shard is not None:
        args.append(f"--shard={ctx.shard.index}/{ctx.shard.total}")
    if selection == "changed-since" and ctx.selection_base:
        args.append(f"--changedSince={_q(ctx.selection_base)}")
    return f" -- {' '.join(args)}" if args else ""


class PnpmExecutor(Executor):
    """pnpm workspaces. A `baseline` marker runs the package's `test:baseline` script.

    Options: `deps_checker` (default knip), `selection = "changed-since"` (Jest
    `--changedSince`). Shards use Jest's `--shard=k/N`. No `retries`: Jest has no CLI retry flag,
    so retries stay in the test runner's own config.
    """

    name = "pnpm"
    shardable = frozenset({"test"})
    selections = ("changed-since",)

    def command(self, ctx: SuiteContext) -> str | None:
        if ctx.suite == "lint":
            return f"pnpm --filter {_q(ctx.package.name)} run lint"
        if ctx.suite == "deps":
            checker = self.options.get("deps_checker", DEFAULT_JS_DEPS_CHECKER)
            return f"pnpm exec {checker} --workspace {_q(ctx.package.path)}"
        if ctx.suite != "test":
            return None
        script = "test:baseline" if ctx.markers.strip() == "baseline" else "test"
        extra = _jest_args(ctx, self.options.get("selection"))
        return f"pnpm --filter {_q(ctx.package.name)} run {script}{extra}"


class NpmExecutor(Executor):
    """npm workspaces. Same options, shard flag and `deps` suite as pnpm, through `npx`."""

    name = "npm"
    shardable = frozenset({"test"})
    selections = ("changed-since",)

    def command(self, ctx: SuiteContext) -> str | None:
        if ctx.suite == "deps":
            checker = self.options.get("deps_checker", DEFAULT_JS_DEPS_CHECKER)
            return f"npx --no-install {checker} --workspace {_q(ctx.package.path)}"
        if ctx.suite not in ("lint", "test"):
            return None
        extra = _jest_args(ctx, self.options.get("selection")) if ctx.suite == "test" else ""
        return f"npm run {ctx.suite} --workspace {_q(ctx.package.name)}{extra}"


class MakeExecutor(Executor):
    """Any language with a Makefile: `make -C <path> <suite>`. Option: prefix for target names."""

    name = "make"

    def command(self, ctx: SuiteContext) -> str | None:
        target = f"{self.options.get('prefix', '')}{ctx.suite}"
        return f"make -C {_q(ctx.package.path)} {_q(target)}"


BUILTIN_EXECUTORS = {
    cls.name: cls
    for cls in (
        PytestExecutor,
        UvPytestExecutor,
        GoTestExecutor,
        CargoTestExecutor,
        PnpmExecutor,
        NpmExecutor,
        MakeExecutor,
    )
}
