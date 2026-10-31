"""The base of a change in a shallow or partial clone, and reading a file at a revision."""

import subprocess

import pytest

from picard import gitutil

pytestmark = pytest.mark.integration

MISSING_SHA = "0123456789abcdef0123456789abcdef01234567"


def git(repo, *args):
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@e.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(repo, path, message):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{message}\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def origin(tmp_path):
    """A repository with main at A-B-C; returns (path, [sha A, sha B, sha C])."""
    repo = tmp_path / "origin"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    shas = [commit(repo, f"{name}.txt", f"chore: {name}") for name in ("a", "b", "c")]
    return repo, shas


@pytest.fixture
def shallow(origin, tmp_path):
    """A depth-1 clone of origin: only C is present."""
    repo, _ = origin
    clone = tmp_path / "shallow"
    git(tmp_path, "clone", "--quiet", "--depth", "1", repo.as_uri(), str(clone))
    return clone


@pytest.fixture
def fetches(monkeypatch):
    """Every `git fetch` argument list, recorded while the real git still runs."""
    calls = []
    real_run = subprocess.run

    def run(args, *rest, **kwargs):
        if "fetch" in args:
            calls.append((list(args), kwargs))
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(gitutil.subprocess, "run", run)
    return calls


def test_is_shallow(origin, shallow):
    assert gitutil.is_shallow(shallow) is True
    assert gitutil.is_shallow(origin[0]) is False


def test_present_base_resolves_without_fetching(origin, fetches):
    repo, (a, _, c) = origin
    assert gitutil.resolve_base(a, cwd=repo) == a
    assert gitutil.resolve_base("main", c, cwd=repo) == c
    assert fetches == []


def test_shallow_clone_fetches_the_missing_base_once(origin, shallow, fetches):
    _, (a, _, _) = origin
    missing = subprocess.run(["git", "cat-file", "-e", a], cwd=shallow, check=False)
    assert missing.returncode != 0
    assert gitutil.resolve_base(a, cwd=shallow) == a
    assert len(fetches) == 1
    args, kwargs = fetches[0]
    assert args == ["git", "fetch", "--no-tags", "--quiet", "--unshallow", "origin", a]
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["timeout"] == gitutil.FETCH_TIMEOUT == 300
    assert gitutil.is_shallow(shallow) is False


def test_base_present_but_history_cut_off_fetches(origin, tmp_path, fetches):
    """The base commit is here, but the shallow boundary hides the merge base."""
    repo, (a, b, c) = origin
    git(repo, "branch", "old", b)
    clone = tmp_path / "partial"
    git(tmp_path, "clone", "--quiet", "--depth", "1", repo.as_uri(), str(clone))
    git(clone, "fetch", "--quiet", "--depth", "1", "origin", "old")
    fetches.clear()
    assert gitutil.resolve_base(b, cwd=clone) == b
    assert len(fetches) == 1


def test_base_that_exists_nowhere_raises_after_one_fetch(shallow, fetches):
    with pytest.raises(gitutil.UnknownBaseError) as info:
        gitutil.resolve_base(MISSING_SHA, cwd=shallow)
    assert str(info.value) == (
        f"base {MISSING_SHA} is not in this shallow clone and could not be fetched"
    )
    assert isinstance(info.value, gitutil.GitError)  # uncaught, it still exits 2
    assert len(fetches) == 1


def test_full_clone_message_and_no_unshallow(origin, tmp_path, fetches):
    repo, _ = origin
    clone = tmp_path / "full"
    git(tmp_path, "clone", "--quiet", repo.as_uri(), str(clone))
    with pytest.raises(gitutil.UnknownBaseError, match=r"is not in this clone and"):
        gitutil.resolve_base(MISSING_SHA, cwd=clone)
    assert [args for args, _ in fetches] == [
        ["git", "fetch", "--no-tags", "--quiet", "origin", MISSING_SHA]
    ]


def test_no_origin_raises_without_fetching(origin, fetches):
    repo, _ = origin
    with pytest.raises(gitutil.UnknownBaseError, match=r"is not in this clone"):
        gitutil.resolve_base(MISSING_SHA, cwd=repo)
    assert fetches == []


@pytest.mark.parametrize("head", ["no-such-branch", MISSING_SHA, "--output=x"])
def test_bad_head_is_a_plain_error_without_fetching(origin, shallow, fetches, head):
    """A typo in --head stops the run; it is never an unknown base that runs everything."""
    _, (a, _, _) = origin
    with pytest.raises(gitutil.GitError, match="is not a commit") as info:
        gitutil.resolve_base(a, head, cwd=shallow)
    assert not isinstance(info.value, gitutil.UnknownBaseError)
    assert fetches == []


def test_option_like_base_is_never_fetched(shallow, fetches):
    with pytest.raises(gitutil.UnknownBaseError):
        gitutil.resolve_base("--upload-pack=touch pwned", cwd=shallow)
    assert fetches == []
    assert not (shallow / "pwned").exists()


def test_fetch_timeout_still_raises_unknown_base(origin, shallow, monkeypatch):
    """A hanging fetch widens like a failed one: UnknownBaseError, never another error."""
    _, (a, _, _) = origin
    real_run = subprocess.run

    def run(args, *rest, **kwargs):
        if "fetch" in args:
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(gitutil.subprocess, "run", run)
    with pytest.raises(gitutil.UnknownBaseError, match="shallow clone") as info:
        gitutil.resolve_base(a, cwd=shallow)
    assert "timed out" in str(info.value.__cause__)


def test_show_file_present_and_absent(origin):
    repo, (a, b, c) = origin
    (repo / "dir").mkdir()
    commit(repo, "dir/lock.txt", "chore: lock é")
    assert gitutil.show_file("HEAD", "dir/lock.txt", cwd=repo) == "chore: lock é\n"
    assert gitutil.show_file(b, "b.txt", cwd=repo) == "chore: b\n"
    assert gitutil.show_file(a, "b.txt", cwd=repo) is None
    assert gitutil.show_file("HEAD", "dir", cwd=repo) is None  # a tree, not a file
    assert gitutil.show_file(c, "dir/lock.txt", cwd=repo) is None


def test_show_file_paths_are_from_the_repository_root(origin):
    repo, _ = origin
    commit(repo, "dir/lock.txt", "chore: lock")
    assert gitutil.show_file("HEAD", "dir/lock.txt", cwd=repo / "dir") == "chore: lock\n"


def test_show_file_that_is_not_text_raises(origin):
    repo, _ = origin
    (repo / "blob.bin").write_bytes(b"\xff\xfe\x00")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "chore: binary")
    with pytest.raises(gitutil.GitError, match="is not UTF-8 text"):
        gitutil.show_file("HEAD", "blob.bin", cwd=repo)


def test_show_file_bad_revision_raises(origin):
    repo, _ = origin
    with pytest.raises(gitutil.GitError, match="is not a commit"):
        gitutil.show_file(MISSING_SHA, "a.txt", cwd=repo)
    with pytest.raises(gitutil.GitError, match="is not a commit"):
        gitutil.show_file("--output=x", "a.txt", cwd=repo)
