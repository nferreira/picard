"""Setting the repository up for the Pants engine, so nobody writes its files by hand.

`picard setup` writes the Pants configuration once, generates the build metadata for the
languages it handles and leaves anything that already exists alone. `picard setup --check` is
the CI side of it: it fails when code was added that has no build metadata yet.

Only Python is enabled. Go was tried: Pants tailors and lists a module but does not follow a
local `replace` or `go.work` edge between modules, so it would drop dependents. That would make
the answer smaller than the truth, which an impact engine must never do. Rust and JavaScript have
no stable backend, so those packages stay with the fallback engine.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from picard.graph.impact import SetupError

if TYPE_CHECKING:
    from picard.config import Config, Package

DEFAULT_VERSION = "2.33.1"
BUILD_FILE = "BUILD.pants"  # not BUILD: on a case-insensitive disk it would clash with build/
GITIGNORE_LINES = (".pants.d/", ".pids/", "dist/")
PYTHON_EXECUTORS = ("pytest", "uv-pytest")
TIMEOUT_SECONDS = 1800  # the first run downloads Pants and can take minutes
_ONLY_REQUIREMENTS = re.compile(r"(?:\s*python_requirements\([^)]*\)\s*)+")


def python_packages(cfg: Config) -> list[Package]:
    return [
        p for p in cfg.packages.values() if p.provider == "uv" or p.executor in PYTHON_EXECUTORS
    ]


def source_roots(root: Path, packages: Iterable[Package]) -> list[str]:
    """`/<package>/src` where it exists, `/<package>` otherwise, and `/` for tests and tools."""
    roots: list[str] = []
    for package in packages:
        base = "" if package.path == "." else f"/{package.path}"
        candidate = f"{base}/src" if (root / package.path / "src").is_dir() else base
        if candidate and candidate not in roots:
            roots.append(candidate)
    return [*roots, "/"]


def _python_constraint(root: Path) -> str | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["requires-python"]
    except (KeyError, ValueError):
        return None


def render_config(version: str, roots: list[str], python_constraint: str | None) -> str:
    quoted = ", ".join(f'"{r}"' for r in roots)
    lines = [
        "# Written by `picard setup`. Safe to edit: picard never overwrites this file.",
        "[GLOBAL]",
        f'pants_version = "{version}"',
        'backend_packages = ["pants.backend.python"]',
        'pants_ignore = ["/node_modules", ".venv", "/.picard", "/build", "/dist"]',
        "pantsd = false",
        "",
        "[source]",
        f"root_patterns = [{quoted}]",
        "",
        "[python]",
        "enable_resolves = false",
        "# in a uv workspace these make first-party imports ambiguous, and Pants drops the edges",
        "tailor_requirements_targets = false",
    ]
    if python_constraint:
        lines.append(f'interpreter_constraints = ["{python_constraint}"]')
    lines += [
        "",
        "[python-infer]",
        "# third-party imports are not part of the first-party graph",
        'unowned_dependency_behavior = "ignore"',
        "",
        "[tailor]",
        f'build_file_name = "{BUILD_FILE}"',
        "",
        "[anonymous-telemetry]",
        "enabled = false",
        "",
    ]
    return "\n".join(lines)


def _missing(result: subprocess.CompletedProcess[str]) -> str:
    """The "would create X" lines only: the rest of the tool's output names the tool."""
    lines = [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if line.startswith(("Would create", "Would update", "  - "))
    ]
    return "\n" + "\n".join(lines) if lines else ""


class PantsWorkspace:
    """Writes the Pants files for a repository and keeps them complete."""

    def __init__(
        self,
        root: Path,
        options: dict,
        packages: list[Package],
        say: Callable[[str], None],
    ) -> None:
        self.root, self.options, self.packages, self.say = root, options, packages, say
        command = options.get("command", "pants")
        self.argv = shlex.split(command) if isinstance(command, str) else list(command)

    def _pants(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603
                [*self.argv, *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupError(f"cannot run {self.argv[0]!r}: {exc}") from exc

    def setup(self) -> None:
        if not self.packages:
            self.say("no Python packages found: nothing to set up, the native engine answers")
            return
        self._write_config()
        self._ignore_generated()
        self._generate_build_files()

    def check(self) -> None:
        if not (self.root / "pants.toml").is_file():
            raise SetupError("pants.toml is missing: run `picard setup`")
        result = self._pants("tailor", "--check", "::")
        if result.returncode != 0:
            raise SetupError(f"build files are out of date: run `picard setup`{_missing(result)}")
        self.say("build files are complete")

    def _write_config(self) -> None:
        path = self.root / "pants.toml"
        if path.exists():
            self.say("pants.toml already exists: left as it is")
            return
        text = render_config(
            str(self.options.get("version", DEFAULT_VERSION)),
            source_roots(self.root, self.packages),
            _python_constraint(self.root),
        )
        path.write_text(text, encoding="utf-8")
        self.say("wrote pants.toml")

    def _ignore_generated(self) -> None:
        path = self.root / ".gitignore"
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        missing = [line for line in GITIGNORE_LINES if line not in existing]
        if not missing:
            return
        prefix = "" if not existing or path.read_text(encoding="utf-8").endswith("\n") else "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(prefix + "\n".join(missing) + "\n")
        self.say(f"added to .gitignore: {', '.join(missing)}")

    def _build_files(self) -> set[Path]:
        ignored = {"node_modules", ".venv", ".git", ".pants.d"}
        return {
            p
            for p in self.root.rglob(BUILD_FILE)
            if not ignored.intersection(p.relative_to(self.root).parts)
        }

    def _generate_build_files(self) -> None:
        self.say("generating build files (the first run downloads Pants and can take minutes)")
        before = self._build_files()
        result = self._pants("tailor", "::")
        if result.returncode != 0:
            detail = (result.stdout + result.stderr).strip()[-600:]
            raise SetupError(f"generating build files failed:\n{detail}")
        created = sorted(self._build_files() - before)
        # A pants.toml written by hand may still let tailor create third-party targets, which break
        # first-party edges in a uv workspace. Drop those files and say how to stop them coming back.
        removed = [
            p for p in created if _ONLY_REQUIREMENTS.fullmatch(p.read_text(encoding="utf-8"))
        ]
        for path in removed:
            path.unlink()
        if removed:
            self.say(
                f"removed {len(removed)} third-party target file(s); set "
                "`tailor_requirements_targets = false` under [python] in pants.toml"
            )
        self.say(f"created {len(created) - len(removed)} build file(s)")
