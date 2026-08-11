"""Build identification for /version.

version.py resolves the running commit once at import, trying Railway env vars, then
generic GIT_* env, then the git CLI, then giving up. The resolution *order* is the
contract — production reads it from Railway, local dev from git — so the tests drive
the private resolvers directly rather than re-importing the module, which would make
the assertions depend on the checkout's own git state.
"""

import version


def test_short_commit_truncates_to_seven():
    assert version._short("0123456789abcdef") == "0123456"


def test_short_commit_passes_unknown_through():
    assert version._short(version.UNKNOWN) == version.UNKNOWN
    assert version._short("") == version.UNKNOWN
    assert version._short(None) == version.UNKNOWN


def test_commit_url_builds_a_github_link():
    assert version._commit_url("https://github.com/o/r", "abc123") == ("https://github.com/o/r/commit/abc123")


def test_commit_url_strips_a_trailing_slash():
    assert version._commit_url("https://github.com/o/r/", "abc") == ("https://github.com/o/r/commit/abc")


def test_commit_url_is_empty_without_a_usable_commit_or_base():
    assert version._commit_url("https://github.com/o/r", version.UNKNOWN) == ""
    assert version._commit_url("https://github.com/o/r", "") == ""
    assert version._commit_url("", "abc") == ""


def test_info_shape_is_complete():
    """/version formats a template with **info, so every key must be present."""
    info = version._info("abcdef1234", "devel", "http://x", "test")
    assert info == {
        "commit": "abcdef1234",
        "branch": "devel",
        "short_commit": "abcdef1",
        "commit_url": "http://x",
        "source": "test",
    }


def test_info_defaults_missing_values_to_unknown():
    info = version._info(None, None, "", "unknown")
    assert info["commit"] == version.UNKNOWN
    assert info["branch"] == version.UNKNOWN
    assert info["short_commit"] == version.UNKNOWN


def test_railway_resolution(monkeypatch):
    """The production path: Railway injects git metadata on GitHub-triggered deploys."""
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "deadbeefcafe")
    monkeypatch.setenv("RAILWAY_GIT_BRANCH", "main")
    monkeypatch.setenv("RAILWAY_GIT_REPO_OWNER", "joeljwilliams")
    monkeypatch.setenv("RAILWAY_GIT_REPO_NAME", "wwstatsbot")
    info = version._from_railway()
    assert info["source"] == "railway"
    assert info["branch"] == "main"
    assert info["short_commit"] == "deadbee"
    assert info["commit_url"] == ("https://github.com/joeljwilliams/wwstatsbot/commit/deadbeefcafe")


def test_railway_falls_back_to_the_default_repo_when_owner_is_absent(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc")
    monkeypatch.delenv("RAILWAY_GIT_REPO_OWNER", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_REPO_NAME", raising=False)
    assert version._from_railway()["commit_url"].startswith(version.GITHUB_REPO)


def test_railway_returns_none_without_a_commit(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    assert version._from_railway() is None


def test_generic_env_resolution(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "0123456789")
    monkeypatch.setenv("GIT_BRANCH", "devel")
    info = version._from_env()
    assert info["source"] == "env"
    assert info["short_commit"] == "0123456"
    assert info["branch"] == "devel"


def test_generic_env_returns_none_without_a_commit(monkeypatch):
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    assert version._from_env() is None


def test_git_helper_swallows_failures(monkeypatch):
    """_git must never raise or hang — it runs at import in a container without git."""
    assert version._git("definitely-not-a-git-subcommand") is None


def test_resolution_order_prefers_railway_over_env(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "railwaysha")
    monkeypatch.setenv("GIT_COMMIT", "envsha")
    assert version._resolve()["source"] == "railway"


def test_resolution_order_prefers_env_over_git(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setenv("GIT_COMMIT", "envsha")
    assert version._resolve()["source"] == "env"


def test_resolution_falls_all_the_way_through_to_unknown(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.setattr(version, "_from_git", lambda: None)
    info = version._resolve()
    assert info == {
        "commit": version.UNKNOWN,
        "branch": version.UNKNOWN,
        "short_commit": version.UNKNOWN,
        "commit_url": "",
        "source": "unknown",
    }


def test_cached_info_is_returned(monkeypatch):
    assert version.get_version_info() is version._VERSION_INFO
