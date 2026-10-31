"""Ask bazel-diff which Bazel targets a change affects.

    [impact]
    engine    = "bazel-diff"
    command   = "bazel-diff"   # the launcher (default "bazel-diff")
    bazel     = "bazel"        # the Bazel (or Bazelisk) binary bazel-diff runs (default "bazel")
    workspace = "."            # the Bazel workspace, relative to the repository root
    fallback  = "native"
    timeout   = 1800           # seconds for each call; hashing a large workspace is slow

bazel-diff compares the target hashes of two checked-out revisions. picard needs a git range
(`--base` or `--local`), checks the base out in a temporary `git worktree`, runs
`generate-hashes` there and in the working tree, then `get-impacted-targets`. The worktree is
always removed. picard never relies on `--modified-filepaths`.

A target `//a/b:c` belongs to the picard package that owns `a/b`; `@external` targets are
ignored. The packages bazel-diff knows are those with a BUILD or BUILD.bazel file at their root.
If the head of the change is not the commit checked out (`HEAD`), the working tree cannot be
hashed as the head, which is a failure that widens. The shared policy (fallback, dependents,
failures) is in `tool.py`.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from picard.graph.engines.tool import ToolImpactEngine
from picard.graph.impact import owner

if TYPE_CHECKING:
    from picard.config import Config

INSTALL_HELP = """\
bazel-diff is not installed (or not on your PATH). Download a release binary, or add it as a
Bazel module and set command = "bazel run @bazel-diff//:bazel-diff --":
https://github.com/Tinder/bazel-diff

Without bazel-diff picard answers with the native engine. To stop looking for it, set
`engine = "native"` under `[impact]`."""


class BazelDiffImpactEngine(ToolImpactEngine):
    name = "bazel-diff"
    label = "bazel-diff"
    default_command = "bazel-diff"
    default_timeout = 1800
    install = INSTALL_HELP
    needs_revisions = True

    def launchers(self) -> list[tuple[str, str]]:
        return [*super().launchers(), ("Bazel", str(self.options.get("bazel", "bazel")))]

    def known(self, cfg: Config) -> set[str]:
        root = self._root(cfg)
        return {
            p.name
            for p in cfg.packages.values()
            if (root / p.path / "BUILD").is_file() or (root / p.path / "BUILD.bazel").is_file()
        }

    def _git(self, cfg: Config, *args: str) -> str:
        return self.run(cfg, args, argv=["git"]).strip()

    def query(self, cfg: Config, files: list[str]) -> set[str]:
        revisions = self.revisions
        if revisions is None:  # compute() already handed this to the fallback
            raise RuntimeError("no git range to ask bazel-diff about")
        if self._git(cfg, "rev-parse", "--verify", f"{revisions.head}^{{commit}}") != self._git(
            cfg, "rev-parse", "--verify", "HEAD^{commit}"
        ):
            raise RuntimeError(
                f"head {revisions.head} is not the checked-out HEAD, so it cannot be hashed"
            )
        root = self._root(cfg)
        workspace = str(self.options.get("workspace", "."))
        bazel = str(self.options.get("bazel", "bazel"))
        scratch = Path(tempfile.mkdtemp(prefix="picard-bazel-diff-"))
        base_tree = scratch / "base"
        added = False
        try:
            self._git(cfg, "worktree", "add", "--detach", str(base_tree), revisions.base)
            added = True
            starting, final, out = scratch / "base.json", scratch / "head.json", scratch / "out"
            for tree, hashes in ((base_tree, starting), (root, final)):
                self.run(
                    cfg,
                    ["generate-hashes", "-w", str(tree / workspace), "-b", bazel, str(hashes)],
                    cwd=tree,
                )
            self.run(
                cfg,
                [
                    "get-impacted-targets",
                    "--startingHashes",
                    str(starting),
                    "--finalHashes",
                    str(final),
                    "--workspacePath",
                    str(root / workspace),
                    "-o",
                    str(out),
                ],
            )
            targets = out.read_text(encoding="utf-8").splitlines()
        finally:
            if added:
                try:
                    self._git(cfg, "worktree", "remove", "--force", str(base_tree))
                except (OSError, RuntimeError) as exc:
                    self.log(f"could not remove the worktree {base_tree}: {exc}")
            shutil.rmtree(scratch, ignore_errors=True)
            if added:
                try:
                    self._git(cfg, "worktree", "prune")
                except (OSError, RuntimeError) as exc:
                    self.log(f"git worktree prune failed: {exc}")
        return self._packages(cfg, targets, workspace)

    def _packages(self, cfg: Config, targets: list[str], workspace: str) -> set[str]:
        found: set[str] = set()
        ignored: list[str] = []
        prefix = "" if workspace in ("", ".") else workspace.strip("/") + "/"
        for raw in targets:
            target = raw.strip()
            if not target or target.startswith("@"):  # blank, or an external repository
                continue
            if not target.startswith("//"):
                raise RuntimeError(f"bazel-diff printed {target[:80]!r}, not a //label")
            package = target[2:].split(":", 1)[0].strip("/")
            path = (prefix + package).strip("/") or "."
            name = owner(cfg, path) if path != "." else _root_package(cfg)
            if name is None:
                ignored.append(target)
            else:
                found.add(name)
        if ignored:
            self.log(f"targets outside picard packages, ignored: {ignored[:20]}")
        return found


def _root_package(cfg: Config) -> str | None:
    return next((p.name for p in cfg.packages.values() if p.path == "."), None)
