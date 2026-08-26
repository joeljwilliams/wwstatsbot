"""Release version and build identification for the /version command.

Two independent things are reported. **VERSION** is the semantic version of the
release — chosen by a human, bumped in a commit. The **commit and branch** identify the
exact build running it, so a deploy can be tied to a precise revision.

VERSION is a plain constant here, and this module is the single source of truth for it.
The two obvious alternatives do not work for this app:

  * `importlib.metadata.version("wwstatsbot")` — there is no dist-info. The bot is a flat
    set of modules with no build backend, installed with `uv sync --no-install-project`.
  * parsing `pyproject.toml` at runtime — it is copied into the *builder* stage only, so
    it does not exist in the running image.

`pyproject.toml` still carries a `version` for uv's benefit, and
tests/test_version.py asserts the two agree, so the mirror cannot drift silently.

Bumping a release: edit VERSION here and `version` in pyproject.toml in the same commit,
following semver — major for a breaking change to commands or stored data, minor for a
new command or capability, patch for fixes.

Commit/branch resolution order (first hit wins), computed ONCE at import so the git
subprocess (if any) runs at process startup, never inside the async event loop:

  1. Railway  — the production path. Railway auto-injects git metadata as runtime
                env vars on GitHub-triggered deploys (RAILWAY_GIT_COMMIT_SHA, ...).
  2. Generic  — GIT_COMMIT / GIT_BRANCH env, a portable escape hatch for a manual
                `docker build --build-arg` or `docker run -e`.
  3. git      — local dev: shell out to git (guarded so it can never hang/crash in
                a container that has neither the binary nor a .git dir).
  4. unknown  — nothing resolved; every field is "unknown" and there's no link.

get_version_info() returns the cached dict; callers must not mutate it.
"""

import os
import shutil
import subprocess

# The release version, semver. Keep in step with `version` in pyproject.toml — a test
# enforces it.
#
# The numbers carry the project's history rather than starting from scratch:
#   major 2  — 1.x was the original bot (Carson True, later @jeffffc); 2.x is the async
#              rewrite this fork carries.
#   minor 22 — feature releases since that rewrite. (Coincidentally the same number as the
#              python-telegram-bot major it targets. The two are unrelated.)
#
# Not derived from git tags: the container has no .git and Railway does not inject tag
# metadata, so a tag-derived version would read "unknown" exactly where it matters most.
VERSION = "2.26.2"

UNKNOWN = "unknown"

# Base repo URL for building commit links on the generic/git paths (the Railway
# path derives its own from RAILWAY_GIT_REPO_OWNER/NAME). Env-overridable.
GITHUB_REPO = os.environ.get("GITHUB_REPO", "https://github.com/joeljwilliams/wwstatsbot")


def _commit_url(base, commit):
    """A GitHub commit URL, or "" when the commit is unknown or there's no base."""
    if not base or not commit or commit == UNKNOWN:
        return ""
    return "{}/commit/{}".format(base.rstrip("/"), commit)


def _short(commit):
    return commit[:7] if commit and commit != UNKNOWN else UNKNOWN


def _info(commit, branch, commit_url, source):
    # `commit` (the full sha) is retained even though /version no longer displays it: it
    # builds the commit URL, and structured log lines carry it.
    return {
        "version": VERSION,
        "commit": commit or UNKNOWN,
        "branch": branch or UNKNOWN,
        "short_commit": _short(commit),
        "commit_url": commit_url,
        "source": source,
    }


def _from_railway():
    """Railway-provided runtime vars (GitHub-triggered deploys only). None if absent."""
    commit = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    if not commit:
        return None
    branch = os.environ.get("RAILWAY_GIT_BRANCH")
    owner = os.environ.get("RAILWAY_GIT_REPO_OWNER")
    repo = os.environ.get("RAILWAY_GIT_REPO_NAME")
    base = "https://github.com/{}/{}".format(owner, repo) if owner and repo else GITHUB_REPO
    return _info(commit, branch, _commit_url(base, commit), "railway")


def _from_env():
    """Generic GIT_COMMIT/GIT_BRANCH escape hatch. None if unset."""
    commit = os.environ.get("GIT_COMMIT")
    if not commit:
        return None
    branch = os.environ.get("GIT_BRANCH")
    return _info(commit, branch, _commit_url(GITHUB_REPO, commit), "env")


def _git(*args):
    """Run a git command, returning stripped stdout or None on any failure.

    Guarded to never hang or raise: short timeout, and every git/OS error swallowed.
    The caller only reaches this when git is known to be present.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _from_git():
    """Local-dev resolution via the git CLI. None if git/repo is unavailable."""
    if shutil.which("git") is None:
        return None
    if _git("rev-parse", "--is-inside-work-tree") != "true":
        return None
    commit = _git("rev-parse", "HEAD")
    if not commit:
        return None
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":  # detached HEAD returns the literal "HEAD"
        branch = "(detached)"
    info = _info(commit, branch, _commit_url(GITHUB_REPO, commit), "git")
    # A dirty working tree is meaningful only in local dev; mark the short SHA.
    if _git("status", "--porcelain"):
        info["short_commit"] = info["short_commit"] + "-dirty"
    return info


def _resolve():
    return _from_railway() or _from_env() or _from_git() or _info(UNKNOWN, UNKNOWN, "", "unknown")


_VERSION_INFO = _resolve()


def get_version_info():
    """Return the cached version info dict (resolved once at import)."""
    return _VERSION_INFO
