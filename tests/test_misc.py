"""/start, /about and /version.

/version reports two independent things — the release version (semver, chosen by a human)
and the commit/branch of the exact build. The split matters when diagnosing a deploy: the
version says what was intended, the commit says what is actually running.
"""

from conftest import FakeChat, FakeContext, FakeUpdate, FakeUser, message

import version
from handlers import misc


async def test_version_reports_the_release_version_and_short_commit(monkeypatch):
    monkeypatch.setattr(
        version,
        "get_version_info",
        lambda: {
            "version": "1.2.3",
            "branch": "main",
            "commit": "bafdebd18bccb1ce61a78a152aa0333d655f7227",
            "short_commit": "bafdebd",
            "commit_url": "https://github.com/o/r/commit/bafdebd18bccb1ce61a78a152aa0333d655f7227",
            "source": "railway",
        },
    )
    msg = message("/version")
    await misc.display_version(FakeUpdate(message=msg), FakeContext())

    reply = msg.last_reply
    assert "v1.2.3" in reply
    assert "bafdebd" in reply
    assert "Branch: <code>main</code>" in reply
    # The full sha appears only inside the href, never as visible text.
    assert reply.count("bafdebd18bccb1ce61a78a152aa0333d655f7227") == 1


async def test_version_falls_back_to_plain_without_a_commit_url(monkeypatch):
    monkeypatch.setattr(
        version,
        "get_version_info",
        lambda: {
            "version": "1.2.3",
            "branch": version.UNKNOWN,
            "commit": version.UNKNOWN,
            "short_commit": version.UNKNOWN,
            "commit_url": "",
            "source": "unknown",
        },
    )
    msg = message("/version")
    await misc.display_version(FakeUpdate(message=msg), FakeContext())
    assert "<a href" not in msg.last_reply
    assert "v1.2.3" in msg.last_reply


async def test_version_uses_the_real_module_constant():
    """Guards the wiring, not the number: /version must report VERSION, not a literal."""
    msg = message("/version")
    await misc.display_version(FakeUpdate(message=msg), FakeContext())
    assert "v" + version.VERSION in msg.last_reply


async def test_start_replies_only_in_a_private_chat():
    private = message("/start", chat=FakeChat("private", 1))
    await misc.startme(FakeUpdate(message=private), FakeContext())
    assert "Thank you for starting me" in private.last_reply

    group = message("/start", chat=FakeChat("group", -100))
    await misc.startme(FakeUpdate(message=group), FakeContext())
    assert group.replies == [], "/start must stay silent in groups"


async def test_about_links_the_repo():
    msg = message("/about", from_user=FakeUser(7, "Alice"))
    await misc.display_about(FakeUpdate(message=msg), FakeContext())
    assert version.GITHUB_REPO in msg.last_reply
    assert "/version" in msg.last_reply
