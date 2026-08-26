"""The Possible Achievements post, the debounce that publishes it, and idle expiry.

Three properties carry this file.

**The post has to stay parseable by /info.** It is deliberately the same shape the game's
own achievement manager posts, so replying to it returns the cards. That contract is
asserted by feeding the rendered post straight through the real parser rather than by
eyeballing the format.

**The debounce has to coalesce.** Sixteen players revealing inside a minute is ordinary,
and an edit per reveal is how a bot meets Telegram's rate limiter. The test asserts one
edit for a burst, which is only meaningful because the fake JobQueue implements
get_jobs_by_name — the thing the real code checks to decide a publish is already pending.

**The list has to fit.** Telegram rejects a message over 4096 characters, and a full game
is well past that; a rejected edit would freeze the list at whatever it last said. The
renderer degrades in steps instead, and the test drives a 16-player game to prove it.
"""

import pytest
from conftest import FakeUpdate
from test_standin_session import player_message, reveal, start_session

import db
import feasibility
import rulelist
import session
from handlers import achievements as achv_handlers
from handlers import gamesession
from rulelist import RULES


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    """The real catalogue, without a database. db.get_rules() is the live read path."""
    catalogue = {rule["name"]: rule for rule in RULES}
    monkeypatch.setattr(db, "get_rules", lambda: catalogue)
    return catalogue


async def publish(context):
    """Fire whatever the debounce scheduled, as the scheduler would."""
    await context.job_queue.run_pending(context)


# --- The post ---------------------------------------------------------------


async def test_the_post_lists_an_achievement_under_the_player_who_can_earn_it(context):
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")

    rendered = gamesession.render_list(session_data)

    assert "Possible Achievements:" in rendered
    assert "Cold as Ice" in rendered
    ren, _, rest = rendered.partition("omu (")
    assert "Cold as Ice" in ren, "belongs to the Snow Wolf, not the harlot"
    assert "Cold as Ice" not in rest


async def test_the_post_is_parseable_by_info(context):
    """The contract with handlers/achievements.py: reply with /info and get the cards.

    Fed through the real parser rather than checked by eye, because the format is only
    useful if that function agrees with it — markers and all.
    """
    session_data = await start_session(context)
    await reveal(context, 1, "alpha_wolf")
    await reveal(context, 2, "drunk")

    rendered = gamesession.render_list(session_data)
    names = achv_handlers._extract_possible_achievements(rendered)

    assert "Lucky Day" in names, "an ordinary row"
    assert all(not name.startswith(("\N{BLACK QUESTION MARK ORNAMENT}", "-")) for name in names), names


async def test_every_extracted_name_matches_a_real_achievement(context):
    """A row /info cannot resolve is worse than no row: the fuzzy fallback answers with
    a different achievement and nothing says so."""
    session_data = await start_session(context)
    await reveal(context, 1, "alpha_wolf")
    await reveal(context, 2, "wolf_cub")
    await reveal(context, 3, "cursed")

    names = achv_handlers._extract_possible_achievements(gamesession.render_list(session_data))
    catalogue = {rule["name"] for rule in RULES}
    for name in names:
        assert name in catalogue, name


async def test_uncertain_rows_are_marked(context):
    session_data = await start_session(context)
    await reveal(context, 1, "tanner")
    rendered = gamesession.render_list(session_data)
    # Masochist is a MAYBE — the Tanner still has to win.
    assert " - \N{BLACK QUESTION MARK ORNAMENT} Masochist" in rendered


async def test_a_swing_reachable_row_is_marked_differently(context):
    """A Cursed player's wolf achievements are real but conditional, and reading them as
    available now would be a different promise."""
    session_data = await start_session(context)
    await reveal(context, 1, "cursed")
    await reveal(context, 2, "sorcerer")
    await reveal(context, 3, "werewolf")

    rendered = gamesession.render_list(session_data)
    ren = rendered.split("\n\n")[1]
    assert "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS} No Sorcery!" in ren


async def test_roleless_achievements_are_named_once_with_everyone_who_can_get_them(context):
    """The manager's own shape: a section at the bottom, not a row under each player.

    Sixteen copies of one roleless achievement says the same thing sixteen times and
    crowds out the rows that are about somebody in particular.
    """
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "seer")

    rendered = gamesession.render_list(session_data)
    assert rendered.count("Welcome to Hell") == 1
    assert "Welcome to Hell (4):" in rendered, "named once, with a count"
    assert "Ren, omu, J J" in rendered, "and the players who can still get it"


async def test_an_unrevealed_player_is_left_out(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    rendered = gamesession.render_list(session_data)
    assert "omu (" not in rendered


async def test_a_dead_player_is_left_out(context):
    """The list answers "what is still possible", and nothing is, for them."""
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "seer")
    session.set_alive(session_data, 1, False)

    rendered = gamesession.render_list(session_data)
    assert "Ren (" not in rendered


async def test_a_dead_player_also_stops_gating_other_peoples_achievements(context):
    """Cold as Ice needs a harlot to freeze. Once the harlot is dead, it is not possible."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")
    assert "Cold as Ice" in gamesession.render_list(session_data)

    session.set_alive(session_data, 2, False)
    assert "Cold as Ice" not in gamesession.render_list(session_data)


async def test_the_post_says_how_far_along_the_reveal_is(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    assert "1 of 4 revealed" in gamesession.render_list(session_data)


async def test_before_anyone_reveals_only_the_roleless_sections_show(context):
    """Nothing role-gated can be judged yet, but "play a game" is already true."""
    session_data = await start_session(context)
    rendered = gamesession.render_list(session_data)

    assert "Welcome to Hell (4):" in rendered
    assert "0 of 4 revealed" in rendered


# --- Fitting in one message -------------------------------------------------


async def big_game(context):
    """Sixteen players, all revealed, in the roles that produce the longest lists."""
    roster = [(i, "Player{}".format(i)) for i in range(1, 17)]
    session_data = await start_session(context, players=roster)
    loud = [
        "alpha_wolf",
        "wolf_cub",
        "serial_killer",
        "arsonist",
        "hunter",
        "gunner",
        "guardian_angel",
        "chemist",
        "harlot",
        "cupid",
        "tanner",
        "cultist",
        "cultist_hunter",
        "grave_digger",
        "barkeep",
        "doppelganger",
    ]
    for (uid, _), role_name in zip(roster, loud, strict=True):
        await reveal(context, uid, role_name)
    return session_data


async def test_a_full_game_still_fits_in_one_message(context):
    """Telegram rejects anything over 4096, and a rejected edit freezes the list."""
    session_data = await big_game(context)
    rendered = gamesession.render_list(session_data)
    assert len(rendered) <= 4096, len(rendered)


async def test_trimming_drops_rows_not_players(context):
    """When the list will not fit, rows go and players stay.

    (Every role in this fixture has something available, so anyone missing from the output
    was dropped by the trimming rather than having nothing to show. A player who is simply
    out of luck is absent either way — the roster message is where "who has revealed" is
    answered, and this one only ever answers "what is still possible".)"""
    session_data = await big_game(context)
    rendered = gamesession.render_list(session_data)
    for _uid, entry in session.players_in_order(session_data):
        assert entry["name"] in rendered, entry["name"]


async def test_trimming_says_that_it_trimmed(context):
    """Silently truncating would read as "this is everything"."""
    session_data = await big_game(context)
    rendered = gamesession.render_list(session_data)
    assert "more</i>" in rendered or "Trimmed to fit" in rendered


# --- The debounce -----------------------------------------------------------


async def test_a_burst_of_reveals_costs_one_publish(context):
    """Sixteen reveals in three seconds must not be sixteen edits."""
    await start_session(context)
    context.bot.sent.clear()

    for uid in (1, 2, 3):
        await reveal(context, uid, "villager")

    publish_jobs = context.job_queue.pending(gamesession._PUBLISH_JOB.format(-100))
    assert len(publish_jobs) == 1, "the second and third reveals must not schedule again"

    await publish(context)
    assert len(context.bot.sent) == 1, "one post, not three"


async def test_the_publish_posts_the_list_then_edits_it(context):
    session_data = await start_session(context)
    context.bot.sent.clear()

    await reveal(context, 1, "villager")
    await publish(context)
    assert len(context.bot.sent) == 1
    assert session_data["list_message_id"] is not None

    await reveal(context, 2, "seer")
    await publish(context)
    assert len(context.bot.sent) == 1, "the second publish edits rather than reposting"
    assert any(e["message_id"] == session_data["list_message_id"] for e in context.bot.edits)


async def test_the_publish_also_brings_the_roster_up_to_date(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await publish(context)
    assert any(e["message_id"] == session_data["state_message_id"] for e in context.bot.edits)


async def test_a_publish_for_an_ended_session_does_nothing(context):
    """The session can end between the schedule and the fire."""
    await start_session(context)
    await reveal(context, 1, "villager")
    session.end(context.chat_data)
    context.bot.sent.clear()

    await publish(context)
    assert context.bot.sent == []


async def test_an_identical_edit_is_swallowed(context):
    """A reveal that unlocks nothing new produces "message is not modified"."""
    from telegram.error import BadRequest

    await start_session(context)
    await reveal(context, 1, "villager")
    await publish(context)

    context.bot._edit_error = BadRequest("Message is not modified")
    await reveal(context, 2, "villager")
    await publish(context)  # must not raise


async def test_everything_still_works_without_a_job_queue(context):
    """A bot built without the job-queue extra must degrade, not crash."""
    context.job_queue = None
    await start_session(context)
    msg = player_message("/role seer")
    context.args = ["seer"]
    await gamesession.role_cmd(FakeUpdate(message=msg), context)
    assert "role was set to" in msg.last_reply


# --- /la --------------------------------------------------------------------


async def test_la_points_at_the_live_list_rather_than_reposting_it(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await publish(context)
    context.bot.sent.clear()

    msg = player_message("/la")
    context.args = []
    await gamesession.list_achievements_cmd(FakeUpdate(message=msg), context)

    assert len(context.bot.sent) == 1
    sent = context.bot.sent[0]
    assert sent["reply_parameters"].message_id == session_data["list_message_id"]
    assert "Possible Achievements" not in sent["text"], "a pointer, not a second copy"


async def test_la_before_anyone_reveals_says_so(context):
    await start_session(context)
    msg = player_message("/la")
    context.args = []
    await gamesession.list_achievements_cmd(FakeUpdate(message=msg), context)
    assert "Nobody has revealed" in msg.last_reply


async def test_la_is_silent_without_a_session(context):
    msg = player_message("/la")
    context.args = []
    await gamesession.list_achievements_cmd(FakeUpdate(message=msg), context)
    assert msg.replies == []


# --- Idle expiry ------------------------------------------------------------


async def test_a_session_starts_its_idle_clock_immediately(context):
    """One nobody ever touches still has to expire."""
    await start_session(context)
    assert context.job_queue.pending(gamesession._IDLE_JOB.format(-100))


async def test_activity_pushes_the_idle_clock_back(context):
    await start_session(context)
    first = context.job_queue.pending(gamesession._IDLE_JOB.format(-100))[0]

    await reveal(context, 1, "villager")

    assert first.removed, "the old countdown must be cancelled, not left to fire"
    assert context.job_queue.pending(gamesession._IDLE_JOB.format(-100))


async def test_the_warning_fires_before_the_session_ends(context):
    await start_session(context)
    context.bot.sent.clear()

    await gamesession._idle_warning(_job_context(context))

    assert "end the stand-in session" in context.bot.sent[0]["text"]
    assert session.get(context.chat_data) is not None, "warned, not ended"


async def test_the_session_ends_after_the_grace_period(context):
    await start_session(context)
    await gamesession._idle_warning(_job_context(context))
    await gamesession._idle_end(_job_context(context))

    assert session.get(context.chat_data) is None
    ended = context.bot.edits[-1]
    assert "GAME ENDED" in ended["text"]
    assert ended["reply_markup"] is None, "the live button must not outlive the session"


async def test_expiry_of_an_already_ended_session_says_nothing(context):
    await start_session(context)
    session.end(context.chat_data)
    context.bot.sent.clear()

    await gamesession._idle_end(_job_context(context))
    assert context.bot.sent == []


def _job_context(context, chat_id=-100):
    """A context as the JobQueue would provide it, carrying the job's chat."""
    from conftest import FakeJob

    context.job = FakeJob(None, 0, chat_id=chat_id, name="test")
    return context


# --- The rules the post is built from ---------------------------------------


async def test_the_post_uses_the_database_rules_not_the_seed_list(context, monkeypatch):
    """Rules are editable at runtime, so a /setrule correction must reach the next post."""
    edited = {
        "Cold as Ice": {"tier": rulelist.CHECK, "subject": "snow_wolf", "expr": "False", "note": ""},
    }
    monkeypatch.setattr(db, "get_rules", lambda: edited)

    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")

    assert "Cold as Ice" not in gamesession.render_list(session_data)


async def test_a_broken_rule_does_not_take_the_post_down(context, monkeypatch):
    """One bad expression must cost one row, not the whole list."""
    broken = {
        "Cold as Ice": {"tier": rulelist.CHECK, "subject": "snow_wolf", "expr": "count(", "note": ""},
        "Welcome to Hell": {"tier": rulelist.ALWAYS, "subject": "any", "expr": "True", "note": ""},
    }
    monkeypatch.setattr(db, "get_rules", lambda: broken)

    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")

    rendered = gamesession.render_list(session_data)
    assert "Cold as Ice" not in rendered
    assert "Welcome to Hell" in rendered


async def test_feasibility_sees_only_living_revealed_players(context):
    session_data = await start_session(context)
    await reveal(context, 1, "seer")
    await reveal(context, 2, "beholder")
    session.set_alive(session_data, 2, False)

    revealed = session.revealed_roles(session_data)
    assert 2 not in revealed
    per_player, _ = feasibility.feasible(revealed, db.get_rules())
    names = {entry["name"] for entry in per_player[1]}
    assert "Should Have Known" not in names, "no living beholder to reveal"


# --- Already earned ---------------------------------------------------------
#
# Nobody is hunting an achievement they finished months ago. The attained lists are
# fetched once when the session opens and subtracted from every render — without them the
# post is largely a list of things half the room already has, which is worse than no post:
# it buries the two or three rows that are actually news.


async def test_an_achievement_a_player_already_has_is_not_offered(context):
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")
    assert "Cold as Ice" in gamesession.render_list(session_data)

    session.set_attained(session_data, 1, ["Cold as Ice"])
    assert "Cold as Ice" not in gamesession.render_list(session_data)


async def test_one_players_collection_does_not_hide_it_from_another(context):
    """Two Snow Wolves, one of whom has it: the other must still be told."""
    session_data = await start_session(context, players=[(1, "Ren"), (2, "omu"), (3, "J J")])
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "snow_wolf")
    await reveal(context, 3, "harlot")

    session.set_attained(session_data, 1, ["Cold as Ice"])
    rendered = gamesession.render_list(session_data)

    ren, _, rest = rendered.partition("omu\n")
    assert "Cold as Ice" not in ren
    assert "Cold as Ice" in rest


async def test_a_roleless_achievement_lists_only_the_players_missing_it(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    session.set_attained(session_data, 1, ["Welcome to Hell"])
    session.set_attained(session_data, 2, ["Welcome to Hell"])

    rendered = gamesession.render_list(session_data)
    assert "Welcome to Hell (2):" in rendered
    assert "Ren" not in rendered.split("Welcome to Hell (2):")[1].split("\n")[1]


async def test_an_achievement_everybody_already_has_is_left_out_entirely(context):
    """Printed with an empty list it would read as a row nobody can earn."""
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    for uid in (1, 2, 3, 4):
        session.set_attained(session_data, uid, ["Welcome to Hell"])

    assert "Welcome to Hell" not in gamesession.render_list(session_data)


async def test_an_unknown_collection_shows_everything(context):
    """The stats API is occasionally unavailable. A game played during one of those
    minutes should still get a list — hiding a row nobody can verify is the worse error."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")

    assert session_data["players"]["1"]["attained"] is None
    assert "Cold as Ice" in gamesession.render_list(session_data)


async def test_the_session_fetches_every_players_collection_once(context, monkeypatch):
    """One batch at /gs, not one per render: a publish happens every few seconds."""
    calls = []

    async def fake_get(user_id):
        calls.append(user_id)
        return [{"name": "Welcome to Hell"}]

    monkeypatch.setattr(gamesession.api, "get_achievements", fake_get)
    session_data = await start_session(context)

    assert sorted(calls) == [1, 2, 3, 4]
    assert session_data["players"]["1"]["attained"] == ["Welcome to Hell"]

    await reveal(context, 1, "villager")
    await publish(context)
    assert sorted(calls) == [1, 2, 3, 4], "rendering must not re-query"


async def test_a_failed_lookup_leaves_that_player_unknown_rather_than_empty(context, monkeypatch):
    """One player's failure must not be read as "they have nothing", nor sink the session."""

    async def flaky(user_id):
        if user_id == 2:
            raise RuntimeError("stats API down")
        return [{"name": "Welcome to Hell"}]

    monkeypatch.setattr(gamesession.api, "get_achievements", flaky)
    session_data = await start_session(context)

    assert session_data["players"]["1"]["attained"] == ["Welcome to Hell"]
    assert session_data["players"]["2"]["attained"] is None
    assert session.get(context.chat_data) is not None


async def test_attained_lists_survive_a_persistence_round_trip(context):
    from conftest import assert_json_roundtrips

    session_data = await start_session(context)
    session.set_attained(session_data, 1, ["Cold as Ice"])
    restored = assert_json_roundtrips(session_data)
    assert session.already_has(restored, 1, "Cold as Ice")
