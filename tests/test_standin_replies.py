"""Replying to *our own* Possible Achievements post reads the session, not the message.

The post is a view of the session, and a trimmed one: a twenty-four-player game has
around nine thousand characters of content and four thousand to put it in. Everything
that trimming drops used to be invisible to `/info` and `/roll`, because both read the
text of the message they were replying to — so a roll could draw from three of a player's
nine rows and nothing on screen said the other six existed.

Two properties carry this file.

**What the session knows beats what the message shows.** A candidate the post had no room
for is still a candidate, and an achievement it could not print is still cardable.

**The two readings agree when nothing was trimmed.** `gamesession.reply_contents` is a
second way of answering the question `_extract_by_player` answers, and a second answer
that drifts is worse than no second answer at all — so an untrimmed post is rendered,
parsed, and compared against the session it came from.
"""

import pytest
from conftest import FakeUpdate, FakeUser, bot_message, message
from test_standin_list import big_game, visible
from test_standin_session import reveal, start_session

import db
from handlers import achievements as achv_handlers
from handlers import gamesession
from rulelist import RULES

# Enough distinct roles to fill a twenty-four player table. The mix matters only in that
# it produces a long list; which roles they are is the feasibility module's business.
ROLES = [
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
    "seer",
    "traitor",
    "sorcerer",
    "snow_wolf",
    "detective",
    "beholder",
    "wild_child",
    "mason",
]


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    """The real catalogue, without a database. db.get_rules() is the live read path."""
    monkeypatch.setattr(db, "get_rules", lambda: {rule["name"]: rule for rule in RULES})


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    """/roll falls through to the shared search when a query names nothing listed.

    Stubbed empty so these tests measure what the *post* offers, which is the thing that
    changed, rather than what the catalogue would have rescued.
    """

    async def nothing(query):
        return []

    monkeypatch.setattr(achv_handlers.builders, "build_info_results", nothing)


async def posted_list(context, session_data):
    """Publish the list and hand back the message a player would be replying to.

    Carrying the text Telegram would hold — the post with its markup applied — so that
    every fallback in these tests is the real one. The entities are not reproduced, so a
    name read back out of this message is never tappable; that is the *worse* half of the
    comparison and the half these tests are about.
    """
    await context.job_queue.run_pending(context)
    return bot_message(visible(gamesession.render_list(session_data)), message_id=session_data["list_message_id"])


async def crowded(context, count=24):
    """A game big enough that the post cannot print everything it knows."""
    roster = [(uid, "Player{}".format(uid)) for uid in range(1, count + 1)]
    session_data = await start_session(context, players=roster)
    for uid, role in zip(range(1, count + 1), ROLES * 3, strict=False):
        await reveal(context, uid, role)
    return session_data


# --- What the session knows beats what the message shows --------------------


async def test_info_cards_what_the_post_had_no_room_to_print(context):
    """The post is trimmed to three rows a player; /info still sees all of them."""
    session_data = await crowded(context)
    replied = await posted_list(context, session_data)

    shown = achv_handlers._extract_possible_achievements(visible(gamesession.render_list(session_data)))
    per_player, _groups, _mentions = achv_handlers._post_contents(context, replied)
    known = achv_handlers._row_names(per_player)

    assert len(known) > len(shown), (len(known), len(shown))
    assert set(shown) <= set(known), "and it is a superset, not a different answer"


async def test_a_candidate_the_post_could_not_fit_can_still_be_rolled(context):
    """Read off the message, a roll draws from the rows that survived the trimming.

    Asserted as a comparison between the two readings rather than against a hand-picked
    achievement, because which one gets trimmed depends on the role mix — the property is
    that the session never knows fewer candidates than the message shows.
    """
    session_data = await crowded(context)
    replied = await posted_list(context, session_data)

    per_player, groups, _mentions = achv_handlers._post_contents(context, replied)
    from_text = achv_handlers._extract_by_player(visible(gamesession.render_list(session_data)))

    wider = []
    for _player, rows in per_player:
        for achievement in rows:
            ours = achv_handlers._players_who_can_get((per_player, groups), achievement)
            theirs = achv_handlers._players_who_can_get(from_text, achievement)
            assert set(theirs) <= set(ours), achievement
            if len(ours) > len(theirs):
                wider.append(achievement)

    assert wider, "this game was supposed to be too big for one message"


async def test_a_trimmed_out_player_is_still_tappable(context):
    """`mentions` covers the table, not only the names the post had room to mention."""
    session_data = await crowded(context)
    replied = await posted_list(context, session_data)

    _per_player, _groups, mentions = achv_handlers._post_contents(context, replied)

    assert len(mentions) == 24
    assert mentions["Player24"] == 24


# --- Somebody else's post is still read the only way it can be --------------


async def test_a_post_that_is_not_ours_is_parsed_from_its_text(context):
    """The real manager's post, a forward, an older list: nothing to look up."""
    await crowded(context)
    other = bot_message("Possible Achievements:\n\nMango\n - Double Shot\n", message_id=9999)

    per_player, _groups, _mentions = achv_handlers._post_contents(context, other)

    assert per_player == [("Mango", ["Double Shot"])]


async def test_a_reply_in_a_chat_with_no_session_is_parsed_from_its_text(context):
    other = bot_message("Possible Achievements:\n\nMango\n - Double Shot\n", message_id=1)

    per_player, _groups, _mentions = achv_handlers._post_contents(context, other)

    assert per_player == [("Mango", ["Double Shot"])]


# --- The two readings agree ------------------------------------------------


async def test_the_session_and_the_parser_agree_on_an_untrimmed_post(context):
    """The drift guard. Two ways of answering one question is one too many unless they
    are checked against each other, and this is the check."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")
    await reveal(context, 3, "seer")

    rendered = gamesession.render_list(session_data)
    assert "Trimmed to fit" not in rendered and "more</i>" not in rendered, "must be whole"

    parsed_players, parsed_groups = achv_handlers._extract_by_player(visible(rendered))
    session_players, session_groups, _mentions = gamesession.reply_contents(
        context.chat_data, bot_message("", message_id=session_data["list_message_id"])
    )

    assert session_players == parsed_players
    assert session_groups == parsed_groups


async def test_they_agree_on_a_full_sixteen_player_game(context):
    """The same check where the post is long enough to be interesting, with the trimming
    turned off so the comparison is against everything rather than against a fragment."""
    session_data = await big_game(context)

    contents = gamesession.list_contents(session_data)
    whole = gamesession._build_list(session_data, contents, None, include_uncertain=True)
    parsed_players, parsed_groups = achv_handlers._extract_by_player(visible(whole))
    session_players, session_groups, _mentions = gamesession.reply_contents(
        context.chat_data, bot_message("", message_id=session_data["list_message_id"])
    )

    assert session_players == parsed_players
    assert session_groups == parsed_groups


# --- End to end -------------------------------------------------------------


async def test_roll_against_our_own_post_names_a_player_from_the_session(context, monkeypatch):
    session_data = await crowded(context)
    replied = await posted_list(context, session_data)

    per_player, groups, _mentions = achv_handlers._post_contents(context, replied)
    achievement, candidates = next(
        (name, achv_handlers._players_who_can_get((per_player, groups), name))
        for _player, rows in per_player
        for name in rows
        if len(achv_handlers._players_who_can_get((per_player, groups), name)) > 1
    )

    monkeypatch.setattr(achv_handlers, "_pick", lambda options: options[0])
    msg = message("/roll " + achievement, from_user=FakeUser(1, "Player1"), reply_to_message=replied)
    context.args = achievement.split()
    await achv_handlers.roll_cmd(FakeUpdate(message=msg), context)

    assert "Rolling" in msg.last_reply
    assert candidates[0] in msg.last_reply
