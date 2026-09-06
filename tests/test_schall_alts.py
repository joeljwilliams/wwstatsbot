"""Alt accounts are left out of a /schall check.

An alt is a second account of somebody already in the room. Its stats are not that
player's — a fresh account holds almost nothing — so leaving it in means a name that sits
in "not obtained" for every achievement and reads as a real gap in the roster. The
possible-achievements list already drops them for the same reason; this is the other place
a roster is checked player by player.

The filter runs at check time, not when the reply's mentions are remembered, so unmarking
an alt brings them straight back to a cached roster instead of needing a fresh reply. Who
was dropped is named in the reply, because "Checked 5 players" after six were mentioned
otherwise reads as a mention the bot failed to see.
"""

from conftest import FakeContext, FakeEntity, FakeUpdate, FakeUser, bot_message, message

import db
from handlers import search


def player_list(*users):
    """A bot message mentioning players, as the game bot posts one."""
    entities, offset = [], 0
    for uid, name in users:
        entities.append(FakeEntity("text_mention", offset, len(name), FakeUser(uid, name)))
        offset += len(name) + 1
    return bot_message(" ".join(name for _, name in users), entities=entities)


async def run(users=((1, "Alice"), (2, "Bob")), args=("busy",), **ctx):
    msg = message("/sch busy", reply_to_message=player_list(*users))
    context = FakeContext(args=list(args), **ctx)
    await search.display_search_all(FakeUpdate(message=msg), context)
    return msg.last_reply, context


async def test_an_alt_is_not_checked(achievements, no_fts, stats_api):
    stats_api.set_achievements(1, [])
    stats_api.set_achievements(2, [])
    db._ALTS = {2}
    reply, _ = await run()

    assert "Checked 1 player for it" in reply
    assert "Alice" in reply
    assert "Not obtained (1)" in reply


async def test_the_reply_names_who_was_ignored(achievements, no_fts, stats_api):
    stats_api.set_achievements(1, [])
    db._ALTS = {2}
    reply, _ = await run()

    assert "<i>Bob ignored as alts</i>" in reply


async def test_several_alts_are_listed_together(achievements, no_fts, stats_api):
    stats_api.set_achievements(1, [])
    db._ALTS = {2, 3}
    reply, _ = await run(users=((1, "Alice"), (2, "Bob"), (3, "Cara")))

    assert "<i>Bob, Cara ignored as alts</i>" in reply


async def test_an_alt_name_is_escaped_once(achievements, no_fts, stats_api):
    """Names are stored unescaped and escaped at render time, so a persistence round-trip
    followed by a toggle re-render cannot escape them twice."""
    from conftest import FakeCallbackQuery, assert_json_roundtrips

    stats_api.set_achievements(1, [])
    db._ALTS = {2}
    reply, ctx = await run(users=((1, "Alice"), (2, "Bo<b>")))
    assert "Bo&lt;b&gt; ignored as alts" in reply

    restored = assert_json_roundtrips(ctx.bot_data)
    token = next(iter(restored["schall"]))
    query = FakeCallbackQuery(data="schall:{}:have".format(token))
    await search.schall_callback(FakeUpdate(callback_query=query), FakeContext(bot_data=restored))

    text, _ = query.edits[-1]
    assert "Bo&lt;b&gt; ignored as alts" in text


async def test_the_footer_survives_the_toggle(achievements, no_fts, stats_api):
    """Both views are the same result, so the note belongs on both — flipping the list
    must not quietly turn six mentioned players back into five checked ones."""
    from conftest import FakeCallbackQuery

    stats_api.set_achievements(1, [])
    db._ALTS = {2}
    _, ctx = await run()

    token = next(iter(ctx.bot_data["schall"]))
    query = FakeCallbackQuery(data="schall:{}:have".format(token))
    await search.schall_callback(FakeUpdate(callback_query=query), FakeContext(bot_data=ctx.bot_data))

    text, _ = query.edits[-1]
    assert "<i>Bob ignored as alts</i>" in text


async def test_a_run_with_no_alts_carries_no_note(achievements, no_fts, stats_api):
    stats_api.set_achievements(1, [])
    stats_api.set_achievements(2, [])
    reply, _ = await run()

    assert "ignored as alts" not in reply


async def test_an_alt_is_not_in_the_other_view_either(achievements, no_fts, stats_api):
    """Dropped from the run, not merely from the list on screen — otherwise the toggle
    would reveal them again."""
    stats_api.set_achievements(1, [])
    stats_api.set_achievements(2, ["Busy Night"])
    db._ALTS = {2}
    _, ctx = await run()

    payload = next(iter(ctx.bot_data["schall"].values()))
    assert payload["have"] == []
    assert [uid for uid, _ in payload["missing"]] == [1]


async def test_a_roster_of_only_alts_says_so(achievements, no_fts, stats_api):
    """Not the "reply to direct mentions" refusal: the mentions were fine, there was just
    nobody left to check once the alts came out."""
    db._ALTS = {1, 2}
    reply, _ = await run()

    assert "marked as an alt" in reply


async def test_the_remembered_roster_still_holds_the_alt(achievements, no_fts, stats_api):
    """Filtering at check time is what makes unmarking take effect on a cached list."""
    stats_api.set_achievements(1, [])
    stats_api.set_achievements(2, [])
    db._ALTS = {2}
    chat_data = {}
    await run(chat_data=chat_data)
    assert chat_data[search._SCHALL_CACHE_KEY]["users"] == [[1, "Alice"], [2, "Bob"]]

    db._ALTS = set()
    followup = message("/schall busy")
    await search.display_search_all(FakeUpdate(message=followup), FakeContext(args=["busy"], chat_data=chat_data))
    assert "Checked 2 players" in followup.last_reply
