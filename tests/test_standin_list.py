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

from html.parser import HTMLParser

import pytest
from conftest import FakeUpdate, FakeUser, message
from test_standin_session import player_message, reveal, start_session

import api
import db
import feasibility
import rulelist
import session
from handlers import achievements as achv_handlers
from handlers import gamesession
from rulelist import RULES


class _Stripped(HTMLParser):
    def handle_data(self, data):
        self.text = getattr(self, "text", "") + data


def visible(rendered):
    """The post as a reader sees it, with the markup taken off.

    Almost everything in this file is about *content* — who is listed under what — and
    every name in the post is a tg:// mention, so asserting on the raw string would mean
    spelling out an <a href> in each of them. Parsed rather than regexed on purpose: the
    renderer measures its own length by stripping tags, and a test that stripped them the
    same way would agree with it whether or not either was right.
    """
    parser = _Stripped(convert_charrefs=True)
    parser.feed(rendered)
    return getattr(parser, "text", "")


def post_text(session_data):
    """The post's text. render_list returns (html, keyboard), exactly as render_state does
    — the keyboard is the full-list button, and it has its own tests below."""
    return gamesession.render_list(session_data)[0]


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

    rendered = post_text(session_data)

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

    rendered = post_text(session_data)
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

    names = achv_handlers._extract_possible_achievements(post_text(session_data))
    catalogue = {rule["name"] for rule in RULES}
    for name in names:
        assert name in catalogue, name


async def test_uncertain_rows_are_marked(context):
    session_data = await start_session(context)
    await reveal(context, 1, "tanner")
    rendered = post_text(session_data)
    # Masochist is a MAYBE — the Tanner still has to win.
    assert " - \N{BLACK QUESTION MARK ORNAMENT} Masochist" in rendered


async def test_a_swing_reachable_row_is_marked_differently(context):
    """A Cursed player's wolf achievements are real but conditional, and reading them as
    available now would be a different promise."""
    session_data = await start_session(context)
    await reveal(context, 1, "cursed")
    await reveal(context, 2, "sorcerer")
    await reveal(context, 3, "werewolf")

    rendered = post_text(session_data)
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

    rendered = visible(post_text(session_data))
    assert rendered.count("Welcome to Hell") == 1
    assert "Welcome to Hell (4):" in rendered, "named once, with a count"
    assert "Ren, omu, J J" in rendered, "and the players who can still get it"


async def test_every_name_in_the_post_is_tappable(context):
    """Names here are mentions, like every other message this bot sends.

    A post of sixteen plain names is one you cannot tap through, and two players with
    similar display names are impossible to tell apart in it.
    """
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")

    rendered = post_text(session_data)

    assert "<a href='tg://user?id=1'>Ren</a>\n" in rendered, "the player heading"
    # And in the group sections at the bottom, where the whole living roster is named.
    header = rendered.split("Welcome to Hell</b> (4):\n")[1].split("\n")[0]
    assert header.count("tg://user?id=") == 4, header


async def test_a_group_achievement_is_named_in_bold(context):
    """Its line and the line under it are both lists of names otherwise."""
    session_data = await start_session(context)
    rendered = post_text(session_data)
    assert "<b>Welcome to Hell</b> (4):" in rendered


async def test_the_mention_markup_is_not_charged_against_the_message_limit(context):
    """The markup is most of the bytes and none of the message.

    A full game renders to well over 4096 raw characters now, all of it <a href> Telegram
    never counts — measuring the raw string would trim a list that fits comfortably.
    """
    session_data = await big_game(context)
    rendered = post_text(session_data)

    assert len(rendered) > 4096, "otherwise this test proves nothing"
    assert len(visible(rendered)) <= 4096


async def test_an_unrevealed_player_is_left_out(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    rendered = post_text(session_data)
    assert "omu (" not in rendered


async def test_a_dead_player_is_left_out(context):
    """The list answers "what is still possible", and nothing is, for them."""
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    await reveal(context, 2, "seer")
    session.set_alive(session_data, 1, False)

    rendered = post_text(session_data)
    assert "Ren (" not in rendered


async def test_a_dead_player_also_stops_gating_other_peoples_achievements(context):
    """Cold as Ice needs a harlot to freeze. Once the harlot is dead, it is not possible."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")
    assert "Cold as Ice" in post_text(session_data)

    session.set_alive(session_data, 2, False)
    assert "Cold as Ice" not in post_text(session_data)


async def test_the_post_says_how_far_along_the_reveal_is(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    assert "1 of 4 revealed" in post_text(session_data)


async def test_before_anyone_reveals_only_the_roleless_sections_show(context):
    """Nothing role-gated can be judged yet, but "play a game" is already true."""
    session_data = await start_session(context)
    rendered = visible(post_text(session_data))

    assert "Welcome to Hell (4):" in rendered
    assert "0 of 4 revealed" in rendered


# --- Fitting in one message -------------------------------------------------


# The roles that produce the longest lists, and enough of them for a big table.
LOUD_ROLES = [
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


async def big_game(context):
    """Sixteen players, all revealed, in the roles that produce the longest lists."""
    roster = [(i, "Player{}".format(i)) for i in range(1, 17)]
    session_data = await start_session(context, players=roster)
    for (uid, _), role_name in zip(roster, LOUD_ROLES, strict=True):
        await reveal(context, uid, role_name)
    return session_data


async def test_a_full_game_still_fits_in_one_message(context):
    """Telegram rejects anything over 4096, and a rejected edit freezes the list.

    Measured on what a client displays, which is what the limit is actually against — the
    mention markup around every name is several times the size of the names themselves."""
    session_data = await big_game(context)
    rendered = visible(post_text(session_data))
    assert len(rendered) <= 4096, len(rendered)


async def test_trimming_drops_rows_not_players(context):
    """When the list will not fit, rows go and players stay.

    (Every role in this fixture has something available, so anyone missing from the output
    was dropped by the trimming rather than having nothing to show. A player who is simply
    out of luck is absent either way — the roster message is where "who has revealed" is
    answered, and this one only ever answers "what is still possible".)"""
    session_data = await big_game(context)
    rendered = post_text(session_data)
    for _uid, entry in session.players_in_order(session_data):
        assert entry["name"] in rendered, entry["name"]


async def test_trimming_says_that_it_trimmed(context):
    """Silently truncating would read as "this is everything"."""
    session_data = await big_game(context)
    rendered = post_text(session_data)
    assert "more</i>" in rendered or "Trimmed to fit" in rendered


async def test_the_and_n_more_line_is_not_read_as_an_achievement(context):
    """It carried the dash that means "an achievement is named here", so replying to a
    trimmed post with /info asked the catalogue for "…and 6 more"."""
    session_data = await big_game(context)
    rendered = post_text(session_data)
    assert "more</i>" in rendered, "this game was supposed to trim"

    names = achv_handlers._extract_possible_achievements(visible(rendered))

    assert not [name for name in names if "more" in name], names


async def crowded_game(context, count=24, namelen=8):
    """A table too big for one message — the size where the old ladder fell off a cliff."""
    roster = [(uid, ("N" * namelen)[:namelen] + str(uid)) for uid in range(1, count + 1)]
    session_data = await start_session(context, players=roster)
    for uid, role_name in zip(range(1, count + 1), LOUD_ROLES * 4, strict=False):
        await reveal(context, uid, role_name)
    return session_data


async def test_a_table_too_big_for_the_message_still_shows_every_player(context):
    """The regression this ladder exists for.

    With rungs of eight, five and three rows and nothing below them, a twenty-four player
    game missed every rung and landed on the certain-only pass — which drops a player
    whose achievements are all uncertain, and for most role mixes that is most of them.
    Nine thousand characters of content came out as five hundred.
    """
    session_data = await crowded_game(context)
    rendered = visible(post_text(session_data))

    for _uid, entry in session.players_in_order(session_data):
        assert entry["name"] in rendered, entry["name"]
    assert "Trimmed to fit" not in rendered, "one row each beats dropping people"
    assert len(rendered) > 2500, "and it should use the room it has"


async def test_the_group_sections_are_capped_the_way_the_rows_are(context):
    """They name every living player, so in a big game they were a quarter of the post
    and the only part of it that could not be made to give any room back."""
    session_data = await crowded_game(context)
    rendered = visible(post_text(session_data))

    assert "more</i>" in post_text(session_data)
    names_line = rendered.split("Welcome to Hell (24):\n")[1].split("\n")[0]
    assert "and " in names_line and "more" in names_line, names_line


async def test_a_capped_group_still_counts_everyone(context):
    """The number in the heading answers "how many are still in for this". A count of the
    names that happened to fit would be a different, wrong answer."""
    session_data = await crowded_game(context)
    rendered = visible(post_text(session_data))
    assert "Welcome to Hell (24):" in rendered


async def test_a_post_that_cannot_fit_at_all_is_cut_and_says_so(context):
    """Sixty players whose display names are the length Telegram allows.

    Bigger than any real game, deliberately: the point is that there is a net under the
    last rung at all. Nothing renders this in under 4096 characters, and the old floor was
    returned with no length check — so Telegram refused the edit and the list froze at
    whatever it last said, with nothing anywhere to explain why.
    """
    session_data = await crowded_game(context, count=60, namelen=128)
    rendered = post_text(session_data)

    assert "Too long to show in full" in rendered
    assert len(visible(rendered)) <= 4096, len(visible(rendered))


async def test_cutting_stops_at_a_line_boundary(context):
    """Half a player's name is a worse last line than one fewer player."""
    session_data = await crowded_game(context, count=60, namelen=128)
    rendered = visible(post_text(session_data))

    body = rendered[: rendered.index("Too long to show in full")]
    assert body.endswith("\n"), repr(body[-40:])


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
    from structlog.testing import capture_logs
    from telegram.error import BadRequest

    await start_session(context)
    await reveal(context, 1, "villager")
    await publish(context)

    context.bot._edit_error = BadRequest("Message is not modified")
    await reveal(context, 2, "villager")
    with capture_logs() as entries:
        await publish(context)  # must not raise

    assert not [e for e in entries if "failed" in e["event"]], entries


async def test_an_edit_that_failed_for_any_other_reason_is_reported(context):
    """The silent freeze. "Message is too long", "can't parse entities" and "message to
    edit not found" all wore the same exception as the no-op above, so `except BadRequest:
    pass` left the live message quietly no longer following the game — nothing in the log,
    nothing on screen, and the list still showing whatever it last managed to say."""
    from structlog.testing import capture_logs
    from telegram.error import BadRequest

    await start_session(context)
    await reveal(context, 1, "villager")
    await publish(context)

    context.bot._edit_error = BadRequest("Message_too_long")
    await reveal(context, 2, "seer")
    with capture_logs() as entries:
        await publish(context)  # still must not raise

    failures = [e for e in entries if e["event"] == "standin_list_edit_failed"]
    assert len(failures) == 1, entries
    assert "Message_too_long" in failures[0]["error"]


async def test_a_first_post_that_failed_is_reported_and_left_unrecorded(context):
    """Without a message id every later publish posts the list again instead of editing
    it, so the one thing this must not do is record an id it never got."""
    from structlog.testing import capture_logs
    from telegram.error import BadRequest

    session_data = await start_session(context)
    await reveal(context, 1, "villager")

    context.bot._send_error = BadRequest("Chat not found")
    with capture_logs() as entries:
        await publish(context)

    assert [e["event"] for e in entries if "failed" in e["event"]] == ["standin_list_post_failed"]
    assert session_data.get("list_message_id") is None

    context.bot._send_error = None
    await reveal(context, 2, "seer")
    await publish(context)
    assert session_data["list_message_id"] is not None, "and the next reveal retries"


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

    assert "Cold as Ice" not in post_text(session_data)


async def test_a_broken_rule_does_not_take_the_post_down(context, monkeypatch):
    """One bad expression must cost one row, not the whole list."""
    broken = {
        "Cold as Ice": {"tier": rulelist.CHECK, "subject": "snow_wolf", "expr": "count(", "note": ""},
        "Welcome to Hell": {"tier": rulelist.ALWAYS, "subject": "any", "expr": "True", "note": ""},
    }
    monkeypatch.setattr(db, "get_rules", lambda: broken)

    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")

    rendered = post_text(session_data)
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
    assert "Cold as Ice" in post_text(session_data)

    session.set_attained(session_data, 1, ["Cold as Ice"])
    assert "Cold as Ice" not in post_text(session_data)


async def test_one_players_collection_does_not_hide_it_from_another(context):
    """Two Snow Wolves, one of whom has it: the other must still be told."""
    session_data = await start_session(context, players=[(1, "Ren"), (2, "omu"), (3, "J J")])
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "snow_wolf")
    await reveal(context, 3, "harlot")

    session.set_attained(session_data, 1, ["Cold as Ice"])
    rendered = post_text(session_data)

    ren, _, rest = visible(rendered).partition("omu\n")
    assert "Cold as Ice" not in ren
    assert "Cold as Ice" in rest


async def test_a_roleless_achievement_lists_only_the_players_missing_it(context):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    session.set_attained(session_data, 1, ["Welcome to Hell"])
    session.set_attained(session_data, 2, ["Welcome to Hell"])

    rendered = visible(post_text(session_data))
    assert "Welcome to Hell (2):" in rendered
    assert "Ren" not in rendered.split("Welcome to Hell (2):")[1].split("\n")[1]


async def test_an_achievement_everybody_already_has_is_left_out_entirely(context):
    """Printed with an empty list it would read as a row nobody can earn."""
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    for uid in (1, 2, 3, 4):
        session.set_attained(session_data, uid, ["Welcome to Hell"])

    assert "Welcome to Hell" not in post_text(session_data)


async def test_an_unknown_collection_shows_everything(context):
    """The stats API is occasionally unavailable. A game played during one of those
    minutes should still get a list — hiding a row nobody can verify is the worse error."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")

    assert session_data["players"]["1"]["attained"] is None
    assert "Cold as Ice" in post_text(session_data)


async def test_the_session_fetches_every_players_collection_once(context, monkeypatch):
    """One batch at /gs, not one per render: a publish happens every few seconds."""
    calls = []

    async def fake_get(user_id):
        calls.append(user_id)
        return [{"name": "Welcome to Hell"}]

    monkeypatch.setattr(api, "get_achievements", fake_get)
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

    monkeypatch.setattr(api, "get_achievements", flaky)
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


# --- Alts --------------------------------------------------------------------
#
# A second account belonging to somebody already at the table. Being an alt is a fact about
# the *account*, not about a round — the same person brings the same spare account every
# time — so it is stored in the database and outlives every session, and /alt is the one
# command here that answers without a game in progress.
#
# Alts keep their role and stay in the composition: what everyone else can earn depends on
# that role existing. They are only left out of the output, since achievements landing on
# an account nobody collects for are noise in a list whose job is to stay readable.


@pytest.fixture
def alts(monkeypatch):
    """The alt store, in memory. Patched over db so these need no Postgres."""
    marked = set()

    async def toggle(user_id, name, marked_by):
        if user_id in marked:
            marked.discard(user_id)
            return False
        marked.add(user_id)
        return True

    async def not_an_admin(user_id):
        return False

    monkeypatch.setattr(db, "is_alt_account", lambda uid: uid in marked)
    monkeypatch.setattr(db, "toggle_alt_account", toggle)
    # The permission check consults the admins table; there is no database here.
    monkeypatch.setattr(db, "is_admin", not_an_admin)
    return marked


async def alt(context, text="", user_id=1, name="Ren", reply_to=None):
    msg = player_message("/alt " + text, user_id=user_id, name=name, reply_to=reply_to)
    context.args = text.split()
    await gamesession.alt_cmd(FakeUpdate(message=msg), context)
    return msg


async def test_an_alt_is_left_out_of_the_list(context, alts):
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")
    assert "Cold as Ice" in post_text(session_data)

    alts.add(1)
    assert "Cold as Ice" not in post_text(session_data)


async def test_an_alts_role_still_counts_for_everybody_else(context, alts):
    """The point of the distinction: they are playing, they are just not collecting."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")
    alts.add(2)

    rendered = post_text(session_data)
    assert "Cold as Ice" in rendered, "the Snow Wolf can still freeze the alt's harlot"
    assert "omu\n" not in rendered, "but the harlot has no entry of their own"


async def test_an_alt_is_not_counted_among_the_players_who_can_get_a_group_achievement(context, alts):
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    assert "Welcome to Hell (4):" in visible(post_text(session_data))

    alts.add(4)
    assert "Welcome to Hell (3):" in visible(post_text(session_data))


async def test_the_roster_says_who_is_an_alt(context, alts):
    """Otherwise the only sign is an absence, which reads as a bug."""
    session_data = await start_session(context)
    await reveal(context, 1, "villager")
    alts.add(1)

    rendered, _ = gamesession.render_state(session_data)
    assert "(alt)" in rendered


async def test_alt_marks_the_sender_by_default(context, alts):
    await start_session(context)
    msg = await alt(context)

    assert 1 in alts
    assert "is an alt" in msg.last_reply


async def test_alt_works_with_no_game_running(context, alts):
    """Being somebody's second account has nothing to do with a round being in progress."""
    msg = await alt(context)

    assert session.get(context.chat_data) is None
    assert 1 in alts
    assert "is an alt" in msg.last_reply


async def test_alt_marks_the_replied_to_account_even_with_no_game(context, alts):
    theirs = message("hi", from_user=FakeUser(7, "Someone"))
    await alt(context, reply_to=theirs)
    assert 7 in alts


async def test_alt_can_mention_a_player_during_a_game(context, alts):
    from test_standin_session import OMU, invoke

    await start_session(context)
    await invoke(gamesession.alt_cmd, context, "/alt", mentions=[OMU])
    assert 2 in alts


async def test_something_that_names_nobody_marks_nobody(context, alts):
    """A typed display name is not an identifier — reply or mention instead."""
    msg = await alt(context, "omu")

    assert alts == set()
    assert "Reply to the account" in msg.last_reply


async def test_alt_toggles_so_a_mistake_can_be_undone(context, alts):
    """Marking the wrong person otherwise costs them their list in *every* future game."""
    await start_session(context)
    for _ in range(2):
        msg = await alt(context)

    assert alts == set()
    assert "not an alt any more" in msg.last_reply


async def test_marking_an_alt_is_ungated(context, alts):
    """The ordinary flow is a main account replying to its own alt — two different users.

    Any "only yourself" rule would break the one thing this command exists for. What keeps
    it safe is that it toggles and anyone can unmark themselves, so a wrong mark is undone
    by the person it was wrong about.
    """
    theirs = message("hi", from_user=FakeUser(7, "Someone"))
    await alt(context, user_id=555, name="Passer By", reply_to=theirs)
    assert 7 in alts


async def test_anybody_can_unmark_their_own_account(context, alts):
    """Which is what makes an ungated mark recoverable without hunting down who made it."""
    alts.add(555)
    await alt(context, user_id=555, name="Passer By")
    assert alts == set()


async def test_alt_naming_somebody_unknown_marks_nobody(context, alts):
    await start_session(context)
    msg = await alt(context, "Nobody")

    assert alts == set()
    assert "Reply to the account" in msg.last_reply
