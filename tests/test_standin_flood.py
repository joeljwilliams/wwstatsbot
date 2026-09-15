"""Flood control, and the edits this session was making for no reason.

A busy game is the one place this bot goes anywhere near Telegram's per-chat rate limit.
Every write schedules a publish, a publish edits *both* live messages, and the debounce
puts a floor of five seconds under that — so a table revealing in a burst costs up to
twenty-four edits a minute on top of a reply to every command, against a documented soft
limit of about twenty messages a minute to one group.

Two properties carry this file.

**An edit that would change nothing is not sent.** Both messages are re-rendered and
re-sent on every publish whether or not either changed. Telegram answers "message is not
modified", the handler is careful to ignore it, and the call is spent anyway. The rule is
the one `RedisPersistence._save` already follows one layer down — and so is its important
half, that a fingerprint advances only on an edit that *landed*.

**Flood control postpones the publish rather than losing it.** A RetryAfter used to leave
the loop entirely: the live messages stayed at their last successful edit until somebody
happened to reveal a role, and the exception went to the error handler as if the bot had
crashed. Now the next publish is moved past the window Telegram named, and both messages
catch up together.
"""

from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs
from telegram.error import BadRequest, RetryAfter
from test_standin_list import big_game, publish
from test_standin_session import reveal, role_notices, start_session

import db
from handlers import gamesession
from rulelist import RULES

PUBLISH_JOB = gamesession._PUBLISH_JOB.format(-100)
ROLE_JOB = gamesession._ROLE_BURST_JOB.format(-100)


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    """The real catalogue, without a database. db.get_rules() is the live read path."""
    monkeypatch.setattr(db, "get_rules", lambda: {rule["name"]: rule for rule in RULES})


async def settled(context):
    """A session with both live messages posted and up to date."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await publish(context)
    context.bot.edits.clear()
    context.bot.sent.clear()
    return session_data


# --- Not saying the same thing twice ----------------------------------------


async def test_a_publish_that_changes_nothing_costs_no_edits(context):
    """The cheapest request is the one not made. This one was made twice a game at least:
    every /love between players already in love, every re-sent roster that moved nothing,
    every second /dead for somebody already dead."""
    await settled(context)

    await gamesession._publish(_job(context))

    assert context.bot.edits == []


async def test_a_real_change_is_still_published(context):
    """The skip must not be able to swallow an actual update."""
    await settled(context)

    await reveal(context, 2, "harlot")
    await publish(context)

    assert len(context.bot.edits) == 2, "the roster and the list"


async def test_the_keyboard_counts_as_part_of_the_message(context):
    """The list's button appears and disappears with the trimming, so a rendering whose
    text is unchanged but whose button has gone still has to be edited."""
    text = "Possible Achievements:\n"
    _, keyboard = gamesession.render_list(await big_game(context))

    assert gamesession._fingerprint(text, keyboard) != gamesession._fingerprint(text, None)


async def test_an_edit_that_failed_is_tried_again(context):
    """The fingerprint advances only on an edit that landed, so a blip is retried rather
    than remembered as done — which would freeze the message for the life of the game."""
    await settled(context)
    context.bot._edit_error = BadRequest("Message_too_long")

    await reveal(context, 2, "harlot")
    await publish(context)
    assert context.bot.edits == [], "nothing landed"

    context.bot._edit_error = None
    await gamesession._publish(_job(context))

    assert len(context.bot.edits) == 2, "and the next publish tries again"


async def test_not_modified_is_remembered_rather_than_retried(context):
    """Telegram confirming the message already looks like this is worth recording: it is
    the one failure that means the state we wanted is the state that exists."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await publish(context)
    context.bot._edit_error = BadRequest("Message is not modified")

    await reveal(context, 2, "harlot")
    await publish(context)
    assert session_data["list_fingerprint"] is not None

    context.bot.edits.clear()
    context.bot._edit_error = None
    await gamesession._publish(_job(context))

    assert context.bot.edits == [], "asked once, told, and not asked again"


# --- Flood control ----------------------------------------------------------


def test_the_wait_is_read_whichever_type_it_arrives_as():
    """PTB warns that RetryAfter.retry_after becomes a timedelta in a future version.

    Pinned here rather than left to the upgrade, because the first thing that would run
    the new type is flood handling — which by definition only runs when something is
    already going wrong.
    """
    from datetime import timedelta

    assert gamesession._retry_seconds(RetryAfter(30)) == 30
    assert gamesession._retry_seconds(SimpleNamespace(retry_after=timedelta(seconds=30))) == 30


async def test_flood_control_postpones_the_publish(context):
    await settled(context)
    context.bot._edit_error = RetryAfter(30)

    await reveal(context, 2, "harlot")
    with capture_logs() as entries:
        await publish(context)

    failures = [e for e in entries if e["event"] == "standin_roster_edit_failed"]
    assert len(failures) == 1
    assert failures[0]["retry_after"] == 30

    pending = context.job_queue.pending(PUBLISH_JOB)
    assert len(pending) == 1
    assert pending[0].when == 31, "a second past the window, because the clocks differ"


async def test_a_flooded_publish_does_not_try_the_second_message(context):
    """It would meet the same window, be refused the same way, and cost another call."""
    await settled(context)
    context.bot._edit_error = RetryAfter(30)

    await reveal(context, 2, "harlot")
    with capture_logs() as entries:
        await publish(context)

    assert [e["event"] for e in entries if "failed" in e["event"]] == ["standin_roster_edit_failed"]


async def test_the_postponed_publish_replaces_whatever_was_pending(context):
    """One already five seconds out would land inside the same window."""
    await settled(context)
    context.bot._edit_error = RetryAfter(30)

    await reveal(context, 2, "harlot")
    await publish(context)
    # A reveal arriving during the window asks for a publish of its own.
    await reveal(context, 3, "seer")

    assert len(context.job_queue.pending(PUBLISH_JOB)) == 1


async def test_the_postponed_publish_catches_both_messages_up(context):
    await settled(context)
    context.bot._edit_error = RetryAfter(30)

    await reveal(context, 2, "harlot")
    await publish(context)
    assert context.bot.edits == []

    context.bot._edit_error = None
    await context.job_queue.run_pending(context, elapsed=31)

    assert len(context.bot.edits) == 2


async def test_flood_control_on_the_first_post_is_postponed_too(context):
    """And the message id stays unrecorded, so the retry posts rather than edits nothing."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    context.bot._send_error = RetryAfter(12)

    with capture_logs() as entries:
        await publish(context)

    assert session_data.get("list_message_id") is None
    failures = [e for e in entries if e["event"] == "standin_list_post_failed"]
    assert failures and failures[0]["retry_after"] == 12
    assert context.job_queue.pending(PUBLISH_JOB)[0].when == 13


async def test_flood_control_puts_the_collected_role_notice_back(context):
    """A confirmation swallowed here would leave players believing their /role never landed,
    and retyping it is exactly the noise the collected notice exists to remove."""
    session_data = await settled(context)
    await reveal(context, 2, "harlot")
    await reveal(context, 3, "seer")
    context.bot._send_error = RetryAfter(20)

    with capture_logs() as entries:
        await gamesession._role_notice(_job(context))

    failures = [e for e in entries if e["event"] == "standin_role_notice_failed"]
    assert failures and failures[0]["retry_after"] == 20
    assert session_data["role_pending"] == ["2", "3"], "nobody is dropped by the refusal"

    pending = context.job_queue.pending(ROLE_JOB)
    assert len(pending) == 1, "the one already pending is replaced, not added to"
    assert pending[0].when == 21

    context.bot._send_error = None
    await context.job_queue.run_pending(context, elapsed=21)
    notice = role_notices(context)[0]
    assert "Harlot" in notice and "Seer" in notice


def _job(context):
    """A context standing in for the debounce job firing against this chat."""
    context.job = type("Job", (), {"chat_id": -100})()
    return context
