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


def pointing(text, mentions=(), user_id=1, name="Ren", reply_to=None):
    """An addressed command carrying a real text_mention entity for each player named.

    Built rather than faked: an id in an entity is the only way these tests can be wrong
    in the same direction production would be.
    """
    body = text
    entities = [FakeEntity("bot_command", offset=0, length=len(text.split()[0]))]
    for uid, uname in mentions:
        body += " "
        entities.append(FakeEntity("text_mention", offset=len(body), length=len(uname), user=FakeUser(uid, uname)))
        body += uname
    return FakeMessage(text=body, from_user=FakeUser(user_id, name), reply_to_message=reply_to, entities=entities)


def handle_mention(text, handles, user_id=1, name="Ren"):
    """An addressed command carrying @handle MENTION entities, which carry no id at all."""
    body = text
    entities = [FakeEntity("bot_command", offset=0, length=len(text.split()[0]))]
    for handle in handles:
        body += " "
        entities.append(FakeEntity("mention", offset=len(body), length=len(handle)))
        body += handle
    return FakeMessage(text=body, from_user=FakeUser(user_id, name), entities=entities)


async def run(context, msg):
    context.args = msg.text.split()[1:]
    await gamesession.set_lynch_order_cmd(FakeUpdate(message=msg), context)
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
    assert session.rotating_lynch_order(session.get(chat_data)) == [
        (1, "Ren"),
        (2, "omu"),
        (3, "J J"),
        (1, "Ren"),
    ]


def test_every_player_receives_exactly_one_vote():
    """The property the repeated name exists for: read as "lynch the name below you", the
    list hands each player exactly one vote and nobody two."""
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    order = [name for _, name in session.rotating_lynch_order(session.get(chat_data))]

    votes = {}
    for _voter, target in zip(order[:-1], order[1:], strict=True):
        votes[target] = votes.get(target, 0) + 1
    assert votes == {"Ren": 1, "omu": 1, "J J": 1}
    assert len(order) == len(ROSTER) + 1, "one longer than the roster, which closes the cycle"


def test_nobody_lynches_themselves():
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    order = [name for _, name in session.rotating_lynch_order(session.get(chat_data))]
    assert all(voter != target for voter, target in zip(order[:-1], order[1:], strict=True))


def test_the_dead_are_left_out():
    """A dead player can neither vote nor be voted for; leaving them in would point two
    players at a corpse."""
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    current = session.get(chat_data)
    session.set_alive(current, 2, False)
    assert session.rotating_lynch_order(current) == [(1, "Ren"), (3, "J J"), (1, "Ren")]


def test_a_lone_survivor_is_not_told_to_lynch_themselves():
    chat_data = {}
    session.start(chat_data, 1, ROSTER, [], 1000.0)
    current = session.get(chat_data)
    session.set_alive(current, 2, False)
    session.set_alive(current, 3, False)
    assert session.rotating_lynch_order(current) == [(1, "Ren")]


def test_an_empty_roster_has_no_order():
    chat_data = {}
    session.start(chat_data, 1, [], [], 1000.0)
    assert session.rotating_lynch_order(session.get(chat_data)) == []


# --- /lo -------------------------------------------------------------------------


async def test_lo_shows_the_rotating_order(context):
    await start_session(context)
    msg = await show(context)

    assert msg.last_reply == (
        "<b>Lynchorder:</b>\n"
        "<a href='tg://user?id=1'>Ren</a>\n"
        "<a href='tg://user?id=2'>omu</a>\n"
        "<a href='tg://user?id=3'>J J</a>\n"
        "<a href='tg://user?id=1'>Ren</a>\n"
    )


async def test_lo_says_which_order_it_is_showing(context):
    """A set order nobody remembers setting is otherwise indistinguishable from the
    default, and the two behave differently when somebody dies. The incumbent has one mode
    and says only "Lynchorder:", so the marker is ours to add."""
    await start_session(context)
    assert "(set)" not in (await show(context)).last_reply

    await set_order(context, "omu then Ren")
    assert "(set)" in (await show(context)).last_reply


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


async def test_every_name_in_the_list_is_a_mention(context):
    """As in the real manager's own lynchorder and this bot's roster: the list is read to
    find yourself in it, and a plain name is neither tappable nor unambiguous when two
    players have chosen similar ones."""
    await start_session(context)
    reply = (await show(context)).last_reply
    for uid, name in ROSTER:
        assert "<a href='tg://user?id={}'>{}</a>".format(uid, name) in reply


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
    assert "The lynchorder was set by" in msg.last_reply
    assert "<a href='tg://user?id=1'>Ren</a>" in msg.last_reply, "and by whom"
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
    assert "The lynchorder was set by" in msg.last_reply


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
    assert "The lynchorder was reset by" in msg.last_reply
    assert "<b>Lynchorder:</b>" in msg.last_reply, "and it shows what is now in force"


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
    assert "The lynchorder was reset by" in msg.last_reply
    assert "<a href='tg://user?id=2'>omu</a>" in msg.last_reply, "the order is shown, not just named"


async def test_rslo_on_an_already_rotating_order_is_harmless(context):
    await start_session(context)
    msg = await reset_order(context)
    assert session.lynch_order(session.get(context.chat_data)) is None
    assert "The lynchorder was reset by" in msg.last_reply


# --- Who may change it -----------------------------------------------------------


async def test_a_player_may_set_it(context):
    await start_session(context)
    msg = await set_order(context, "omu first", user_id=2, name="omu")
    assert "The lynchorder was set by" in msg.last_reply


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
    assert "The lynchorder was set by" in msg.last_reply


async def test_a_passer_by_may_still_read_it(context):
    """Reading is harmless, and refusing it would be unhelpful to a spectator."""
    await start_session(context)
    msg = await show(context, user_id=777, name="Passer By")
    assert "Lynchorder" in msg.last_reply


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
    assert "<b>Lynchorder:</b>" in msg.last_reply


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


# --- Naming players: @handle, a tapped mention, or an id ---------------------------


async def test_tapped_mentions_become_the_order(context):
    """The order is the players named, in the order named, rendered as mentions."""
    await start_session(context)
    msg = await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu"), (3, "J J"), (1, "Ren")]))

    assert session.lynch_order(session.get(context.chat_data)) == [2, 3, 1]
    assert msg.last_reply == (
        "The lynchorder was set by <a href='tg://user?id=1'>Ren</a>\n"
        "<b>Lynchorder</b> <i>(set)</i>:\n"
        "<a href='tg://user?id=2'>omu</a>\n"
        "<a href='tg://user?id=3'>J J</a>\n"
        "<a href='tg://user?id=1'>Ren</a>\n"
        "<a href='tg://user?id=2'>omu</a>\n"
    )


async def test_a_named_order_closes_the_cycle(context):
    """Same mechanic as the rotating one: the first player is repeated at the bottom, so
    everybody lynches the name below them and each receives exactly one vote."""
    await start_session(context)
    await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu"), (1, "Ren")]))
    assert (await show(context)).last_reply.endswith("<a href='tg://user?id=2'>omu</a>\n")


async def test_a_single_player_is_not_told_to_lynch_themselves(context):
    """Which is also what "/slo @somebody" means on its own: one target, one mention."""
    await start_session(context)
    msg = await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu")]))

    assert session.lynch_order(session.get(context.chat_data)) == [2]
    assert msg.last_reply.count("tg://user?id=2") == 1


async def test_a_bare_user_id_names_a_player(context):
    """The one typed form that cannot be misread, and it is checked against the roster."""
    await start_session(context)
    msg = await run(context, pointing("/slo@wwstatsbot 3 2"))

    assert session.lynch_order(session.get(context.chat_data)) == [3, 2]
    assert "<a href='tg://user?id=3'>J J</a>" in msg.last_reply


async def test_an_at_handle_names_a_player_the_roster_taught_us(context):
    """A plain @handle carries no id. The roster's own mentions taught us the mapping."""
    current = await start_session(context)
    session.set_username(current, 2, "omu_plays")

    msg = await run(context, handle_mention("/slo@wwstatsbot", ["@omu_plays"]))
    assert session.lynch_order(session.get(context.chat_data)) == [2]
    assert "<a href='tg://user?id=2'>omu</a>" in msg.last_reply


async def test_naming_somebody_twice_counts_once(context):
    """Two votes to one player is exactly what the cycle exists to prevent."""
    await start_session(context)
    await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu"), (2, "omu"), (1, "Ren")]))
    assert session.lynch_order(session.get(context.chat_data)) == [2, 1]


async def test_a_named_order_follows_a_rename(context):
    """Ids are stored, names resolved at render time, so a stale label is impossible."""
    await start_session(context)
    await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu"), (1, "Ren")]))
    session.player(session.get(context.chat_data), 2)["name"] = "omu the second"

    assert "omu the second" in (await show(context)).last_reply


async def test_a_named_player_who_dies_later_drops_out(context):
    """Unlike free text, a named order is a list of players — and a corpse in it would be
    an instruction pointing at one."""
    await start_session(context)
    await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu"), (3, "J J"), (1, "Ren")]))
    session.set_alive(session.get(context.chat_data), 3, False)

    reply = (await show(context)).last_reply
    assert "J J" not in reply
    assert "omu" in reply and "Ren" in reply


async def test_naming_a_dead_player_says_who_was_left_out(context):
    """Silently one name short of what somebody typed is worse than being told why."""
    await start_session(context)
    session.set_alive(session.get(context.chat_data), 3, False)
    msg = await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu"), (3, "J J"), (1, "Ren")]))

    assert session.lynch_order(session.get(context.chat_data)) == [2, 1]
    assert "already dead" in msg.last_reply
    assert "J J" in msg.last_reply


async def test_naming_only_dead_players_is_refused(context):
    await start_session(context)
    session.set_alive(session.get(context.chat_data), 3, False)
    msg = await run(context, pointing("/slo@wwstatsbot", mentions=[(3, "J J")]))

    assert session.lynch_order(session.get(context.chat_data)) is None
    assert "already dead" in msg.last_reply


async def test_a_mention_of_somebody_outside_the_roster_is_questioned_not_obeyed(context):
    """The trap this guards: _pointed_at cuts every mention out of the text whether or not
    it resolved, so a mistyped @handle arrives looking exactly like a bare /slo — which
    would *reset* the order instead of asking what was meant."""
    await start_session(context)
    await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu"), (1, "Ren")]))
    msg = await run(context, handle_mention("/slo@wwstatsbot", ["@nobody_here"]))

    assert session.lynch_order(session.get(context.chat_data)) == [2, 1], "the order stands"
    assert "don't know who that is" in msg.last_reply


async def test_free_text_still_works_alongside(context):
    """Anything nobody could resolve to players is still stored and printed verbatim."""
    await start_session(context)
    msg = await run(context, pointing("/slo@wwstatsbot whoever shouts loudest"))

    assert session.lynch_order(session.get(context.chat_data)) == "whoever shouts loudest"
    assert "whoever shouts loudest" in msg.last_reply


async def test_players_win_over_leftover_text(context):
    """ "/slo @omu then @ren" is an order of two players, not a sentence about them."""
    await start_session(context)
    msg = await run(context, pointing("/slo@wwstatsbot then", mentions=[(2, "omu"), (1, "Ren")]))

    assert session.lynch_order(session.get(context.chat_data)) == [2, 1]
    assert "then" not in msg.last_reply


async def test_a_named_order_survives_the_persistence_roundtrip(context):
    """A list of ints has to come back as a list of ints, not as strings."""
    from conftest import assert_json_roundtrips

    await start_session(context)
    await run(context, pointing("/slo@wwstatsbot", mentions=[(2, "omu"), (1, "Ren")]))
    restored = assert_json_roundtrips(context.chat_data)

    assert session.lynch_order(session.get(restored)) == [2, 1]
    assert "<a href='tg://user?id=2'>omu</a>" in (await show(FakeContextWith(restored, context))).last_reply


class FakeContextWith:
    """The same context with restored chat_data, so the render path is exercised on what
    came back out of JSON rather than on what went in."""

    def __init__(self, chat_data, original):
        self.chat_data = chat_data
        self.bot = original.bot
        self.args = []
        self.job_queue = original.job_queue
        self.bot_data = original.bot_data
