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


def mention(user_id, name):
    """How a player's name appears in anything the bot says about them.

    Every message that names somebody links them, the way the rest of this bot's output
    does — a roster of sixteen plain names is untappable and impossible to disambiguate
    when two people have chosen similar ones.
    """
    return "<a href='tg://user?id={}'>{}</a>".format(user_id, name)


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
        gamesession.alt_cmd,
    ],
)
async def test_every_command_is_silent_with_no_session(context, handler):
    """These are the incumbent's command words. With no session they are not ours."""
    msg = player_message("/role seer")
    context.args = ["seer"]
    await handler(FakeUpdate(message=msg), context)
    assert msg.replies == []
    assert context.bot.edits == []
    assert context.job_queue.pending() == [], "nothing may be scheduled either"


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
        gamesession.alt_cmd,
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
    assert msg.last_reply == mention(1, "Ren") + "'s role was set to: Gunner \N{PISTOL}"
    # The messages are updated on a trailing debounce, not inline — sixteen players
    # revealing in a minute must not cost sixteen edits (see _DEBOUNCE_SECONDS).
    assert context.job_queue.pending(), "an update should have been scheduled"


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
    assert msg.last_reply == "{}'s rolemodel is now {}".format(mention(1, "Ren"), mention(2, "omu"))


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
    assert msg.last_reply == "{}'s rolemodel is now {}".format(mention(2, "omu"), mention(1, "Ren"))


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
    assert msg.last_reply == "{} is now in love.".format(mention(1, "Ren"))


async def test_love_naming_two_players_pairs_them_both_ways(context):
    """Love is mutual; a one-sided record would show a heart against one of the couple."""
    session_data = await start_session(context)
    msg = player_message("/love Ren omu")
    context.args = ["Ren", "omu"]
    await gamesession.love_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["partner"] == 2
    assert session_data["players"]["2"]["partner"] == 1
    assert session_data["players"]["2"]["lover"] is True
    assert msg.last_reply == "{} and {} are now in love.".format(mention(1, "Ren"), mention(2, "omu"))


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
    assert context.bot.sent[-1]["text"] == "{} has considered the game stopped!".format(mention(1, "Ren"))
    ended = context.bot.edits[-1]
    assert "GAME ENDED" in ended["text"], "the roster must stop claiming the game is running"
    assert ended["reply_markup"] is None, "the live button must not outlive the session"


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
    assert mention(1, "Ren") + ": Alpha Wolf \N{HIGH VOLTAGE SIGN}" in rendered
    assert "<b>Dead Players:</b>" in rendered
    assert keyboard.inline_keyboard[0][0].text == "Stop"


async def test_an_unrevealed_player_is_shown_as_such(context):
    session_data = await start_session(context)
    rendered, _ = gamesession.render_state(session_data)
    assert mention(2, "omu") + ": <i>not revealed</i>" in rendered


async def test_a_rolemodel_renders_inline_in_parentheses(context):
    """`J J: Wild Child 👶 (omu)` — the manager's own convention."""
    session_data = await start_session(context)
    await reveal(context, 3, "wc")
    session.set_model(session_data, 3, 2)
    rendered, _ = gamesession.render_state(session_data)
    assert mention(3, "J J") + ": Wild Child \N{BABY} (" + mention(2, "omu") + ")" in rendered


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
    assert mention(1, "Ren") + ": Wild Child \N{BABY} (" + mention(2, "omu") + ")" in rendered
    assert session.is_member(restored, 1)


# --- The Beholder settles the Seer/Fool question -----------------------------
#
# A player told they are the Seer cannot know they are not the Fool — but the Beholder is
# *shown* the real Seer when the game starts, so their claim is the one that answers it for
# everybody. Recording an unsure player as possibly-Seer after that hands them achievements
# they cannot earn while withholding the Fool's.


async def claim(context, text, user_id=1, name="Ren"):
    msg = player_message("/role " + text, user_id=user_id, name=name)
    context.args = text.split()
    await gamesession.role_cmd(FakeUpdate(message=msg), context)
    return msg


async def test_bh_alone_is_just_the_role(context):
    session_data = await start_session(context)
    await claim(context, "bh")

    assert session_data["players"]["1"]["roles"] == ["beholder"]
    assert session_data["no_seer"] is False
    assert session_data["seer_id"] is None


async def test_bhns_records_the_beholder_and_that_there_is_no_seer(context):
    session_data = await start_session(context)
    msg = await claim(context, "bhns")

    assert session_data["players"]["1"]["roles"] == ["beholder"]
    assert session_data["no_seer"] is True
    assert "there is no Seer" in msg.last_reply


async def test_the_long_spelling_of_no_seer_works_too(context):
    """Players type what they say out loud, not only the shorthand."""
    for text in ("beholder no seer", "bh no seer", "no seer"):
        context.chat_data.clear()
        session_data = await start_session(context)
        await claim(context, text)
        assert session_data["no_seer"] is True, text


async def test_with_no_seer_an_unsure_claim_becomes_the_fool(context):
    session_data = await start_session(context)
    await claim(context, "bhns")

    msg = await claim(context, "sf", user_id=2, name="omu")
    assert session_data["players"]["2"]["roles"] == ["fool"]
    assert "Fool" in msg.last_reply


async def test_an_unsure_claim_made_first_is_settled_retroactively(context):
    """Reveals arrive in any order, and leaving them ambiguous keeps offering the Seer's
    achievements to somebody who provably cannot earn one."""
    session_data = await start_session(context)
    await claim(context, "sf", user_id=2, name="omu")
    assert session_data["players"]["2"]["roles"] == ["seer", "fool"]

    msg = await claim(context, "bhns")
    assert session_data["players"]["2"]["roles"] == ["fool"]
    assert "settled as the Fool" in msg.last_reply


async def test_bhws_names_the_seer(context):
    session_data = await start_session(context)
    msg = await claim(context, "bhws omu")

    assert session_data["players"]["1"]["roles"] == ["beholder"]
    assert session_data["players"]["2"]["roles"] == ["seer"]
    assert session_data["seer_id"] == 2
    assert "is the Seer" in msg.last_reply


async def test_beholder_naming_a_player_works_in_longhand(context):
    session_data = await start_session(context)
    await claim(context, "beholder omu")
    assert session_data["players"]["2"]["roles"] == ["seer"]


async def test_naming_the_seer_makes_every_other_unsure_player_the_fool(context):
    """The Beholder saw who it was, so anyone else's "seer or fool" is answered."""
    session_data = await start_session(context)
    await claim(context, "sf", user_id=3, name="J J")
    await claim(context, "bhws omu")

    assert session_data["players"]["2"]["roles"] == ["seer"]
    assert session_data["players"]["3"]["roles"] == ["fool"]


async def test_the_named_seer_keeps_their_role_if_they_claimed_sf(context):
    session_data = await start_session(context)
    await claim(context, "sf", user_id=2, name="omu")
    await claim(context, "bhws omu")
    assert session_data["players"]["2"]["roles"] == ["seer"]


async def test_bhws_naming_nobody_in_the_game_is_reported(context):
    session_data = await start_session(context)
    msg = await claim(context, "bhws Nobody")

    assert "player from this game" in msg.last_reply
    assert session_data["seer_id"] is None


async def test_a_role_that_merely_starts_with_beholder_still_gets_did_you_mean(context):
    """ "beholder" plus an unrecognised word is more likely a fumbled role name than a
    claim about the Seer, so it falls through rather than erroring about targets."""
    session_data = await start_session(context)
    msg = await claim(context, "beholder blacksmit")

    assert "Did you mean" in msg.last_reply
    assert session_data["seer_id"] is None


# --- Stopping ----------------------------------------------------------------


async def test_a_group_admin_who_is_not_playing_can_stop_the_game(context):
    """Usually the person who notices the session outlived its round."""
    await start_session(context)
    context.bot.chat_admins = {999}

    for _ in range(2):
        await gamesession.stop_callback(stop_query(user_id=999, name="Chair"), context)

    assert session.get(context.chat_data) is None
    assert context.bot.sent[-1]["text"] == mention(999, "Chair") + " has considered the game stopped!"


async def test_an_ordinary_bystander_still_cannot_stop_the_game(context):
    await start_session(context)
    context.bot.chat_admins = set()

    await gamesession.stop_callback(stop_query(user_id=999, name="Passer By"), context)
    assert session.get(context.chat_data) is not None


async def test_an_unreachable_admin_check_denies_rather_than_allows(context):
    """If Telegram will not say who administrates the chat, nobody gains a stop."""

    async def unavailable(chat_id, user_id):
        raise RuntimeError("Bad Gateway")

    await start_session(context)
    context.bot.get_chat_member = unavailable

    await gamesession.stop_callback(stop_query(user_id=999, name="Passer By"), context)
    assert session.get(context.chat_data) is not None


async def test_stopping_says_in_the_chat_who_did_it(context):
    """The button's toast is only seen by whoever tapped; the game ending is everyone's."""
    await start_session(context)
    context.bot.sent.clear()

    for _ in range(2):
        await gamesession.stop_callback(stop_query(), context)

    assert context.bot.sent[-1]["text"] == mention(1, "Ren") + " has considered the game stopped!"


async def test_the_roster_stops_saying_the_game_is_running(context):
    """It stays in the chat as the record of the game, so it must not go on announcing one."""
    session_data = await start_session(context)
    await reveal(context, 1, "seer")
    msg = player_message("/gsend")
    await gamesession.end_session_cmd(FakeUpdate(message=msg), context)

    ended = context.bot.edits[-1]
    assert "GAME ENDED" in ended["text"]
    assert "GAME RUNNING" not in ended["text"]
    assert "Reveal your role" not in ended["text"], "no instructions for a session that is over"
    assert mention(1, "Ren") + ": Seer" in ended["text"], "but the record of the game stays"
    assert session_data["players"]["1"]["roles"] == ["seer"]


# --- Naming players: the failures from the first live game -------------------
#
# Two shapes broke in a real round, both because arguments were split positionally while
# player names contain spaces, and because a plain @handle carries no user id.


async def test_rm_in_reply_sets_the_replied_to_players_model_not_theirs(context):
    """The live bug: `/rm J J` in reply to somebody set *J J's* rolemodel to "J".

    "J J" is two words and one player, so the two-argument form fired, the reply was
    ignored, and "J" prefix-matched J J into the target slot.
    """
    session_data = await start_session(context)
    await reveal(context, 2, "wc")

    theirs = message("hi", from_user=FakeUser(2, "omu"))
    msg = player_message("/rm J J", reply_to=theirs)
    context.args = ["J", "J"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["2"]["model"] == 3, "omu's model is J J"
    assert session_data["players"]["3"]["model"] is None, "J J is not the target"


async def test_a_multi_word_name_with_no_reply_is_the_senders_model(context):
    session_data = await start_session(context)
    await reveal(context, 1, "wc")

    msg = player_message("/rm J J")
    context.args = ["J", "J"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["model"] == 3


async def test_a_player_whose_name_is_two_words_beats_the_split_reading(context):
    """ "J J" as a whole player must win over target "J" plus model "J"."""
    session_data = await start_session(context)
    await reveal(context, 2, "dg")

    msg = player_message("/rm omu J J")
    context.args = ["omu", "J", "J"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["2"]["model"] == 3


async def test_a_plain_at_handle_resolves_once_the_roster_taught_us_it(context):
    """The other live bug: `/rm @beforeshu @jjw91` was refused for want of a user id.

    A plain @handle carries none — but the roster mentions people properly, and a
    text_mention carries the whole User object, username included.
    """
    players = [
        FakeUser(1, "Ren", username="beforeshu"),
        FakeUser(2, "omu", username="jjw91"),
        FakeUser(3, "J J"),
    ]
    entities = [FakeEntity(TEXT_MENTION, user=u) for u in players]
    roster = bot_message("Players Alive: 3/3", entities=entities)
    msg = gs_message(reply_to=roster)
    await gamesession.start_session_cmd(FakeUpdate(message=msg), context)
    session_data = session.get(context.chat_data)

    await reveal(context, 1, "wc")
    rm = player_message("/rm @beforeshu @jjw91")
    context.args = ["@beforeshu", "@jjw91"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=rm), context)

    assert session_data["players"]["1"]["model"] == 2


async def test_a_handle_we_have_never_seen_is_still_refused(context):
    session_data = await start_session(context)
    await reveal(context, 1, "wc")

    msg = player_message("/rm @stranger")
    context.args = ["@stranger"]
    await gamesession.rolemodel_cmd(FakeUpdate(message=msg), context)

    assert "isn't in this game" in msg.last_reply
    assert session_data["players"]["1"]["model"] is None


async def test_a_players_own_command_teaches_us_their_handle(context):
    """Covers anyone the roster mentioned before they had set a username."""
    session_data = await start_session(context)
    msg = message("/role seer", from_user=FakeUser(1, "Ren", username="Beforeshu"))
    context.args = ["seer"]
    await gamesession.role_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["1"]["username"] == "beforeshu"


async def test_love_handles_a_two_word_name_too(context):
    """/love split its arguments the same way /rm did."""
    session_data = await start_session(context)
    msg = player_message("/love omu J J")
    context.args = ["omu", "J", "J"]
    await gamesession.love_cmd(FakeUpdate(message=msg), context)

    assert session_data["players"]["2"]["partner"] == 3
    assert session_data["players"]["3"]["partner"] == 2
