"""Built-in executors: pytest, uv, go, cargo, pnpm, npm and a generic make."""

from __future__ import annotations

import shlex

from picard.executors.base import Executor, SuiteContext


def _q(path: str) -> str:
    return shlex.quote(path)


class PytestExecutor(Executor):
    """pytest, with the tier's marker expression as `-m`. Options: launcher, linter."""

    name = "pytest"
    default_launcher = ""

    def command(self, ctx: SuiteContext) -> str | None:
        launcher = self.options.get("launcher", self.default_launcher)
        prefix = f"{launcher} " if launcher else ""
        path = _q(ctx.package.path)
        if ctx.suite == "test":
            marker = f' -m "{ctx.markers}"' if ctx.markers else ""
            return f"{prefix}pytest {path}{marker}"
        if ctx.suite == "lint":
            return f"{prefix}{self.options.get('linter', 'ruff check')} {path}"
        return None


class UvPytestExecutor(PytestExecutor):
    """pytest run through `uv run`, for uv workspaces."""

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


class PnpmExecutor(Executor):
    """pnpm workspaces. A `baseline` marker runs the package's `test:baseline` script."""

    name = "pnpm"

    def command(self, ctx: SuiteContext) -> str | None:
        if ctx.suite == "lint":
            return f"pnpm --filter {_q(ctx.package.name)} run lint"
        if ctx.suite != "test":
            return None
        script = "test:baseline" if ctx.markers.strip() == "baseline" else "test"
        return f"pnpm --filter {_q(ctx.package.name)} run {script}"


class NpmExecutor(Executor):
    """npm workspaces."""

    name = "npm"

    def command(self, ctx: SuiteContext) -> str | None:
        if ctx.suite not in ("lint", "test"):
            return None
        return f"npm run {ctx.suite} --workspace {_q(ctx.package.name)}"


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
