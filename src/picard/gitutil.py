"""Thin git wrappers. Arguments are always passed as a list, never through a shell."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise GitError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return result.stdout


def merge_base(a: str, b: str, cwd: Path | None = None) -> str:
    return _git("merge-base", a, b, cwd=cwd).strip()


def default_base_ref(cwd: Path | None = None) -> str:
    """origin/main when it exists, else main."""
    for ref in ("origin/main", "main"):
        try:
            _git("rev-parse", "--verify", "--quiet", ref, cwd=cwd)
            return ref
        except GitError:
            continue
    raise GitError("neither origin/main nor main exists; pass --base")


def changed_files(base: str, head: str, cwd: Path | None = None) -> list[str]:
    """Files changed on head since it diverged from base (three-dot diff, renames split)."""
    out = _git("diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", cwd=cwd)
    return [p for p in out.split("\0") if p]


def subjects(base: str, head: str, cwd: Path | None = None) -> list[str]:
    out = _git("log", "--format=%s", f"{base}..{head}", cwd=cwd)
    return [line for line in out.splitlines() if line]


def staged_files(cwd: Path | None = None) -> list[str]:
    """Files in the index that differ from HEAD."""
    out = _git("diff", "--cached", "--name-only", "--no-renames", "-z", cwd=cwd)
    return [p for p in out.split("\0") if p]


def working_files(cwd: Path | None = None) -> list[str]:
    """Every uncommitted change: staged, unstaged and untracked (not ignored) files."""
    tracked = _git("diff", "HEAD", "--name-only", "--no-renames", "-z", cwd=cwd)
    untracked = _git("ls-files", "--others", "--exclude-standard", "-z", cwd=cwd)
    return sorted({p for p in (tracked + untracked).split("\0") if p})


# ── "what is different locally" ────────────────────────────────────────────────────────


def _try(*args: str, cwd: Path | None = None) -> str | None:
    try:
        return _git(*args, cwd=cwd).strip() or None
    except GitError:
        return None


def current_branch(cwd: Path | None = None) -> str | None:
    """The checked-out branch, or None on a detached HEAD."""
    return _try("symbolic-ref", "--short", "-q", "HEAD", cwd=cwd)


def upstream(cwd: Path | None = None) -> str | None:
    """The remote-tracking branch of the current branch, e.g. origin/feature."""
    return _try("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", cwd=cwd)


def _ref_exists(ref: str, cwd: Path | None = None) -> bool:
    return _try("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=cwd) is not None


def _created_from(branch: str, cwd: Path | None = None) -> str | None:
    """The branch named in the oldest reflog entry ("branch: Created from main"), if any."""
    log = _try("reflog", "show", "--format=%gs", branch, cwd=cwd)
    if not log:
        return None
    match = re.match(r"branch: Created from (\S+)$", log.splitlines()[-1])
    source = match.group(1) if match else None
    if source and source != "HEAD" and source != branch and _ref_exists(source, cwd):
        # A branch name, not a commit id.
        is_branch = _try(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{source}", cwd=cwd
        ) or _try("rev-parse", "--verify", "--quiet", f"refs/remotes/{source}", cwd=cwd)
        return source if is_branch else None
    return None


_DEFAULT_BRANCHES = ("main", "master", "develop", "trunk")


def _nearest_ancestor(
    branch: str | None, head: str, cwd: Path | None = None
) -> tuple[str, str] | None:
    """The other branch HEAD is closest to: fewest own commits since their merge base."""
    listing = _try(
        "for-each-ref", "--format=%(refname)%09%(symref)", "refs/heads", "refs/remotes", cwd=cwd
    )
    if not listing:
        return None
    up = upstream(cwd)
    scored: list[tuple[tuple[int, int, int], str, int]] = []
    for line in listing.splitlines():
        refname, _, symref = line.partition("\t")
        if symref:
            continue  # origin/HEAD and friends
        short = refname.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        if branch and refname == f"refs/heads/{branch}":
            continue
        if branch and refname.startswith("refs/remotes/") and refname.split("/", 3)[3:] == [branch]:
            continue  # the remote copy of this very branch
        if short == up:
            continue
        base = _try("merge-base", head, short, cwd=cwd)
        if base is None:
            continue
        ahead = int(_git("rev-list", "--count", f"{base}..{head}", cwd=cwd))
        behind = int(_git("rev-list", "--count", f"{base}..{short}", cwd=cwd))
        is_default = short.split("/")[-1] in _DEFAULT_BRANCHES
        scored.append(((ahead, behind, 0 if is_default else 1), short, ahead))
    if not scored:
        return None
    _, best, ahead = min(scored)
    return best, f"nearest ancestor branch, {ahead} commit(s) ahead of it"


def origin_branch(head: str = "HEAD", cwd: Path | None = None) -> tuple[str, str]:
    """(ref, how) for the branch the current branch grew out of.

    In order: a merge base recorded by gh or VS Code, the branch named when it was created,
    the nearest other branch, and finally the upstream.
    """
    branch = current_branch(cwd)
    if branch:
        for key, who in (("gh-merge-base", "gh"), ("vscode-merge-base", "VS Code")):
            ref = _try("config", "--get", f"branch.{branch}.{key}", cwd=cwd)
            if ref and _ref_exists(ref, cwd):
                return ref, f"recorded by {who}"
        created = _created_from(branch, cwd)
        if created:
            return created, "the branch it was created from (reflog)"
    nearest = _nearest_ancestor(branch, head, cwd)
    if nearest:
        return nearest
    up = upstream(cwd)
    if up:
        return up, "its upstream, there is no other branch to compare with"
    raise GitError(
        "cannot tell which branch this one came from: no other branch, no upstream. "
        "Pass --against <ref>"
    )


@dataclass(frozen=True)
class LocalDiff:
    base_ref: str  # the ref compared against, e.g. origin/main
    how: str  # why that ref
    base_sha: str  # the merge base the diff starts from
    committed: tuple[str, ...]  # changed in commits since the base
    uncommitted: tuple[str, ...]  # staged, unstaged and untracked
    subjects: tuple[str, ...]  # commit subjects since the base

    @property
    def files(self) -> list[str]:
        return sorted({*self.committed, *self.uncommitted})

    def describe(self, head_label: str) -> str:
        return (
            f"{head_label} against {self.base_ref} ({self.how}): "
            f"{len(self.subjects)} commit(s), {len(self.committed)} committed and "
            f"{len(self.uncommitted)} uncommitted file(s)"
        )


def local_diff(
    against: str = "origin-branch",
    head: str = "HEAD",
    *,
    include_working: bool = True,
    cwd: Path | None = None,
) -> LocalDiff:
    """Everything that differs locally.

    against: "origin-branch" (default) compares with the branch this one grew out of,
    "upstream" with the remote-tracking branch (what is not pushed yet), anything else is
    taken as a ref. Uncommitted work is always added unless include_working is False.
    """
    if against == "origin-branch":
        ref, how = origin_branch(head, cwd)
    elif against == "upstream":
        ref = upstream(cwd)
        if ref is None:
            raise GitError(
                "this branch has no upstream. Push it with `git push -u`, or use --against <ref>"
            )
        how = "the remote-tracking branch"
    else:
        if not _ref_exists(against, cwd):
            raise GitError(f"--against {against!r} is not a ref")
        ref, how = against, "given with --against"
    base = merge_base(ref, head, cwd)
    uncommitted = tuple(working_files(cwd)) if include_working else ()
    return LocalDiff(
        base_ref=ref,
        how=how,
        base_sha=base,
        committed=tuple(changed_files(base, head, cwd)),
        uncommitted=uncommitted,
        subjects=tuple(subjects(base, head, cwd)),
    )
