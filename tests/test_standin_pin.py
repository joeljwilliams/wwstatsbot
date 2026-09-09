"""Pinning the roster message for the length of a game.

The roster is the one message people scroll back to mid-round, so it goes to the top of
the chat while the game runs and comes down when it ends. Three things shape it, and each
is tested below:

* **The permission is discovered by using it.** A group that has not made this bot an
  admin gets no pin and no complaint — a game that refused to start over a convenience
  would be worse than one with nothing pinned.
* **Only our own pin is ever removed, and only by id.** `unpin_chat_message` with no
  message clears the group's *most recent* pin, which by the end of a game may be a rules
  post or a bracket somebody else put there.
* **Every way a game can end unpins it.** /gsend, the Stop button and the idle expiry all
  end sessions, and a pinned roster outliving its game misleads whoever scrolls to it.
"""

import pytest
from conftest import FakeBot, FakeCallbackQuery, FakeEntity, FakeMessage, FakeUpdate, FakeUser, bot_message
from telegram.error import BadRequest, Forbidden

import db
import session
from handlers import gamesession

ROSTER = [(1, "Ren"), (2, "omu"), (3, "J J")]


@pytest.fixture(autouse=True)
def no_bot_admins(monkeypatch):
    async def not_an_admin(user_id):
        return False

    monkeypatch.setattr(db, "is_admin", not_an_admin)


def roster_message(players=ROSTER):
    entities = [FakeEntity("text_mention", user=FakeUser(uid, name)) for uid, name in players]
    return bot_message("Players Alive: {n}/{n}".format(n=len(players)), entities=entities)


def addressed(text, user_id=1, name="Ren", reply_to=None):
    return FakeMessage(
        text=text,
        from_user=FakeUser(user_id, name),
        reply_to_message=reply_to,
        entities=[FakeEntity("bot_command", offset=0, length=len(text.split()[0]))],
    )


async def start_session(context, managing=True):
    """Start a game. Pinning is part of game management, so that is on unless a test is
    about what happens when it is not."""
    if managing:
        context.chat_data[gamesession._GM_KEY] = True
    msg = addressed("/gs@wwstatsbot", reply_to=roster_message())
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    return session.get(context.chat_data)


async def end_with_command(context):
    msg = addressed("/gsend@wwstatsbot")
    context.args = []
    await gamesession.end_session_cmd(FakeUpdate(message=msg), context)
    return msg


# --- The switch that governs it ----------------------------------------------------


async def test_nothing_is_pinned_when_management_is_off(context):
    """The default. Pinning is something a *manager* does, and a bot standing in for one
    game has no business rearranging the top of a chat that has not asked it to."""
    current = await start_session(context, managing=False)

    assert context.bot.pins == []
    assert current["pinned_message_id"] is None


async def test_switching_management_off_mid_game_takes_the_pin_down(context):
    """Otherwise the pin outlives the permission, and it is the one thing nobody could
    undo without going and finding the message."""
    await start_session(context)
    pinned_id = session.get(context.chat_data)["pinned_message_id"]

    msg = addressed("/gm@wwstatsbot off")
    context.args = ["off"]
    context.bot.chat_admins.add(1)
    await gamesession.game_management_cmd(FakeUpdate(message=msg), context)

    assert [u["message_id"] for u in context.bot.unpins] == [pinned_id]
    assert gamesession.is_managing(context) is False


# --- Pinning on start --------------------------------------------------------------


async def test_the_roster_message_is_pinned(context):
    current = await start_session(context)

    assert len(context.bot.pins) == 1
    pinned = context.bot.pins[0]
    assert pinned["message_id"] == current["state_message_id"], "the roster, not some other message"
    assert current["pinned_message_id"] == pinned["message_id"]


async def test_the_pin_is_silent(context):
    """A pin notification pings every member, and an active game group starts a game every
    few minutes."""
    await start_session(context)
    assert context.bot.pins[0]["disable_notification"] is True


@pytest.mark.parametrize("refusal", [Forbidden("not enough rights"), BadRequest("not enough rights")])
async def test_a_bot_without_the_permission_still_runs_the_game(context, refusal):
    """The permission is discovered by using it, and its absence is not an error: a game
    that refused to start over a pin would be worse than one with nothing at the top."""
    context.bot = FakeBot(pin_error=refusal)
    current = await start_session(context)

    assert current is not None, "the session started"
    assert current["state_message_id"] is not None, "and the roster was posted"
    assert current["pinned_message_id"] is None, "but nothing was recorded as pinned"


async def test_a_refused_pin_is_not_unpinned_later(context):
    """The stored id is the record of *us having pinned it*. Without that record, a session
    that could not pin would still try to unpin at the end — and clear whatever pin the
    group actually has."""
    context.bot = FakeBot(pin_error=Forbidden("not enough rights"))
    await start_session(context)
    await end_with_command(context)

    assert context.bot.unpins == []


# --- Unpinning at the end ----------------------------------------------------------


async def test_gsend_unpins(context):
    current = await start_session(context)
    pinned_id = current["pinned_message_id"]
    await end_with_command(context)

    assert len(context.bot.unpins) == 1
    assert context.bot.unpins[0]["message_id"] == pinned_id


async def test_the_unpin_names_the_message(context):
    """Never the bare call: with no message id Telegram removes the most recent pin, which
    by the end of a game may be somebody else's post entirely."""
    await start_session(context)
    await end_with_command(context)
    assert context.bot.unpins[0]["message_id"] is not None


async def test_the_stop_button_unpins(context):
    current = await start_session(context)
    pinned_id = current["pinned_message_id"]

    # Two presses from the same player: the first arms, the second ends it.
    query = FakeCallbackQuery(data="standin:stop", from_user=FakeUser(1, "Ren"))
    query.message = FakeMessage(text="roster")
    await gamesession.stop_callback(FakeUpdate(callback_query=query), context)
    await gamesession.stop_callback(FakeUpdate(callback_query=query), context)

    assert session.get(context.chat_data) is None, "the session ended"
    assert [u["message_id"] for u in context.bot.unpins] == [pinned_id]


async def test_the_idle_expiry_unpins(context):
    """The path nobody watches: a game that was simply abandoned still has to give the
    group its pin back."""
    current = await start_session(context)
    pinned_id = current["pinned_message_id"]

    context.job = type("Job", (), {"chat_id": -100})()
    await gamesession._idle_end(context)

    assert session.get(context.chat_data) is None
    assert [u["message_id"] for u in context.bot.unpins] == [pinned_id]


async def test_it_is_unpinned_only_once(context):
    """Ending twice must not send a second unpin at a group that has moved on."""
    await start_session(context)
    current = session.get(context.chat_data)
    await gamesession._finish(context, -100, current)
    await gamesession._finish(context, -100, current)

    assert len(context.bot.unpins) == 1


@pytest.mark.parametrize("refusal", [Forbidden("not enough rights"), BadRequest("message to unpin not found")])
async def test_a_refused_unpin_is_tolerated(context, refusal):
    """Somebody unpinned it by hand, or the permission was taken away mid-game. Either way
    the pin is not there any more, which is the outcome wanted."""
    context.bot = FakeBot(unpin_error=refusal)
    await start_session(context)
    await end_with_command(context)

    assert session.get(context.chat_data) is None, "the game still ended"
    # And the end was still announced: a failed unpin must not swallow the rest of the
    # shutdown, which is what an exception escaping _finish would do.
    assert any("stopped" in sent["text"].lower() for sent in context.bot.sent)


# --- State -------------------------------------------------------------------------


async def test_the_pin_survives_the_persistence_roundtrip(context):
    """chat_data is stored as JSON, and a restart mid-game must still know what to unpin."""
    from conftest import assert_json_roundtrips

    current = await start_session(context)
    pinned_id = current["pinned_message_id"]
    restored = assert_json_roundtrips(context.chat_data)

    assert session.get(restored)["pinned_message_id"] == pinned_id


async def test_a_session_predating_the_field_ends_cleanly(context):
    """One of those is in Redis right now; it has no pin to remove and must not fail."""
    await start_session(context)
    current = session.get(context.chat_data)
    del current["pinned_message_id"]
    context.bot.pins.clear()

    await gamesession._finish(context, -100, current)
    assert context.bot.unpins == []
