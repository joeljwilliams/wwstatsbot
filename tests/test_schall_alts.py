"""Alt accounts are left out of a /schall check.

An alt is a second account of somebody already in the room. Its stats are not that
player's — a fresh account holds almost nothing — so leaving it in means a name that sits
in "not obtained" for every achievement and reads as a real gap in the roster. The
possible-achievements list already drops them for the same reason; this is the other place
a roster is checked player by player.

The filter runs at check time, not when the reply's mentions are remembered, so unmarking
an alt brings them straight back to a cached roster instead of needing a fresh reply. Who
was dropped is deliberately *not* named in the reply: it used to be, and a footer listing
accounts that were never checked is information nobody asked for.
"""

from conftest import FakeContext, FakeEntity, FakeUpdate, FakeUser, bot_message, message

from wwstatsbot.data import db
from wwstatsbot.handlers import common, search


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


async def test_the_reply_does_not_name_who_was_ignored(achievements, no_fts, stats_api):
    stats_api.set_achievements(1, [])
    db._ALTS = {2, 3}
    reply, _ = await run(users=((1, "Alice"), (2, "Bob"), (3, "Cara")))

    assert "Bob" not in reply
    assert "Cara" not in reply
    assert "alt" not in reply


async def test_the_toggled_view_does_not_name_them_either(achievements, no_fts, stats_api):
    from conftest import FakeCallbackQuery

    stats_api.set_achievements(1, [])
    db._ALTS = {2}
    _, ctx = await run()

    token = next(iter(ctx.bot_data["schall"]))
    query = FakeCallbackQuery(data="schall:{}:have".format(token))
    await search.schall_callback(FakeUpdate(callback_query=query), FakeContext(bot_data=ctx.bot_data))

    text, _ = query.edits[-1]
    assert "Bob" not in text
    assert "alt" not in text


async def test_the_stored_payload_still_round_trips(achievements, no_fts, stats_api):
    """With REDIS_URL set the payload survives a restart, so it must stay JSON-clean."""
    from conftest import assert_json_roundtrips

    stats_api.set_achievements(1, [])
    db._ALTS = {2}
    _, ctx = await run()

    assert_json_roundtrips(ctx.bot_data)


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
    assert chat_data[common.PLAYERS_KEY]["users"] == [[1, "Alice"], [2, "Bob"]]

    db._ALTS = set()
    followup = message("/schall busy")
    await search.display_search_all(FakeUpdate(message=followup), FakeContext(args=["busy"], chat_data=chat_data))
    assert "Checked 2 players" in followup.last_reply
