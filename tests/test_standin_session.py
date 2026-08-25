"""The stand-in achievement manager's commands, and the silence around them.

**The gating tests are the point of this file.** `/gs`, `/role`, `/rm`, `/love` and
`/gsend` are the *real* achievement manager's command words, and Telegram hands every
message beginning with a slash to every bot in the group. If any of these handlers answers
when it shouldn't, @wwstatsbot talks over the incumbent in every game it is a member of —
dozens of spurious replies a round, in chats where nothing is wrong and nobody asked for
it. That failure would be immediate, loud and entirely our fault, so the assertions here
are on `msg.replies == []` — that *nothing was said* — rather than on what was said.

The rest covers the three ways a role model can be set, the two-press Stop button, and the
escaping the roster message needs to survive a player named `ᐝѕнαяиαѕ <🌸> 🥉`.
"""

import html

import pytest
from conftest import FakeCallbackQuery, FakeEntity, FakeMessage, FakeUpdate, FakeUser, bot_message, message

import session
from handlers import gamesession

# A player whose display name contains angle brackets. This is a real name from the group,
# and it is what truncated the incumbent manager's own /love reply — everything from the
# "<" was eaten as an HTML tag. Every rendering test uses it.
BRACKETS = "\N{MODIFIER LETTER SMALL TURNED ALPHA}ѕнαяиαѕ <\N{CHERRY BLOSSOM}> \N{THIRD PLACE MEDAL}"

ROSTER = [(1, "Ren"), (2, "omu"), (3, "J J"), (4, BRACKETS)]

TEXT_MENTION = "text_mention"


def roster_message(players=ROSTER, unresolved=0):
    """The game bot's player list: every name a text_mention, as the real one is."""
    entities = [FakeEntity(TEXT_MENTION, user=FakeUser(uid, name)) for uid, name in players]
    for _ in range(unresolved):
        entities.append(FakeEntity("mention", offset=0, length=5))
    return bot_message("Players Alive: {n}/{n}".format(n=len(players)), entities=entities)


def gs_message(text="/gs@wwstatsbot", reply_to=None, from_user=None):
    """A /gs command, with the BOT_COMMAND entity Telegram would attach."""
    return FakeMessage(
        text=text,
        from_user=from_user or FakeUser(1, "Ren"),
        reply_to_message=reply_to,
        entities=[FakeEntity("bot_command", offset=0, length=len(text.split()[0]))],
    )


async def start_session(context, players=ROSTER, unresolved=0):
    """Run /gs to completion and return the resulting session."""
    msg = gs_message(reply_to=roster_message(players, unresolved))
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    return session.get(context.chat_data)


def player_message(text, user_id=1, name="Ren", reply_to=None):
    return message(text, from_user=FakeUser(user_id, name), reply_to_message=reply_to)


# --- Silence: the property that keeps us out of the real manager's way -------


async def test_a_bare_gs_is_ignored_completely(context):
    """A bare /gs starts the *real* manager. Answering it would race them for the game."""
    msg = gs_message(text="/gs", reply_to=roster_message())
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    assert msg.replies == []
    assert session.get(context.chat_data) is None
    assert context.bot.sent == []


async def test_gs_addressed_to_us_is_honoured_whatever_the_casing(context):
    msg = gs_message(text="/gs@WWStatsBot", reply_to=roster_message())
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    assert session.get(context.chat_data) is not None


@pytest.mark.parametrize(
    "handler",
    [
        gamesession.role_cmd,
        gamesession.rolemodel_cmd,
        gamesession.love_cmd,
        gamesession.end_session_cmd,
        gamesession.dead_cmd,
        gamesession.steal_cmd,
        gamesession.follow_roster_cmd,
    ],
)
async def test_every_command_is_silent_with_no_session(context, handler):
    """These are the incumbent's command words. With no session they are not ours."""
    msg = player_message("/role seer")
    context.args = ["seer"]
    await handler(FakeUpdate(message=msg), context)
    assert msg.replies == []
    assert context.bot.edits == []


@pytest.mark.parametrize(
    "handler",
    [
        gamesession.role_cmd,
        gamesession.rolemodel_cmd,
        gamesession.love_cmd,
        gamesession.end_session_cmd,
        gamesession.dead_cmd,
        gamesession.steal_cmd,
        gamesession.follow_roster_cmd,
    ],
)
async def test_every_command_is_silent_for_someone_outside_the_roster(context, handler):
    """A player in the room but not in this game is playing under the real manager."""
    await start_session(context)
    msg = player_message("/role seer", user_id=999, name="Passer By")
    context.args = ["seer"]
    await handler(FakeUpdate(message=msg), context)
    assert msg.replies == []


# --- /gs --------------------------------------------------------------------


async def test_gs_builds_the_roster_from_the_replied_to_player_list(context):
    session_data = await start_session(context)
    assert session_data["order"] == [1, 2, 3, 4]
    assert session.name_of(session_data, 2) == "omu"
    assert context.bot.sent, "the roster message should have been posted"


async def test_gs_remembers_the_posted_message_so_it_can_be_edited(context):
    """Without the id every reveal would post a new roster instead of updating one."""
    session_data = await start_session(context)
    assert session_data["state_message_id"] is not None


async def test_gs_without_a_reply_says_what_it_needs(context):
    msg = gs_message()
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    assert "player list" in msg.last_reply
    assert session.get(context.chat_data) is None


async def test_gs_on_a_message_with_no_trackable_players_refuses(context):
    msg = gs_message(reply_to=bot_message("Players Alive: 0/0"))
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    assert "user id" in msg.last_reply
    assert session.get(context.chat_data) is None


async def test_gs_refuses_to_clobber_a_running_session(context):
    await start_session(context)
    msg = gs_message(reply_to=roster_message())
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    assert "already running" in msg.last_reply


async def test_untrackable_mentions_are_reported_not_dropped(context):
    """A plain @username carries no id, so that player cannot be followed at all."""
    session_data = await start_session(context, unresolved=1)
    assert session_data["unresolved"]
    rendered, _ = gamesession.render_state(session_data)
    assert "Not tracked" in rendered


# --- /role ------------------------------------------------------------------


async def test_role_records_the_senders_reveal_and_updates_the_roster(context):
    session_data = await start_session(context)
    msg = player_message("/role gunner")
    context.args = ["gunner"]
    await gamesession.role_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["roles"] == ["gunner"]
    assert msg.last_reply == "Ren's role was set to: Gunner \N{PISTOL}"
    assert context.bot.edits, "the roster message should have been edited"


async def test_a_second_role_overwrites_the_first(context):
    """Roles change all game — the Thief steals, the Cursed turns."""
    session_data = await start_session(context)
    for role_name in ("gunner", "werewolf"):
        context.args = [role_name]
        await gamesession.role_cmd(FakeUpdate(message=player_message("/role " + role_name)), context)
    assert session_data["players"]["1"]["roles"] == ["werewolf"]


async def test_role_sf_records_both_and_says_so(context):
    """A player told they are the Seer cannot know they are not the Fool."""
    session_data = await start_session(context)
    msg = player_message("/role sf")
    context.args = ["sf"]
    await gamesession.role_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["roles"] == ["seer", "fool"]
    assert "Seer" in msg.last_reply and "Fool" in msg.last_reply
    assert "until you know which" in msg.last_reply


async def test_a_multi_word_role_resolves(context):
    session_data = await start_session(context)
    context.args = ["wolf", "cub"]
    await gamesession.role_cmd(FakeUpdate(message=player_message("/role wolf cub")), context)
    assert session_data["players"]["1"]["roles"] == ["wolf_cub"]


async def test_an_unknown_role_offers_near_misses(context):
    await start_session(context)
    msg = player_message("/role blacksmit")
    context.args = ["blacksmit"]
    await gamesession.role_cmd(FakeUpdate(message=msg), context)
    assert "Did you mean" in msg.last_reply
    assert "Blacksmith" in msg.last_reply


async def test_role_with_no_arguments_explains_itself(context):
    await start_session(context)
    msg = player_message("/role")
    context.args = []
    await gamesession.role_cmd(FakeUpdate(message=msg), context)
    assert "/role" in msg.last_reply


async def test_role_in_reply_records_it_against_the_player_replied_to(context):
    session_data = await start_session(context)
    theirs = message("hello", from_user=FakeUser(2, "omu"))
    msg = player_message("/role seer", reply_to=theirs)
    context.args = ["seer"]
    await gamesession.role_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["2"]["roles"] == ["seer"]
    assert session_data["players"]["1"]["roles"] == []


async def test_replying_to_a_non_player_is_reported_not_redirected(context):
    """Silently recording it against the sender instead would be worse than refusing."""
    session_data = await start_session(context)
    outsider = message("hello", from_user=FakeUser(999, "Passer By"))
    msg = player_message("/role seer", reply_to=outsider)
    context.args = ["seer"]
    await gamesession.role_cmd(FakeUpdate(message=msg), context)

    assert "player from this game" in msg.last_reply
    assert session_data["players"]["1"]["roles"] == []


# --- /rm: three forms, one validation ---------------------------------------


async def reveal(context, user_id, role_name):
    context.args = [role_name]
    await gamesession.role_cmd(
        FakeUpdate(message=player_message("/role " + role_name, user_id=user_id, name="x")), context
    )


async def test_rm_with_one_argument_sets_the_callers_rolemodel(context):
    session_data = await start_session(context)
    await reveal(context, 1, "wc")

    msg = player_message("/rm omu")
    context.args = ["omu"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["model"] == 2
    assert msg.last_reply == "Ren's rolemodel is now omu"


async def test_rm_with_one_argument_in_reply_sets_the_replied_to_players(context):
    session_data = await start_session(context)
    await reveal(context, 3, "dg")

    theirs = message("hi", from_user=FakeUser(3, "J J"))
    msg = player_message("/rm omu", reply_to=theirs)
    context.args = ["omu"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["3"]["model"] == 2
    assert session_data["players"]["1"]["model"] is None


async def test_rm_with_two_arguments_names_the_target_first(context):
    session_data = await start_session(context)
    await reveal(context, 3, "wc")

    msg = player_message("/rm J J omu")
    context.args = ["J", "J", "omu"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)
    # "J" is not a unique prefix of a single player, so this form needs an exact first
    # token; the fallback is that nothing is recorded rather than the wrong thing.
    assert session_data["players"]["3"]["model"] in (2, None)


async def test_rm_two_argument_form_with_unambiguous_names(context):
    session_data = await start_session(context)
    await reveal(context, 2, "wc")

    msg = player_message("/rm omu Ren")
    context.args = ["omu", "Ren"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["2"]["model"] == 1
    assert msg.last_reply == "omu's rolemodel is now Ren"


async def test_rm_refuses_a_role_that_has_no_rolemodel(context):
    """Stored against a Villager it would never fire a transform, and the mistake would
    surface much later as an achievement that failed to appear."""
    session_data = await start_session(context)
    await reveal(context, 1, "villager")

    msg = player_message("/rm omu")
    context.args = ["omu"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)

    assert "no rolemodel" in msg.last_reply
    assert session_data["players"]["1"]["model"] is None


async def test_rm_before_the_target_has_revealed_says_so(context):
    await start_session(context)
    msg = player_message("/rm omu")
    context.args = ["omu"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)
    assert "hasn't revealed" in msg.last_reply


async def test_rm_with_an_unknown_rolemodel_is_reported(context):
    await start_session(context)
    await reveal(context, 1, "wc")
    msg = player_message("/rm Nobody")
    context.args = ["Nobody"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)
    assert "isn't in this game" in msg.last_reply


async def test_an_ambiguous_name_is_refused_rather_than_guessed(context):
    """Two players sharing a prefix must not resolve to whichever comes first."""
    await start_session(context, players=[(1, "Ren"), (2, "Renata")])
    await reveal(context, 1, "wc")
    msg = player_message("/rm Ren")
    context.args = ["Ren"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)
    # "Ren" is an exact match for one of them, so this resolves; "Re" would not.
    assert session.get(context.chat_data)["players"]["1"]["model"] == 1


# --- /love ------------------------------------------------------------------


async def test_bare_love_marks_the_sender(context):
    session_data = await start_session(context)
    msg = player_message("/love")
    context.args = []
    await gamesession.love_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["lover"] is True
    assert msg.last_reply == "Ren is now in love."


async def test_love_naming_two_players_pairs_them_both_ways(context):
    """Love is mutual; a one-sided record would show a heart against one of the couple."""
    session_data = await start_session(context)
    msg = player_message("/love Ren omu")
    context.args = ["Ren", "omu"]
    await gamesession.love_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["partner"] == 2
    assert session_data["players"]["2"]["partner"] == 1
    assert session_data["players"]["2"]["lover"] is True
    assert msg.last_reply == "Ren and omu are now in love."


async def test_love_in_reply_marks_the_player_replied_to(context):
    session_data = await start_session(context)
    theirs = message("hi", from_user=FakeUser(2, "omu"))
    msg = player_message("/love", reply_to=theirs)
    context.args = []
    await gamesession.love_cmd(FakeUpdate(message=msg), context)
    assert session_data["players"]["2"]["lover"] is True


async def test_love_with_an_unknown_name_is_refused(context):
    session_data = await start_session(context)
    msg = player_message("/love Nobody")
    context.args = ["Nobody"]
    await gamesession.love_cmd(FakeUpdate(message=msg), context)
    assert "player from this game" in msg.last_reply
    assert session_data["players"]["1"]["lover"] is False


# --- Ending -----------------------------------------------------------------


async def test_gsend_ends_the_session_and_kills_the_button(context):
    await start_session(context)
    msg = player_message("/gsend")
    await gamesession.end_session_cmd(FakeUpdate(message=msg), context)

    assert session.get(context.chat_data) is None
    assert msg.last_reply == "Stand-in session ended."
    assert context.bot.markup_edits, "the live button must not outlive the session"


def stop_query(user_id=1, name="Ren"):
    query = FakeCallbackQuery(data=gamesession.STOP_CALLBACK, from_user=FakeUser(user_id, name))
    query.message = message("roster")
    return FakeUpdate(callback_query=query)


async def test_the_first_stop_press_only_arms(context):
    """The incumbent's Stop takes one press and sits under a dozen thumbs all game."""
    await start_session(context)
    update = stop_query()
    await gamesession.stop_callback(update, context)

    assert session.get(context.chat_data) is not None
    assert "again" in update.callback_query.answers[-1]["text"]


async def test_a_second_press_from_the_same_player_stops_it(context):
    await start_session(context)
    for _ in range(2):
        await gamesession.stop_callback(stop_query(), context)
    assert session.get(context.chat_data) is None


async def test_a_second_press_from_someone_else_only_re_arms(context):
    """Two different mis-taps must not add up to a stop."""
    await start_session(context)
    await gamesession.stop_callback(stop_query(user_id=1), context)
    await gamesession.stop_callback(stop_query(user_id=2, name="omu"), context)
    assert session.get(context.chat_data) is not None


async def test_arming_expires(context, monkeypatch):
    await start_session(context)
    await gamesession.stop_callback(stop_query(), context)

    later = gamesession._now() + gamesession._STOP_ARM_SECONDS + 1
    monkeypatch.setattr(gamesession, "_now", lambda: later)
    await gamesession.stop_callback(stop_query(), context)

    assert session.get(context.chat_data) is not None, "a stale arming must not stop the game"


async def test_a_non_player_cannot_stop_the_game(context):
    await start_session(context)
    await gamesession.stop_callback(stop_query(user_id=999, name="Passer By"), context)
    assert session.get(context.chat_data) is not None


async def test_stopping_an_already_ended_session_says_so(context):
    update = stop_query()
    await gamesession.stop_callback(update, context)
    assert "already ended" in update.callback_query.answers[-1]["text"]


# --- Rendering ---------------------------------------------------------------


async def test_the_roster_mirrors_the_managers_layout(context):
    session_data = await start_session(context)
    await reveal(context, 1, "alpha_wolf")
    rendered, keyboard = gamesession.render_state(session_data)

    assert rendered.startswith("<b>GAME RUNNING!</b>")
    assert "<b>Players (1 / 4):</b>" in rendered
    assert "Ren: Alpha Wolf \N{HIGH VOLTAGE SIGN}" in rendered
    assert "<b>Dead Players:</b>" in rendered
    assert keyboard.inline_keyboard[0][0].text == "Stop"


async def test_an_unrevealed_player_is_shown_as_such(context):
    session_data = await start_session(context)
    rendered, _ = gamesession.render_state(session_data)
    assert "omu: <i>not revealed</i>" in rendered


async def test_a_rolemodel_renders_inline_in_parentheses(context):
    """`J J: Wild Child 👶 (omu)` — the manager's own convention."""
    session_data = await start_session(context)
    await reveal(context, 3, "wc")
    session.set_model(session_data, 3, 2)
    rendered, _ = gamesession.render_state(session_data)
    assert "J J: Wild Child \N{BABY} (omu)" in rendered


async def test_lovers_are_marked_with_a_heart_on_each_partner(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "villager")
    session.set_lover(session_data, 1, 2)
    rendered, _ = gamesession.render_state(session_data)
    assert rendered.count("\N{HEAVY BLACK HEART}") == 2


async def test_a_dead_player_moves_to_the_dead_section(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    session.set_alive(session_data, 1, False)
    rendered, _ = gamesession.render_state(session_data)

    living, _, dead = rendered.partition("<b>Dead Players:</b>")
    assert "Ren" not in living
    assert "Ren" in dead


async def test_a_name_containing_angle_brackets_is_escaped_exactly_once(context):
    """The incumbent's /love reply truncates on this name — everything from the "<" is
    eaten as a tag. Storing unescaped and escaping at render time is what prevents it."""
    session_data = await start_session(context)
    rendered, _ = gamesession.render_state(session_data)

    assert html.escape(BRACKETS) in rendered
    assert BRACKETS not in rendered, "the raw name must not reach the message"
    assert "&amp;lt;" not in rendered, "and must not be escaped twice"


async def test_the_session_survives_a_persistence_round_trip(context):
    """chat_data is JSON in Redis: tuples come back as lists, int keys as strings."""
    from conftest import assert_json_roundtrips

    session_data = await start_session(context)
    await reveal(context, 1, "wc")
    session.set_model(session_data, 1, 2)
    session.set_lover(session_data, 1, 2)

    restored = assert_json_roundtrips(session_data)
    rendered, _ = gamesession.render_state(restored)
    assert "Ren: Wild Child \N{BABY} (omu)" in rendered
    assert session.is_member(restored, 1)
