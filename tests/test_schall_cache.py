"""/schall remembering a chat's player list.

Checking a second achievement against the same roster used to mean scrolling back to the
game bot's player list and replying to it again. Two things fill the list now, and
`/schall <achievement>` with **no** reply re-checks whatever is in it: a reply-based run
records the `text_mention` user ids it saw, and a stand-in session records its table — so
a group whose games this bot runs never has to prime it at all.

Three constraints shape the design, and each has tests below:

* **`/sch` is untouched.** With no reply it still means "check my own achievements" — it is
  the advertised command, and silently turning it into a group query would surprise anyone
  asking about themselves. Only `/schall` reads the cache.
* **Per-chat.** The cache lives in `chat_data`, so one group's line-up can never appear in
  another's results.
* **It expires after an hour, and says so.** A game group's roster changes every round, so
  a remembered result must never be mistaken for a fresh one.
"""

import json
import time

from conftest import FakeChat, FakeContext, FakeEntity, FakeUpdate, FakeUser, bot_message, message
from test_standin_auto import auto, roster, seen
from test_standin_session import BRACKETS, start_session

import session
from handlers import common, gamesession, search


def player_mention(user_id=1, name="Alice", offset=0, length=5):
    return FakeEntity("text_mention", offset=offset, length=length, user=FakeUser(user_id, name))


def player_list(*users):
    """A bot message mentioning players, as the game bot posts one."""
    entities, offset = [], 0
    for uid, name in users:
        entities.append(player_mention(uid, name, offset, len(name)))
        offset += len(name) + 1
    return bot_message(" ".join(name for _, name in users), entities=entities)


async def run(msg, ctx, args=("busy",)):
    await search.display_search_all(FakeUpdate(message=msg), FakeContext(args=list(args), **ctx))
    return msg.last_reply


async def reply_run(ctx, users=((1, "Alice"), (2, "Bob")), args=("busy",)):
    """A reply-based /sch run, which is what populates the cache."""
    msg = message("/sch busy", reply_to_message=player_list(*users))
    context = FakeContext(args=list(args), **ctx)
    await search.display_search_all(FakeUpdate(message=msg), context)
    return msg, context


# --- Caching on a reply-based run -------------------------------------------------


async def test_a_reply_based_run_remembers_the_players(achievements, no_fts, stats_api):
    chat_data = {}
    await reply_run({"chat_data": chat_data})

    cached = chat_data[common.PLAYERS_KEY]
    assert cached["users"] == [[1, "Alice"], [2, "Bob"]]
    assert cached["at"] > 0


async def test_the_cache_is_json_serializable(achievements, no_fts, stats_api):
    """chat_data is persisted as JSON by RedisPersistence, so the roster must survive a
    restart — and tuples degrade to lists, which recall_players normalises back."""
    from conftest import assert_json_roundtrips

    chat_data = {}
    await reply_run({"chat_data": chat_data})
    restored = assert_json_roundtrips(chat_data)
    assert restored == chat_data


async def test_a_later_reply_replaces_the_remembered_list(achievements, no_fts, stats_api):
    chat_data = {}
    await reply_run({"chat_data": chat_data}, users=((1, "Alice"),))
    await reply_run({"chat_data": chat_data}, users=((9, "Zoe"), (8, "Yan")))
    assert chat_data[common.PLAYERS_KEY]["users"] == [[9, "Zoe"], [8, "Yan"]]


async def test_an_uncheckable_reply_does_not_wipe_a_good_list(achievements, no_fts, stats_api):
    """Replying to a message of bare @usernames yields no checkable players. That must not
    destroy a roster the chat already had — the user would lose it for no reason."""
    chat_data = {}
    await reply_run({"chat_data": chat_data}, users=((1, "Alice"),))

    usernames_only = bot_message("@dave", entities=[FakeEntity("mention", 0, 5)])
    msg = message("/sch busy", reply_to_message=usernames_only)
    await search.display_search_all(FakeUpdate(message=msg), FakeContext(args=["busy"], chat_data=chat_data))

    assert chat_data[common.PLAYERS_KEY]["users"] == [[1, "Alice"]]


# --- Using the cache --------------------------------------------------------------


async def test_no_reply_checks_the_remembered_players(achievements, no_fts, stats_api):
    stats_api.set_achievements(1, ["Busy Night"])
    stats_api.set_achievements(2, [])
    chat_data = {}
    await reply_run({"chat_data": chat_data})

    followup = message("/schall busy")
    reply = await run(followup, {"chat_data": chat_data})

    # The default view lists only who is missing it: Alice has it, Bob does not.
    assert "Checked 2 players" in reply
    assert "Bob" in reply
    assert "Not obtained (1)" in reply


async def test_a_cached_run_is_marked_with_a_clock_and_its_age(achievements, no_fts, stats_api):
    """Terse on purpose: the clock plus an age qualifies the "Checked N players" line above
    it. What matters is that a remembered result is never indistinguishable from a fresh
    one, not that the message explains itself."""
    chat_data = {}
    await reply_run({"chat_data": chat_data})
    reply = await run(message("/schall busy"), {"chat_data": chat_data})

    assert "🕐" in reply
    assert "just now" in reply


async def test_the_notice_reports_the_lists_actual_age(monkeypatch, achievements, no_fts, stats_api):
    chat_data = {}
    await reply_run({"chat_data": chat_data})

    # Twelve minutes later, still inside the hour.
    real = chat_data[common.PLAYERS_KEY]["at"]
    monkeypatch.setattr(search, "_now", lambda: real + 12 * 60)
    reply = await run(message("/schall busy"), {"chat_data": chat_data})
    assert "🕐" in reply
    assert "12m ago" in reply


async def test_a_fresh_reply_based_run_carries_no_notice(achievements, no_fts, stats_api):
    """Only a remembered result is labelled; a fresh one must not be."""
    msg, _ = await reply_run({"chat_data": {}})
    assert "🕐" not in msg.last_reply


async def test_the_toggle_keeps_the_notice_after_a_cached_run(achievements, no_fts, stats_api):
    """The button re-renders the same result, so the label must persist — and must not
    age past the TTL while the message sits on screen, so it is frozen at run time."""
    from conftest import FakeCallbackQuery

    chat_data, bot_data = {}, {}
    await reply_run({"chat_data": chat_data, "bot_data": bot_data})
    followup = message("/schall busy")
    await search.display_search_all(
        FakeUpdate(message=followup), FakeContext(args=["busy"], chat_data=chat_data, bot_data=bot_data)
    )

    token = list(bot_data["schall"])[-1]  # the cached run, not the fresh one before it
    query = FakeCallbackQuery(data="schall:{}:have".format(token))
    await search.schall_callback(FakeUpdate(callback_query=query), FakeContext(bot_data=bot_data))

    text, _ = query.edits[-1]
    assert "🕐" in text
    assert "just now" in text, "the age must be frozen, not recomputed on every tap"


# --- Expiry ------------------------------------------------------------------------


async def test_a_list_older_than_an_hour_is_refused(monkeypatch, achievements, no_fts, stats_api):
    chat_data = {}
    await reply_run({"chat_data": chat_data})

    real = chat_data[common.PLAYERS_KEY]["at"]
    monkeypatch.setattr(search, "_now", lambda: real + 61 * 60)
    reply = await run(message("/schall busy"), {"chat_data": chat_data})

    assert "more than 60 minutes old" in reply
    assert "Reply to a player list" in reply


async def test_an_expired_list_is_discarded(monkeypatch, achievements, no_fts, stats_api):
    """Left in place it would be re-checked and re-rejected on every future call."""
    chat_data = {}
    await reply_run({"chat_data": chat_data})

    real = chat_data[common.PLAYERS_KEY]["at"]
    monkeypatch.setattr(search, "_now", lambda: real + 61 * 60)
    await run(message("/schall busy"), {"chat_data": chat_data})
    assert common.PLAYERS_KEY not in chat_data


async def test_a_list_just_inside_the_hour_still_works(monkeypatch, achievements, no_fts, stats_api):
    chat_data = {}
    await reply_run({"chat_data": chat_data})
    real = chat_data[common.PLAYERS_KEY]["at"]
    monkeypatch.setattr(search, "_now", lambda: real + 59 * 60)
    reply = await run(message("/schall busy"), {"chat_data": chat_data})
    assert "Checked 2 players" in reply


async def test_nothing_remembered_explains_how_to_start_one(achievements, no_fts, stats_api):
    reply = await run(message("/schall busy"), {"chat_data": {}})
    assert "Reply to a message that mentions players" in reply
    assert "60 minutes" in reply


# --- Isolation -------------------------------------------------------------------


async def test_one_chats_list_never_reaches_another(achievements, no_fts, stats_api):
    """chat_data is per-chat by construction; this pins the consequence rather than the
    mechanism, because leaking a group's roster into another chat would be a privacy bug."""
    group_a, group_b = {}, {}
    await reply_run({"chat_data": group_a}, users=((1, "Alice"),))

    reply = await run(message("/schall busy", chat=FakeChat("group", -200)), {"chat_data": group_b})
    assert "Alice" not in reply
    assert "Reply to a message that mentions players" in reply


# --- /sch's own behaviour is unchanged --------------------------------------------


async def test_sch_with_no_reply_still_checks_the_sender(achievements, no_fts, stats_api):
    """The whole reason only /schall reads the cache. A user asking about themselves must
    not get a group list back just because one was cached earlier in the chat."""
    from handlers import search as search_mod

    chat_data = {}
    await reply_run({"chat_data": chat_data})

    msg = message("/sch night", from_user=FakeUser(7, "Requester"))
    await search_mod.display_search(FakeUpdate(message=msg), FakeContext(args=["night"], chat_data=chat_data))

    reply = msg.last_reply
    assert "for <a href='tg://user?id=7'>Requester</a>" in reply
    assert "Alice" not in reply and "Bob" not in reply


# --- The helpers -----------------------------------------------------------------


def test_describe_age_wording():
    assert common.describe_age(0) == "just now"
    assert common.describe_age(59) == "just now"
    assert common.describe_age(60) == "1m ago"
    assert common.describe_age(12 * 60) == "12m ago"
    assert common.describe_age(59 * 60) == "59m ago"


def test_recall_returns_none_for_an_untouched_chat():
    assert common.recall_players(FakeContext().chat_data, search._now()) is None


def test_recall_normalises_persisted_lists_back_to_tuples():
    """After a Redis round-trip the pairs are lists; the handler must not be able to tell."""
    ctx = FakeContext(chat_data={common.PLAYERS_KEY: {"users": [[1, "Alice"]], "unresolved": [], "at": time.time()}})
    users, unresolved, age = common.recall_players(ctx.chat_data, search._now())
    assert users == [(1, "Alice")]
    assert unresolved == []
    assert age >= 0


# --- The other way in: a session's table ------------------------------------
#
# The stand-in session records its whole table, which is what makes /schall work with no
# reply in a group whose games this bot runs. The table rather than the game bot's latest
# list, deliberately: the game bot stops linking a player once they are out, so its later
# lists name only the living, and a roster that shrank every round would look exactly like
# a mention having gone missing.


async def session_context(chat_data):
    """A chat with a stand-in session running, sharing chat_data with the /schall runs."""
    context = FakeContext(chat_data=chat_data)
    await start_session(context)
    return context


async def test_a_session_remembers_its_table_with_no_reply_at_all(achievements, no_fts, stats_api):
    chat_data = {}
    await session_context(chat_data)

    reply = await run(message("/schall busy"), {"chat_data": chat_data})

    assert "Checked 4 players" in reply
    assert "🕐" in reply, "a remembered list must never pass for a fresh one"


async def test_the_table_keeps_players_who_have_died(achievements, no_fts, stats_api):
    """Achievements do not die with the player, and a list that shrank every round would
    read as a mention having gone missing."""
    chat_data = {}
    context = await session_context(chat_data)
    living = [(uid, entry["name"]) for uid, entry in session.players_in_order(session.get(chat_data))][:3]

    await gamesession.follow_roster_cmd(
        FakeUpdate(message=message("/ad", reply_to_message=roster(living, total=4))), context
    )

    assert session.player(session.get(chat_data), 4) is not None
    reply = await run(message("/schall busy"), {"chat_data": chat_data})
    assert "Checked 4 players" in reply


async def test_following_a_list_re_confirms_the_age(monkeypatch, achievements, no_fts, stats_api):
    """The age reads "when this line-up was last confirmed", so a live game says "just now"
    however long ago it started."""
    chat_data = {}
    context = await session_context(chat_data)
    started = chat_data[common.PLAYERS_KEY]["at"]

    monkeypatch.setattr(gamesession, "_now", lambda: started + 40 * 60)
    players = [(uid, entry["name"]) for uid, entry in session.players_in_order(session.get(chat_data))]
    await gamesession.follow_roster_cmd(
        FakeUpdate(message=message("/ad", reply_to_message=roster(players, total=4))), context
    )

    monkeypatch.setattr(search, "_now", lambda: started + 40 * 60)
    reply = await run(message("/schall busy"), {"chat_data": chat_data})
    assert "just now" in reply, "a followed roster re-confirms the table"


async def test_a_roster_read_off_the_game_bot_is_remembered(achievements, no_fts, stats_api):
    """The whole point: nobody typed anything. /gm auto opened the session from the game
    bot's own list, and /schall can check it."""
    chat_data = {}
    context = FakeContext(chat_data=chat_data)
    auto(context)
    await seen(context, roster())

    reply = await run(message("/schall busy"), {"chat_data": chat_data})

    assert "Checked 3 players" in reply


async def test_a_session_table_survives_a_persistence_roundtrip(achievements, no_fts, stats_api):
    """Names are stored unescaped and come back as lists, like every other remembered
    list — /schall must not be able to tell where the table came from."""
    chat_data = {}
    await session_context(chat_data)

    restored = json.loads(json.dumps(chat_data[common.PLAYERS_KEY]))
    users, _, _ = common.recall_players({common.PLAYERS_KEY: restored}, search._now())

    # A real name from the group, and the one that broke the incumbent's own reply: stored
    # unescaped, escaped once at render time, so a round-trip cannot double-escape it.
    assert BRACKETS in [name for _, name in users]
    assert all(isinstance(pair, tuple) for pair in users)
