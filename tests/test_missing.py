"""`/miss` — the achievements a player can still earn.

Three things are worth pinning, and none of them is the rendering (that is golden-tested
in test_render_golden.py):

* **what counts as still obtainable**, which `wwstats.missing` decides for both this and
  the "MISSING AND ATTAINABLE VIA PLAYING" section of /achievements — the two must never
  disagree about a player's list, because a table reading one and a player reading the
  other would be looking at different answers to the same question;
* **who it is about**: whoever asked, or whoever they replied to;
* that it answers **in the chat**, unlike /achievements, which has to PM four Markdown
  messages and so cannot answer the question a group actually asks.
"""

from conftest import ACHIEVEMENTS, FakeContext, FakeUpdate, FakeUser, message

from wwstatsbot.handlers import achievements as achv_handlers
from wwstatsbot.render import wwstats


async def run(msg):
    await achv_handlers.display_missing(FakeUpdate(message=msg), FakeContext())
    return msg.last_reply


# --- What "still obtainable" means ------------------------------------------------


def test_missing_leaves_out_what_is_already_held(achievements):
    held = [{"name": "Welcome to Hell"}]
    assert "Welcome to Hell" not in [a["name"] for a in wwstats.missing(held)]


def test_missing_leaves_out_inactive_and_not_via_playing(achievements):
    """Neither can be gone after: one can no longer be earned at all, the other is not
    won at the table."""
    names = [a["name"] for a in wwstats.missing([])]
    assert "Explorer" not in names
    assert "Here's Johnny!" not in names


def test_missing_is_everything_else_for_a_player_with_nothing(achievements):
    assert [a["name"] for a in wwstats.missing([])] == [
        "Welcome to Hell",
        "O HAI DER!",
        "Liquid Business",
        "Busy Night",
    ]


def test_missing_and_the_achievements_report_agree(achievements):
    """The report prints this same bucket, and its count line comes from the same list —
    so a change to one that misses the other is visible here rather than in production."""
    held = [{"name": "Welcome to Hell"}]
    report = wwstats.check(held)
    assert "MISSING AND ATTAINABLE VIA PLAYING ({}/{})".format(
        len(wwstats.missing(held)), len(ACHIEVEMENTS)
    ) in "".join(report)


# --- Who it is about ---------------------------------------------------------------


async def test_no_reply_is_about_the_sender(achievements, stats_api):
    assert "<a href='tg://user?id=7'>Alice</a>" in await run(message("/miss", from_user=FakeUser(7, "Alice")))


async def test_a_reply_is_about_the_replied_to_player(achievements, stats_api):
    replied = message("hi", from_user=FakeUser(99, "Bob"))
    reply = await run(message("/miss", from_user=FakeUser(7, "Alice"), reply_to_message=replied))
    assert "<a href='tg://user?id=99'>Bob</a>" in reply


async def test_the_reply_target_is_the_id_actually_queried(achievements, stats_api):
    replied = message("hi", from_user=FakeUser(99, "Bob"))
    await run(message("/miss", from_user=FakeUser(7, "Alice"), reply_to_message=replied))
    assert {request.url.params.get("pid") for request in stats_api.requests} == {"99"}


async def test_a_name_is_escaped_exactly_once(achievements, stats_api):
    assert "Al &amp; Sons" in await run(message("/miss", from_user=FakeUser(7, "Al & Sons")))


# --- How it answers ----------------------------------------------------------------


async def test_it_answers_in_the_chat_rather_than_by_pm(achievements, stats_api):
    """The whole reason this exists beside /achievements: a group gets the answer, not a
    note saying the answer was sent somewhere else."""
    msg = message("/miss", from_user=FakeUser(7, "Alice"))
    await run(msg)
    assert len(msg.replies) == 1
    assert "PM" not in msg.last_reply


async def test_the_reply_is_html_without_a_link_preview(achievements, stats_api):
    msg = message("/miss", from_user=FakeUser(7, "Alice"))
    await run(msg)
    _text, kwargs = msg.replies[-1]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["disable_web_page_preview"] is True


async def test_no_descriptions_are_printed(achievements, stats_api):
    """Names only — the descriptions are what make /achievements a PM."""
    reply = await run(message("/miss", from_user=FakeUser(7, "Alice")))
    assert "Drink the potion" not in reply
