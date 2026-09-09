"""The lynch order: /lo, /slo and /rslo.

The rotating order is the living roster with the first name repeated at the bottom, so
everybody lynches the name below them and every player receives exactly one vote. That
cycle property is what the feature is *for*, so it is asserted directly rather than
inferred from the rendered lines.

Three constraints shape the rest, and each is tested below:

* **Addressed or silent.** `lo`, `slo` and `rslo` are short words another bot in the room
  may own, and answering somebody else's command in a running game is the failure that
  matters. A bare `/lo` gets nothing at all.
* **Being addressed changes what silence means.** A chat with no session is *told* so,
  because somebody who named this bot outright is owed an answer — unlike the incumbent's
  command words, which stay quiet.
* **A typed order wins until it is cleared**, and clearing it goes back to the rotating
  one. `/slo` with nothing to set is that same instruction, so it resets rather than
  refusing.
"""

import html

import pytest
from conftest import FakeBot, FakeEntity, FakeMessage, FakeUpdate, FakeUser, bot_message

import db
import session
from handlers import gamesession

ROSTER = [(1, "Ren"), (2, "omu"), (3, "J J")]
BRACKETS = "\N{MODIFIER LETTER SMALL TURNED ALPHA}ѕнαяиαѕ <\N{CHERRY BLOSSOM}> \N{THIRD PLACE MEDAL}"


@pytest.fixture(autouse=True)
def no_bot_admins(monkeypatch):
    """_may_manage consults the admins table, and there is no database here."""

    async def not_an_admin(user_id):
        return False

    monkeypatch.setattr(db, "is_admin", not_an_admin)


def roster_message(players=ROSTER):
    entities = [FakeEntity("text_mention", user=FakeUser(uid, name)) for uid, name in players]
    return bot_message("Players Alive: {n}/{n}".format(n=len(players)), entities=entities)


def addressed(text, user_id=1, name="Ren", reply_to=None):
    """A command message with the BOT_COMMAND entity Telegram attaches."""
    return FakeMessage(
        text=text,
        from_user=FakeUser(user_id, name),
        reply_to_message=reply_to,
        entities=[FakeEntity("bot_command", offset=0, length=len(text.split()[0]))],
    )


async def start_session(context, players=ROSTER):
    msg = addressed("/gs@wwstatsbot", reply_to=roster_message(players))
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    return session.get(context.chat_data)


async def invoke(handler, context, text, user_id=1, name="Ren", reply_to=None):
    msg = addressed(text, user_id=user_id, name=name, reply_to=reply_to)
    context.args = msg.text.split()[1:]
    await handler(FakeUpdate(message=msg), context)
    return msg


async def show(context, **kwargs):
    return await invoke(gamesession.lynch_order_cmd, context, "/lo@wwstatsbot", **kwargs)


async def set_order(context, order="", **kwargs):
    text = "/slo@wwstatsbot" + (" " + order if order else "")
    return await invoke(gamesession.set_lynch_order_cmd, context, text, **kwargs)


async def reset_order(context, **kwargs):
    return await invoke(gamesession.reset_lynch_order_cmd, context, "/rslo@wwstatsbot", **kwargs)


# --- The rotating order, and its cycle ---------------------------------------------


def test_the_rotating_order_repeats_the_first_name_at_the_bottom():
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    assert session.rotating_lynch_order(session.get(chat_data)) == ["Ren", "omu", "J J", "Ren"]


def test_every_player_receives_exactly_one_vote():
    """The property the repeated name exists for: read as "lynch the name below you", the
    list hands each player exactly one vote and nobody two."""
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    order = session.rotating_lynch_order(session.get(chat_data))

    votes = {}
    for _voter, target in zip(order[:-1], order[1:], strict=True):
        votes[target] = votes.get(target, 0) + 1
    assert votes == {"Ren": 1, "omu": 1, "J J": 1}
    assert len(order) == len(ROSTER) + 1, "one longer than the roster, which closes the cycle"


def test_nobody_lynches_themselves():
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    order = session.rotating_lynch_order(session.get(chat_data))
    assert all(voter != target for voter, target in zip(order[:-1], order[1:], strict=True))


def test_the_dead_are_left_out():
    """A dead player can neither vote nor be voted for; leaving them in would point two
    players at a corpse."""
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    current = session.get(chat_data)
    session.set_alive(current, 2, False)
    assert session.rotating_lynch_order(current) == ["Ren", "J J", "Ren"]


def test_a_lone_survivor_is_not_told_to_lynch_themselves():
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    current = session.get(chat_data)
    session.set_alive(current, 2, False)
    session.set_alive(current, 3, False)
    assert session.rotating_lynch_order(current) == ["Ren"]


def test_an_empty_roster_has_no_order():
    chat_data = {}
    session.start(chat_data, 1, [], [], 1000.0)
    assert session.rotating_lynch_order(session.get(chat_data)) == []


# --- /lo -------------------------------------------------------------------------


async def test_lo_shows_the_rotating_order(context):
    await start_session(context)
    msg = await show(context)

    assert msg.last_reply == (
        "<b>Lynch order</b>\nRen\nomu\nJ J\nRen\n<i>Rotating: everyone lynches the name below them.</i>\n"
    )


async def test_lo_says_which_order_it_is_showing(context):
    """A set order nobody remembers setting is otherwise indistinguishable from the
    default, and the two behave differently when somebody dies."""
    await start_session(context)
    assert "Rotating" in (await show(context)).last_reply

    await set_order(context, "omu then Ren")
    shown = (await show(context)).last_reply
    assert "(set)" in shown
    assert "Rotating" not in shown


async def test_lo_follows_a_death_without_being_retyped(context):
    await start_session(context)
    session.set_alive(session.get(context.chat_data), 2, False)
    assert "omu" not in (await show(context)).last_reply


async def test_a_name_with_brackets_is_escaped(context):
    """Unescaped, one player's name truncates the whole message."""
    await start_session(context, players=[(1, "Ren"), (2, BRACKETS)])
    reply = (await show(context)).last_reply
    assert html.escape(BRACKETS) in reply
    assert BRACKETS not in reply


async def test_lo_uses_plain_names_not_mentions(context):
    """/lo is asked for repeatedly during a round, and a tappable mention notifies the
    player every time it is. Nobody reading a sequence needs to tap it."""
    await start_session(context)
    assert "tg://user" not in (await show(context)).last_reply


async def test_lo_when_everyone_is_dead_says_so(context):
    await start_session(context)
    current = session.get(context.chat_data)
    for uid, _ in ROSTER:
        session.set_alive(current, uid, False)
    assert "nobody left" in (await show(context)).last_reply


# --- Addressing ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "text"),
    [
        (gamesession.lynch_order_cmd, "/lo"),
        (gamesession.set_lynch_order_cmd, "/slo A then B"),
        (gamesession.reset_lynch_order_cmd, "/rslo"),
    ],
)
async def test_a_bare_command_is_ignored_completely(context, handler, text):
    """Short words another bot may own. Answering one in a running game is the failure
    that matters, so an unaddressed command produces nothing at all."""
    await start_session(context)
    msg = await invoke(handler, context, text)

    assert msg.replies == []
    assert session.lynch_order(session.get(context.chat_data)) is None


async def test_addressing_is_case_insensitive(context):
    await start_session(context)
    msg = await invoke(gamesession.lynch_order_cmd, context, "/lo@WWStatsBot")
    assert msg.replies, "the casing of a bot's own username is not something to police"


@pytest.mark.parametrize(
    ("handler", "text"),
    [
        (gamesession.lynch_order_cmd, "/lo@wwstatsbot"),
        (gamesession.set_lynch_order_cmd, "/slo@wwstatsbot A then B"),
        (gamesession.reset_lynch_order_cmd, "/rslo@wwstatsbot"),
    ],
)
async def test_no_session_is_answered_rather_than_ignored(context, handler, text):
    """Unlike the incumbent's command words, these were addressed to this bot by name —
    saying nothing would read as broken rather than as staying out of the way."""
    msg = await invoke(handler, context, text)
    assert "No game is running here" in msg.last_reply
    assert "/gs@wwstatsbot" in msg.last_reply


# --- /slo ------------------------------------------------------------------------


async def test_slo_sets_a_typed_order(context):
    await start_session(context)
    msg = await set_order(context, "omu then Ren then J J")

    assert session.lynch_order(session.get(context.chat_data)) == "omu then Ren then J J"
    assert "Lynch order set" in msg.last_reply
    assert "omu then Ren then J J" in msg.last_reply


async def test_a_typed_order_survives_a_death(context):
    """It is what somebody wrote, not a view of the roster, so nothing about it changes."""
    await start_session(context)
    await set_order(context, "omu then Ren")
    session.set_alive(session.get(context.chat_data), 2, False)
    assert "omu then Ren" in (await show(context)).last_reply


async def test_slo_takes_the_replied_to_message_when_given_no_argument(context):
    await start_session(context)
    written_down = bot_message("J J\nomu\nRen\nJ J")
    msg = await set_order(context, reply_to=written_down)

    assert session.lynch_order(session.get(context.chat_data)) == "J J\nomu\nRen\nJ J"
    assert "Lynch order set" in msg.last_reply


async def test_a_typed_argument_beats_a_reply(context):
    """Naming the order outright is the more specific instruction."""
    await start_session(context)
    msg = await set_order(context, "typed wins", reply_to=bot_message("replied loses"))
    assert session.lynch_order(session.get(context.chat_data)) == "typed wins"
    assert "replied loses" not in msg.last_reply


async def test_slo_reads_a_caption_too(context):
    """A media message carries its text in caption, and a lynch order posted as an image
    caption is still a lynch order."""
    await start_session(context)
    captioned = FakeMessage(text=None, caption="Ren\nomu\nRen")
    await set_order(context, reply_to=captioned)
    assert session.lynch_order(session.get(context.chat_data)) == "Ren\nomu\nRen"


async def test_slo_with_nothing_to_set_resets(context):
    """ "Set it to nothing" and "go back to the rotating order" are the same instruction,
    and refusing would only make somebody type /rslo to say what they just said."""
    await start_session(context)
    await set_order(context, "something typed")
    msg = await set_order(context)

    assert session.lynch_order(session.get(context.chat_data)) is None
    assert "cleared" in msg.last_reply
    assert "Rotating" in msg.last_reply, "and it shows what is now in force"


async def test_an_order_that_is_only_whitespace_resets(context):
    await start_session(context)
    await set_order(context, "something typed")
    await set_order(context, "   ")
    assert session.lynch_order(session.get(context.chat_data)) is None


async def test_a_typed_order_is_escaped(context):
    """The one place this module renders text it did not compose."""
    await start_session(context)
    msg = await set_order(context, "<b>A</b> then B")
    assert "&lt;b&gt;A&lt;/b&gt;" in msg.last_reply
    assert "<b>A</b>" not in msg.last_reply


async def test_an_over_long_order_is_refused_where_it_is_set(context):
    """Stored orders are re-rendered on every /lo, so the cap belongs here rather than
    being discovered when Telegram refuses a 4096-character reply."""
    await start_session(context)
    msg = await set_order(context, "x" * (gamesession._LYNCH_ORDER_MAX + 1))

    assert session.lynch_order(session.get(context.chat_data)) is None
    assert "too long" in msg.last_reply


async def test_an_order_at_the_limit_is_accepted(context):
    await start_session(context)
    await set_order(context, "x" * gamesession._LYNCH_ORDER_MAX)
    assert session.lynch_order(session.get(context.chat_data)) is not None


# --- /rslo -----------------------------------------------------------------------


async def test_rslo_clears_a_typed_order(context):
    await start_session(context)
    await set_order(context, "omu then Ren")
    msg = await reset_order(context)

    assert session.lynch_order(session.get(context.chat_data)) is None
    assert "cleared" in msg.last_reply
    assert "Ren\nomu\nJ J\nRen" in msg.last_reply, "the rotating order is shown, not just named"


async def test_rslo_on_an_already_rotating_order_is_harmless(context):
    await start_session(context)
    msg = await reset_order(context)
    assert session.lynch_order(session.get(context.chat_data)) is None
    assert "cleared" in msg.last_reply


# --- Who may change it -----------------------------------------------------------


async def test_a_player_may_set_it(context):
    await start_session(context)
    msg = await set_order(context, "omu first", user_id=2, name="omu")
    assert "Lynch order set" in msg.last_reply


async def test_a_passer_by_may_not(context):
    """Asserted on the stored order, not merely on the refusal. Note the id: 999 is the
    suite's SUPERUSER_ID, who may manage anything, so a passer-by has to be somebody
    else — the first version of this test picked 999 and passed for the wrong reason."""
    await start_session(context)
    msg = await set_order(context, "chaos", user_id=777, name="Passer By")

    assert session.lynch_order(session.get(context.chat_data)) is None
    assert "admins" in msg.last_reply


async def test_a_group_admin_may(context):
    """Not necessarily playing, and the person who fixes something from outside the
    roster."""
    context.bot = FakeBot(chat_admins=(777,))
    await start_session(context)
    msg = await set_order(context, "by an admin", user_id=777, name="Chat Admin")
    assert session.lynch_order(session.get(context.chat_data)) == "by an admin"
    assert "Lynch order set" in msg.last_reply


async def test_a_passer_by_may_still_read_it(context):
    """Reading is harmless, and refusing it would be unhelpful to a spectator."""
    await start_session(context)
    msg = await show(context, user_id=777, name="Passer By")
    assert "Lynch order" in msg.last_reply


# --- State ------------------------------------------------------------------------


async def test_the_order_survives_the_persistence_roundtrip(context):
    """chat_data is stored as JSON, so a restart must not lose what somebody typed."""
    from conftest import assert_json_roundtrips

    await start_session(context)
    await set_order(context, "omu then Ren")
    restored = assert_json_roundtrips(context.chat_data)
    assert session.lynch_order(session.get(restored)) == "omu then Ren"


async def test_a_session_predating_the_field_still_works(context):
    """One of those is in Redis right now, and it must not take a command down."""
    await start_session(context)
    del session.get(context.chat_data)["lynch_order"]
    msg = await show(context)
    assert "Rotating" in msg.last_reply


async def test_setting_the_order_counts_as_activity(context):
    """A group setting the order is plainly still playing, so the idle timer restarts —
    otherwise the session could expire underneath them."""
    current = await start_session(context)
    current["last_activity"] = 0.0
    await set_order(context, "omu then Ren")
    assert current["last_activity"] > 0.0
    assert context.job_queue.pending(), "the idle clock is running again"


async def test_ending_a_session_forgets_the_order(context):
    """The rotating order is a fact about this roster, so an override of it means nothing
    in the next game."""
    await start_session(context)
    await set_order(context, "omu then Ren")
    session.end(context.chat_data)
    await start_session(context)
    assert session.lynch_order(session.get(context.chat_data)) is None
