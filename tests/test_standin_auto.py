"""Running the session off the game bot's own messages — `/gm auto`.

Bot-to-bot communication (Bot API 10.0) is what makes this possible, and it changes
something that was true for this bot's whole life: another bot's messages now arrive. Two
groups of tests below, and they guard opposite things.

**The shield.** `handlers.gamesession.game_bot_message` is registered ahead of every
command handler and stops the update unconditionally, because the command words this
module answers belong to the *real* manager and several are answered bare once `/gm` is
on. A bot in the room posting `/gs` — for its own reasons, or by echoing somebody — must
not be issuing that command to us. `test_a_failure_inside_still_stops_the_update` is the
sharpest one: PTB hands an update to the next handler group when an error handler does not
claim it, so a crash in here would leak a bot's message into exactly what it shields.

**The reading.** Everything the automation does is a full reset of somebody's live game,
so the guards matter more than the happy path: only the one *learned* game bot is read, a
message id is acted on once, a list that disagrees with its own header changes nothing,
and the closing message — which carries a player-list header of its own, with the dead
mentioned too — must never be read as a roster.
"""

import pytest
from conftest import FakeChat, FakeEntity, FakeMessage, FakeUpdate, FakeUser, bot_message, message
from telegram.ext import ApplicationHandlerStop
from test_standin_session import invoke, reveal, start_session

import db
import session
import templates as t
from handlers import gamesession

# conftest's bot_message always speaks as this one.
GAME_BOT_ID = 42
OTHER_BOT_ID = 99

ROSTER = [(1, "Ren"), (2, "omu"), (3, "J J")]

TEXT_MENTION = "text_mention"


@pytest.fixture(autouse=True)
def no_bot_admins(monkeypatch):
    """Nobody is a bot-wide admin unless a test says so — /gm consults the admins table."""

    async def not_an_admin(user_id):
        return False

    monkeypatch.setattr(db, "is_admin", not_an_admin)


def roster(alive=ROSTER, dead_rows=(), claimed=None, total=None, message_id=1, chat=None, sender_id=GAME_BOT_ID):
    """The game bot's player list, shaped like the real one.

    "Players Alive: 2/3" over one row per player: the living mentioned, because the game
    bot links them, and the dead as plain text naming the role they were.
    """
    alive = list(alive)
    lines = [
        "Players Alive: {}/{}".format(
            len(alive) if claimed is None else claimed,
            total if total is not None else len(alive) + len(dead_rows),
        )
    ]
    for name, role_text in dead_rows:
        lines.append("{}: \N{SKULL} Dead - {}".format(name, role_text))
    for _, name in alive:
        lines.append("{}: \N{SLIGHTLY SMILING FACE} Alive".format(name))
    entities = [FakeEntity(TEXT_MENTION, user=FakeUser(uid, name)) for uid, name in alive]
    msg = bot_message("\n".join(lines), entities=entities, message_id=message_id, chat=chat)
    msg.from_user = FakeUser(user_id=sender_id, first_name="WerewolfBot", is_bot=True)
    return msg


def closing(alive=ROSTER, total=None, message_id=50):
    """The game bot's closing message: a player list, then the one string it never
    otherwise prints. Every player is mentioned in this one, dead ones included."""
    body = "Players Alive: {} / {}\n".format(len(alive), total if total is not None else len(alive))
    body += "".join("{}: \N{SLIGHTLY SMILING FACE} Alive - the Villager Won\n" for _, _ in alive)
    body += "Game Length: 00:12:34"
    entities = [FakeEntity(TEXT_MENTION, user=FakeUser(uid, name)) for uid, name in alive]
    msg = bot_message(body, entities=entities, message_id=message_id)
    return msg


def auto(context, learned=True):
    """A chat that has asked for this, and been told which bot to follow."""
    context.chat_data[gamesession._GM_KEY] = gamesession._GM_AUTO
    if learned:
        context.chat_data[gamesession._GAME_BOT_KEY] = GAME_BOT_ID


async def seen(context, msg):
    """Feed a bot's message to the watcher, the way the handler table does.

    The stop is asserted on every single call, because it is unconditional by design: this
    handler is the only thing that ever sees a bot speak, and the assertion belongs
    wherever the handler is exercised rather than in one test about it.
    """
    with pytest.raises(ApplicationHandlerStop):
        await gamesession.game_bot_message(FakeUpdate(message=msg), context)


def addressed(text, user_id=1):
    """A command naming this bot, with the BOT_COMMAND entity Telegram would attach."""
    return FakeMessage(
        text=text,
        from_user=FakeUser(user_id, "Ren"),
        entities=[FakeEntity("bot_command", offset=0, length=len(text.split()[0]))],
    )


async def gm(context, arg="", user_id=1, chat_admin=True, bot_admin=False):
    """Run /gm as somebody entitled to. `bot_admin` is whether Telegram would deliver the
    game bot's messages to us at all — the group admin the automation needs."""
    if chat_admin:
        context.bot.chat_admins.add(user_id)
    if bot_admin:
        context.bot.chat_admins.add(context.bot.id)
    msg = addressed("/gm@wwstatsbot {}".format(arg).strip(), user_id=user_id)
    context.args = msg.text.split()[1:]
    await gamesession.game_management_cmd(FakeUpdate(message=msg), context)
    return msg


# --- The shield -------------------------------------------------------------


async def test_every_bot_message_stops_the_update(context):
    """Including one this chat never asked us to read. The point is that nothing further
    in the table sees it, not that we did something with it."""
    msg = bot_message("Here are the groups for something:")

    await seen(context, msg)

    assert msg.replies == []
    assert context.bot.sent == []


async def test_a_bare_command_from_another_bot_is_not_a_command_to_us(context):
    """The failure this exists to prevent. /gs is answered bare once management is on, so
    without the stop, a bot posting one would be starting sessions in the group."""
    auto(context)
    msg = bot_message("/gs", entities=[FakeEntity("bot_command", offset=0, length=3)])
    msg.from_user = FakeUser(user_id=OTHER_BOT_ID, first_name="SomeBot", is_bot=True)

    await seen(context, msg)

    assert session.get(context.chat_data) is None


async def test_a_failure_inside_still_stops_the_update(context, monkeypatch):
    """PTB dispatches the next handler group when an error handler does not claim the
    update, so a crash in here would leak a bot's message into the command words."""

    async def boom(update, context):
        raise RuntimeError("no")

    monkeypatch.setattr(gamesession, "_drive_session", boom)

    with pytest.raises(ApplicationHandlerStop):
        await gamesession.game_bot_message(FakeUpdate(message=bot_message("anything")), context)
    # An edited message arrives with update.message unset, so reporting the failure must not
    # read anything off the update either — that would raise in the handler for raising.
    with pytest.raises(ApplicationHandlerStop):
        await gamesession.game_bot_message(FakeUpdate(message=None), context)


def test_the_filter_matches_bots_and_nobody_else():
    assert gamesession.FROM_A_BOT.filter(bot_message("hello")) is True
    assert gamesession.FROM_A_BOT.filter(message("hello")) is False
    assert gamesession.FROM_A_BOT.filter(FakeMessage(text="hello", from_user=None)) is False


# --- Learning which bot runs the games --------------------------------------


async def test_gs_learns_which_bot_runs_the_games(context):
    """The one thing the automation cannot work out for itself: a group has several bots in
    it, and a roster-shaped message is not proof of anything."""
    await start_session(context)

    assert context.chat_data[gamesession._GAME_BOT_KEY] == GAME_BOT_ID


async def test_ad_learns_it_too(context):
    """A chat whose session was started before any of this existed still teaches us on the
    next /ad, rather than having to be restarted to."""
    await start_session(context)
    del context.chat_data[gamesession._GAME_BOT_KEY]

    await invoke(gamesession.follow_roster_cmd, context, "/ad", reply_to=roster(ROSTER, total=4))

    assert context.chat_data[gamesession._GAME_BOT_KEY] == GAME_BOT_ID


async def test_a_reply_that_started_nothing_teaches_nothing(context):
    """Recorded only from a list we could actually read. A reply to the wrong message is
    how somebody would otherwise teach us that a quiz bot runs the games here."""
    msg = FakeMessage(
        text="/gs@wwstatsbot",
        from_user=FakeUser(1, "Ren"),
        reply_to_message=bot_message("Here are the groups for something:"),
        entities=[FakeEntity("bot_command", offset=0, length=15)],
    )

    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)

    assert gamesession._GAME_BOT_KEY not in context.chat_data


async def test_a_reply_to_a_person_teaches_nothing(context):
    """A player pasting the roster is not the game bot, and following them would mean
    following whatever they paste next."""
    human_roster = FakeMessage(
        text="Players Alive: 3/3",
        from_user=FakeUser(7, "Copycat"),
        entities=[FakeEntity(TEXT_MENTION, user=FakeUser(uid, name)) for uid, name in ROSTER],
    )
    msg = FakeMessage(
        text="/gs@wwstatsbot",
        from_user=FakeUser(1, "Ren"),
        reply_to_message=human_roster,
        entities=[FakeEntity("bot_command", offset=0, length=15)],
    )

    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)

    assert session.get(context.chat_data) is not None
    assert gamesession._GAME_BOT_KEY not in context.chat_data


# --- What is read, and what is not ------------------------------------------


async def test_the_learned_bots_first_roster_opens_a_session(context):
    auto(context)

    await seen(context, roster())

    current = session.get(context.chat_data)
    assert current is not None
    assert [name for _, name in ROSTER] == [entry["name"] for _, entry in session.players_in_order(current)]
    # The roster message it posts is the only announcement an automatic start needs.
    assert len(context.bot.sent) == 1


async def test_nothing_happens_when_management_is_merely_on(context):
    """`auto` is a third state, not the meaning of `on`: a group already running games this
    way must not have its rosters opened for it by a deploy."""
    context.chat_data[gamesession._GM_KEY] = True
    context.chat_data[gamesession._GAME_BOT_KEY] = GAME_BOT_ID

    await seen(context, roster())

    assert session.get(context.chat_data) is None


async def test_another_bot_in_the_room_is_ignored(context):
    """Same message, different sender. Nothing about the text is the authority here."""
    auto(context)

    await seen(context, roster(sender_id=OTHER_BOT_ID))

    assert session.get(context.chat_data) is None


async def test_a_bot_nobody_has_pointed_us_at_yet_is_ignored(context):
    auto(context, learned=False)

    await seen(context, roster())

    assert session.get(context.chat_data) is None


async def test_a_private_chat_is_ignored(context):
    auto(context)

    await seen(context, roster(chat=FakeChat("private", chat_id=5)))

    assert session.get(context.chat_data) is None


async def test_a_joining_list_opens_nothing(context):
    """The game bot posts one of these while players are still joining. It mentions
    everybody who has joined so far and states no counts, so it is not a roster."""
    auto(context)
    msg = bot_message(
        "#players: 3\nRen\nomu\nJ J",
        entities=[FakeEntity(TEXT_MENTION, user=FakeUser(uid, name)) for uid, name in ROSTER],
    )

    await seen(context, msg)

    assert session.get(context.chat_data) is None


async def test_a_list_that_disagrees_with_its_own_header_opens_nothing(context):
    """A roster is applied as a full reset, so a misread must change nothing at all —
    whether it would open a session or reset one."""
    auto(context)

    await seen(context, roster(claimed=9))

    assert session.get(context.chat_data) is None


async def test_a_second_session_is_not_opened_straight_after_the_first(context, monkeypatch):
    """Opening is the one path that costs an API call per player, so it has a floor under
    it. Following a roster does not, and must not: those arrive every phase."""
    at = [1_000.0]
    monkeypatch.setattr(gamesession, "_now", lambda: at[0])
    auto(context)

    await seen(context, roster(message_id=1))
    assert session.get(context.chat_data) is not None
    session.end(context.chat_data)

    at[0] += 30
    await seen(context, roster(message_id=2))
    assert session.get(context.chat_data) is None

    at[0] += 31
    await seen(context, roster(message_id=3))
    assert session.get(context.chat_data) is not None


# --- Following the game ------------------------------------------------------


async def test_a_later_roster_marks_the_dead(context):
    auto(context)
    await seen(context, roster(message_id=1))

    await seen(context, roster(alive=ROSTER[:2], dead_rows=[("J J", "the Villager")], message_id=2))

    current = session.get(context.chat_data)
    assert session.player(current, 3)["alive"] is False
    assert session.player(current, 1)["alive"] is True


async def test_a_death_notice_teaches_the_role_the_player_was(context):
    """Worth reading: a player who never got round to /role still contributes their role to
    everybody else's achievements from the moment they die."""
    auto(context)
    await seen(context, roster(message_id=1))

    await seen(context, roster(alive=ROSTER[:2], dead_rows=[("J J", "the Serial Killer 🔪")], message_id=2))

    assert session.player(session.get(context.chat_data), 3)["roles"] == ["serial_killer"]


async def test_the_same_message_is_acted_on_once(context):
    """Bot-to-bot communication has no natural end to two bots answering each other, so a
    message id is acted on once however many times it arrives."""
    auto(context)
    await seen(context, roster(message_id=1))
    sent = len(context.bot.sent)

    await seen(context, roster(alive=ROSTER[:2], message_id=2))
    await seen(context, roster(alive=ROSTER[:2], message_id=2))

    assert len(context.bot.sent) == sent
    assert session.player(session.get(context.chat_data), 3)["alive"] is False


async def test_a_roster_it_cannot_read_changes_nothing_and_says_nothing(context):
    """An automatic path that complained about every message it could not read would be a
    bot talking over a game."""
    auto(context)
    await seen(context, roster(message_id=1))
    sent = len(context.bot.sent)

    await seen(context, roster(alive=ROSTER[:2], claimed=9, message_id=2))

    assert len(context.bot.sent) == sent
    assert session.player(session.get(context.chat_data), 3)["alive"] is True


async def test_a_transform_is_announced(context):
    """The one thing in a follow that nobody in the chat can see for themselves. A Wild
    Child whose role model was eaten is a wolf from that moment, and it was only ever
    visible as part of /ad's reply."""
    current = await start_session(context, players=ROSTER)
    auto(context)
    await reveal(context, 3, "wc")
    session.set_model(current, 3, 2)

    sent = len(context.bot.sent)

    await seen(context, roster(alive=[ROSTER[0], ROSTER[2]], dead_rows=[("omu", "the Villager")], message_id=2))

    assert session.player(current, 3)["roles"] == ["werewolf"]
    assert len(context.bot.sent) == sent + 1
    assert "Werewolf" in context.bot.sent[-1]["text"]
    # A message of its own, so it must not start with the newline the /ad reply hangs it off.
    assert not context.bot.sent[-1]["text"].startswith("\n")


async def test_deaths_alone_are_not_announced(context):
    """The roster message shows them a few seconds later. Two messages a phase, in a group
    that starts a game every few minutes, is what a stand-in must not be."""
    auto(context)
    await seen(context, roster(message_id=1))
    sent = len(context.bot.sent)

    await seen(context, roster(alive=ROSTER[:2], dead_rows=[("J J", "the Villager")], message_id=2))

    assert len(context.bot.sent) == sent


# --- Closing it out ----------------------------------------------------------


async def test_the_closing_message_ends_the_session(context):
    auto(context)
    await seen(context, roster(message_id=1))
    state_id = session.get(context.chat_data)["state_message_id"]

    await seen(context, closing(alive=ROSTER[:1], total=3))

    assert session.get(context.chat_data) is None
    assert [edit["message_id"] for edit in context.bot.edits] == [state_id]
    assert t.STANDIN_HEADER_ENDED in context.bot.edits[0]["text"]


async def test_the_closing_message_is_never_read_as_a_roster(context):
    """It carries a player-list header of its own, and mentions the dead as well as the
    living — read as a roster it would raise the dead in the last thing anybody sees."""
    auto(context)
    await seen(context, roster(message_id=1))
    await seen(context, roster(alive=ROSTER[:2], dead_rows=[("J J", "the Villager")], message_id=2))
    current = session.get(context.chat_data)

    await seen(context, closing(alive=ROSTER, total=3))

    assert session.player(current, 3)["alive"] is False


async def test_the_roster_comes_down_when_the_game_ends(context):
    """The pin outliving its game is the one thing nobody can undo without going to find
    the message."""
    auto(context)
    context.bot.chat_admins.add(context.bot.id)
    await seen(context, roster(message_id=1))
    pinned = session.get(context.chat_data)["pinned_message_id"]

    await seen(context, closing(alive=ROSTER[:1], total=3))

    assert [u["message_id"] for u in context.bot.unpins] == [pinned]


async def test_a_closing_message_with_no_session_does_nothing(context):
    auto(context)

    await seen(context, closing(alive=ROSTER[:1], total=3))

    assert session.get(context.chat_data) is None
    assert context.bot.edits == []


# --- The switch --------------------------------------------------------------


async def test_gm_auto_is_management_on_and_automatic(context):
    context.chat_data[gamesession._GAME_BOT_KEY] = GAME_BOT_ID

    msg = await gm(context, "auto", bot_admin=True)

    assert gamesession.is_auto(context) is True
    assert gamesession.is_managing(context) is True
    assert msg.replies[0][0] == t.STANDIN_GM_AUTO


async def test_gm_auto_says_so_when_it_cannot_see_the_game_bot(context):
    """Telegram delivers another bot's messages to a group admin and nobody else. Silence
    would leave a group with the switch on, nothing happening, and no way to find out why."""
    msg = await gm(context, "auto")

    assert gamesession.is_auto(context) is True
    assert msg.replies[0][0] == t.STANDIN_GM_AUTO_NEEDS_ADMIN


async def test_gm_auto_says_so_when_it_does_not_know_which_bot_to_follow(context):
    context.chat_data.pop(gamesession._GAME_BOT_KEY, None)

    msg = await gm(context, "auto", bot_admin=True)

    assert t.STANDIN_GM_AUTO_UNLEARNED.format(username="wwstatsbot") == msg.replies[0][0]


async def test_gm_on_after_auto_turns_the_automation_off(context):
    await gm(context, "auto", bot_admin=True)

    await gm(context, "on")

    assert gamesession.is_auto(context) is False
    assert gamesession.is_managing(context) is True


async def test_gm_off_after_auto_turns_everything_off(context):
    await gm(context, "auto", bot_admin=True)

    await gm(context, "off")

    assert gamesession.is_auto(context) is False
    assert gamesession.is_managing(context) is False


async def test_gm_reports_the_automatic_state(context):
    await gm(context, "auto", bot_admin=True)

    msg = await gm(context)

    assert msg.replies[0][0] == t.STANDIN_GM_STATE.format(state=t.STANDIN_GM_STATE_AUTO)


async def test_the_switch_and_the_learned_bot_survive_a_restart(context):
    """Both live in chat_data so RedisPersistence carries them: a group's standing choice
    about which bot runs their games must not quietly revert on a deploy."""
    from conftest import assert_json_roundtrips

    await gm(context, "auto", bot_admin=True)
    context.chat_data[gamesession._GAME_BOT_KEY] = GAME_BOT_ID

    restored = assert_json_roundtrips(context.chat_data)

    assert restored[gamesession._GM_KEY] == gamesession._GM_AUTO
    assert restored[gamesession._GAME_BOT_KEY] == GAME_BOT_ID
