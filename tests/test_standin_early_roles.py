"""Reveals typed before the roster exists.

The game bot announces that it is assigning roles, tells every player theirs in PM, and
posts its player list five to ten seconds later. Players answer their PM at once — into a
chat where this bot has no session to record it against, so `/role ga` went silently
nowhere and the player was left believing it had landed.

The announcement therefore opens a buffer, `/role` writes into it, and the roster folds it
in the moment one arrives. Two properties carry the whole feature and most of the tests
below are about one of them: **nothing is said** while the buffer is filling, because the
roster is seconds from saying it to everybody; and **nothing is trusted** — only
self-reveals, only in a chat that asked this bot to manage its games, only for players the
roster turns out to name, and only for the minute the gap it covers fits inside.
"""

import pytest
from conftest import FakeUser, assert_json_roundtrips, bot_message
from test_standin_auto import GAME_BOT_ID, auto, roster, seen
from test_standin_session import ROSTER, invoke, mention, roster_message, start_session

from wwstatsbot.game import session
from wwstatsbot.handlers import gamesession
from wwstatsbot.render import templates as t

STARTING = "Game is starting, please wait while I assign roles and update the database."


def starting(message_id=5, sender_id=GAME_BOT_ID, text=STARTING):
    """The line the engine posts as it begins dealing."""
    msg = bot_message(text, message_id=message_id)
    msg.from_user = FakeUser(user_id=sender_id, first_name="WerewolfBot", is_bot=True)
    return msg


def managed(context, learned=True):
    """A chat that opens its sessions by hand: `/gm on`, and a game bot it has learned."""
    context.chat_data[gamesession._GM_KEY] = True
    if learned:
        context.chat_data[gamesession._GAME_BOT_KEY] = GAME_BOT_ID


def clock(monkeypatch, at):
    """Move the one clock this feature reads."""
    monkeypatch.setattr(gamesession, "_now", lambda: at)


def buffered(context):
    """What is being held, as {user_id: roles}."""
    entry = context.chat_data.get(session.EARLY_KEY)
    return {} if entry is None else entry["roles"]


# --- Opening the buffer ------------------------------------------------------


async def test_the_starting_announcement_opens_the_buffer(context):
    auto(context)
    await seen(context, starting())
    assert buffered(context) == {}, "open, and holding nothing yet"
    assert session.early_open(context.chat_data, gamesession._now(), gamesession._EARLY_ROLE_SECONDS)


async def test_a_chat_that_opens_its_sessions_by_hand_gets_it_too(context):
    """`/gm on` has the longer gap to lose reveals in — /gs waits for somebody to notice."""
    managed(context)
    await seen(context, starting())
    assert session.early_open(context.chat_data, gamesession._now(), gamesession._EARLY_ROLE_SECONDS)


async def test_a_chat_that_has_not_asked_is_untouched(context):
    """Management off. This handler still sees every bot in the room, and does nothing."""
    context.chat_data[gamesession._GAME_BOT_KEY] = GAME_BOT_ID
    await seen(context, starting())
    assert session.EARLY_KEY not in context.chat_data


async def test_another_bot_announcing_a_game_opens_nothing(context):
    """Only the learned game bot is ever read — the rule the whole automation rests on."""
    auto(context)
    await seen(context, starting(sender_id=777))
    assert session.EARLY_KEY not in context.chat_data


async def test_nothing_is_held_while_a_session_is_running(context):
    """A live game records reveals in the ordinary way; there is no gap to cover."""
    auto(context)
    await start_session(context)
    await seen(context, starting())
    assert session.EARLY_KEY not in context.chat_data


async def test_the_announcement_repeated_keeps_what_was_typed_since(context):
    """The game bot editing its own message must not throw away the reveals it caused."""
    auto(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role seer")
    await seen(context, starting(message_id=6))
    assert buffered(context) == {"1": ["seer"]}


# --- Filling it --------------------------------------------------------------


async def test_a_role_typed_in_the_gap_is_held_and_answered_with_silence(context):
    auto(context)
    await seen(context, starting())
    msg = await invoke(gamesession.role_cmd, context, "/role ga")
    assert msg.replies == [], "the roster is seconds away and about to say it to everybody"
    assert buffered(context) == {"1": ["guardian_angel"]}


async def test_a_role_typed_with_no_game_starting_is_held_by_nothing(context):
    """The gate is the announcement, not the switch: /gm on alone changes nothing."""
    managed(context)
    msg = await invoke(gamesession.role_cmd, context, "/role seer")
    assert msg.replies == []
    assert session.EARLY_KEY not in context.chat_data


async def test_correcting_yourself_in_the_gap_keeps_the_last_word(context):
    auto(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role seer")
    await invoke(gamesession.role_cmd, context, "/role fool")
    assert buffered(context) == {"1": ["fool"]}


async def test_a_role_nobody_can_place_is_refused(context):
    """Answered, unlike everything else here: nothing was recorded, and there is no roster
    row to read instead."""
    auto(context)
    await seen(context, starting())
    msg = await invoke(gamesession.role_cmd, context, "/role wizard")
    assert "don't know a role" in msg.last_reply
    assert buffered(context) == {}


async def test_a_role_aimed_at_somebody_else_is_not_recorded_against_the_sender(context):
    """There is no roster to resolve anybody against yet, so a mention can only be dropped."""
    auto(context)
    await seen(context, starting())
    msg = await invoke(gamesession.role_cmd, context, "/role seer", mentions=[ROSTER[1]])
    assert msg.replies == []
    assert buffered(context) == {}


async def test_a_reply_is_left_alone_for_the_same_reason(context):
    auto(context)
    await seen(context, starting())
    msg = await invoke(gamesession.role_cmd, context, "/role seer", reply_to=roster_message())
    assert msg.replies == []
    assert buffered(context) == {}


@pytest.mark.parametrize("claim", ["bhns", "bhws @someone", "beholder @someone"])
async def test_a_beholder_claim_is_told_to_wait(context, claim):
    """It both names a player and answers the Seer/Fool question for the table, and neither
    is possible without a roster. "I don't know that role" would be a lie."""
    auto(context)
    await seen(context, starting())
    msg = await invoke(gamesession.role_cmd, context, "/role {}".format(claim))
    assert msg.last_reply == t.STANDIN_ROLE_TOO_EARLY
    assert buffered(context) == {}


async def test_a_plain_beholder_is_an_ordinary_role(context):
    """`/role bh` with nothing after it claims nothing about anybody else."""
    auto(context)
    await seen(context, starting())
    msg = await invoke(gamesession.role_cmd, context, "/role bh")
    assert msg.replies == []
    assert buffered(context) == {"1": ["beholder"]}


async def test_someone_outside_a_live_roster_cannot_write_by_being_early(context):
    """The other reason /role finds no session. A game they are not playing in is not one
    they may reach by typing before it opened."""
    auto(context)
    await seen(context, starting())
    await start_session(context)
    msg = await invoke(gamesession.role_cmd, context, "/role seer", user_id=999, name="Passer By")
    assert msg.replies == []
    assert session.EARLY_KEY not in context.chat_data


async def test_what_is_held_survives_a_restart(context):
    """chat_data is persisted as JSON: dict keys come back as strings, tuples as lists."""
    auto(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role sf")
    assert_json_roundtrips(context.chat_data[session.EARLY_KEY])


# --- Spending it -------------------------------------------------------------


async def test_the_roster_folds_in_what_was_typed_before_it_arrived(context):
    auto(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role ga")
    await invoke(gamesession.role_cmd, context, "/role seer", user_id=2, name="omu")

    await seen(context, roster())
    session_data = session.get(context.chat_data)
    assert session.player(session_data, 1)["roles"] == ["guardian_angel"]
    assert session.player(session_data, 2)["roles"] == ["seer"]


async def test_the_roster_shows_them_the_moment_it_is_posted(context):
    """The only confirmation an early reveal ever gets, so it has to be on the first post
    rather than on the edit after it."""
    auto(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role ga")

    await seen(context, roster())
    assert "Guardian Angel" in context.bot.sent[0]["text"]


async def test_a_session_opened_by_hand_spends_it_too(context):
    """The gap is longer here, not shorter: /gs waits for a human."""
    managed(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role ga")

    session_data = await start_session(context)
    assert session.player(session_data, 1)["roles"] == ["guardian_angel"]


async def test_somebody_who_is_not_playing_is_dropped(context):
    """Watching rather than playing — set_roles answers None and nothing is stored."""
    auto(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role ga", user_id=999, name="Passer By")

    await seen(context, roster())
    assert session.player(session.get(context.chat_data), 999) is None


async def test_the_buffer_is_spent_once(context):
    """A second roster is a later phase of the same game, not a second chance to apply
    reveals that are minutes old by then."""
    auto(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role ga")
    await seen(context, roster())
    assert session.EARLY_KEY not in context.chat_data


async def test_a_stale_buffer_is_thrown_away_rather_than_applied(context, monkeypatch):
    """Role claims go stale in a running game, and a buffer nobody ever opened a session
    for is the wreckage of a game that is long over."""
    auto(context)
    clock(monkeypatch, 1000)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role ga")

    clock(monkeypatch, 1000 + gamesession._EARLY_ROLE_SECONDS + 1)
    await seen(context, roster())
    session_data = session.get(context.chat_data)
    assert session.player(session_data, 1)["roles"] == []
    assert session.EARLY_KEY not in context.chat_data, "cleared either way: the chat has moved on"


async def test_a_reveal_typed_after_the_window_is_not_held(context, monkeypatch):
    auto(context)
    clock(monkeypatch, 1000)
    await seen(context, starting())

    clock(monkeypatch, 1000 + gamesession._EARLY_ROLE_SECONDS + 1)
    msg = await invoke(gamesession.role_cmd, context, "/role ga")
    assert msg.replies == []
    assert buffered(context) == {}


async def test_the_beholder_settles_a_pair_held_in_the_gap(context):
    """An unsure seer/fool claim is settled by set_roles on the way in, exactly as one
    typed a minute later would be — the folding-in is not a way around the rules."""
    auto(context)
    await seen(context, starting())
    await invoke(gamesession.role_cmd, context, "/role sf", user_id=2, name="omu")

    await seen(context, roster())
    session_data = session.get(context.chat_data)
    msg = await invoke(gamesession.role_cmd, context, "/role bhns")
    assert session.player(session_data, 2)["roles"] == ["fool"]
    assert mention(2, "omu") in msg.last_reply, "and whoever was settled is told, as ever"


async def test_switching_management_off_stops_it_filling(context):
    """A chat that turned this bot off in the middle of a buffer has said which bot runs
    its games, and it is not this one."""
    auto(context)
    await seen(context, starting())
    context.chat_data[gamesession._GM_KEY] = False
    msg = await invoke(gamesession.role_cmd, context, "/role ga")
    assert msg.replies == []
    assert buffered(context) == {}
