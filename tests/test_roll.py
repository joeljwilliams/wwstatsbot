"""/roll — deciding who chases an achievement only one player can have.

Several achievements can only be had once in a game: one player guards the wolf and
survives, one gets the tanner lynched. When the Possible Achievements post lists the same
one under several people, somebody has to decide who goes for it, and doing that by hand
starts arguments. This does it out loud.

The candidates come from the post, not from the achievement catalogue — the answer has to
be somebody who is actually listed. Both shapes of the post count: rows nested under a
player, and the group sections at the bottom naming everyone who can get a roleless one.
"""

import pytest
from conftest import FakeEntity, FakeUpdate, FakeUser, bot_message, message

import builders
import db
from handlers import achievements as achv_handlers

# Captured before the autouse fixture below replaces it, so the one test that wants the
# real search path can put it back.
_REAL_SEARCH = builders.build_info_results

# The real thing, from a live game.
POST = """Possible Achievements:

Mango
 - Did you guard yourself?
 - Traffic Control
 - Food Waste


omu
 - Did you guard yourself?


shu . \N{COMBINING RING ABOVE}\N{SUBSCRIPT PLUS SIGN} \N{DIVISION SIGN} .
 - Double Shot


Ludwig \N{CRESCENT MOON}
 - Double Shot


KAI \N{SPARKLES}
 - Did you guard yourself?
 - Double Shot
"""

# The stand-in's own post, which puts roleless achievements in a section at the bottom.
POST_WITH_GROUPS = """Possible Achievements:

ieb
 - \N{BLACK QUESTION MARK ORNAMENT} Masochist


In for the Long Haul (3):
ieb, Infinite, D_Evil_SK

Death Village (1):
ieb
"""


@pytest.fixture(autouse=True)
def no_catalogue_search(monkeypatch):
    """The fall-through search needs Postgres; these tests are about the post's own text.

    The one test that exercises the fall-through patches it with something that answers.
    """

    async def _nothing(query):
        return []

    monkeypatch.setattr(achv_handlers.builders, "build_info_results", _nothing)


async def roll(context, query, replied_text=POST, winner=None, monkeypatch=None, entities=None):
    if winner is not None:
        monkeypatch.setattr(achv_handlers, "_pick", lambda candidates: winner)
    replied = bot_message(replied_text, entities=entities)
    msg = message("/roll " + query, from_user=FakeUser(1, "Ren"), reply_to_message=replied)
    context.args = query.split()
    await achv_handlers.roll_cmd(FakeUpdate(message=msg), context)
    return msg


# --- Reading the post --------------------------------------------------------


def test_rows_are_read_under_the_player_they_belong_to():
    per_player, _ = achv_handlers._extract_by_player(POST)
    names = [player for player, _ in per_player]

    assert names[0] == "Mango"
    assert per_player[0][1] == ["Did you guard yourself?", "Traffic Control", "Food Waste"]
    assert len(names) == 5


def test_the_heading_is_not_read_as_a_player():
    per_player, _ = achv_handlers._extract_by_player(POST)
    assert all(not player.endswith(":") for player, _ in per_player)


def test_group_sections_are_read_as_the_players_who_can_get_one():
    _, groups = achv_handlers._extract_by_player(POST_WITH_GROUPS)
    assert groups["In for the Long Haul"] == ["ieb", "Infinite", "D_Evil_SK"]
    assert groups["Death Village"] == ["ieb"]


def test_a_status_marker_is_stripped_from_a_row():
    per_player, _ = achv_handlers._extract_by_player(POST_WITH_GROUPS)
    assert per_player[0][1] == ["Masochist"]


def test_a_dash_prefixed_player_name_is_not_read_as_a_row():
    """ "-Mini | ˹ʙᴜ…" is a real player; the indent is what tells the two apart."""
    post = "Possible Achievements:\n\n-Mini\n - Double Shot\n"
    per_player, _ = achv_handlers._extract_by_player(post)
    assert per_player == [("-Mini", ["Double Shot"])]


def test_players_with_no_rows_are_dropped():
    post = "Possible Achievements:\n\nMango\n\nomu\n - Double Shot\n"
    per_player, _ = achv_handlers._extract_by_player(post)
    assert [player for player, _ in per_player] == ["omu"]


# --- Who is in the running ---------------------------------------------------


def test_candidates_are_everyone_listed_under_that_achievement():
    assert achv_handlers._players_who_can_get(POST, "Double Shot") == [
        "shu . \N{COMBINING RING ABOVE}\N{SUBSCRIPT PLUS SIGN} \N{DIVISION SIGN} .",
        "Ludwig \N{CRESCENT MOON}",
        "KAI \N{SPARKLES}",
    ]


def test_candidates_come_from_the_group_section_too():
    assert achv_handlers._players_who_can_get(POST_WITH_GROUPS, "In for the Long Haul") == [
        "ieb",
        "Infinite",
        "D_Evil_SK",
    ]


def test_an_achievement_nobody_has_listed_has_no_candidates():
    assert achv_handlers._players_who_can_get(POST, "Cold as Ice") == []


# --- Naming the achievement --------------------------------------------------


async def test_the_query_matches_what_the_post_lists_whatever_the_casing():
    assert await achv_handlers._listed_achievement(POST, "double shot") == ("Double Shot", False)
    assert await achv_handlers._listed_achievement(POST, "DOUBLE SHOT") == ("Double Shot", False)


async def test_a_unique_fragment_is_enough():
    assert await achv_handlers._listed_achievement(POST, "traffic") == ("Traffic Control", False)


async def test_an_ambiguous_fragment_is_refused_rather_than_guessed():
    """A fragment in three of them; picking one decides a game on a coin toss nobody saw."""
    assert await achv_handlers._listed_achievement(POST, "d") == (None, True)


async def test_something_the_post_does_not_list_is_not_matched():
    assert await achv_handlers._listed_achievement(POST, "Cold as Ice") == (None, False)


async def test_a_query_the_post_text_misses_falls_through_to_the_shared_search(monkeypatch):
    """ "dygy" is how the group says "Did you guard yourself?" — an initialism, which only
    resolves through the same index /info and /sch use, never through the post's own text."""

    async def fake_search(query):
        assert query == "dygy"
        return [{"name": "Did you guard yourself?"}]

    monkeypatch.setattr(achv_handlers.builders, "build_info_results", fake_search)
    assert await achv_handlers._listed_achievement(POST, "dygy") == ("Did you guard yourself?", False)


async def test_the_shared_search_never_decides_who_can_get_it(monkeypatch):
    """It says *which* achievement was meant. If the post does not list it, nobody rolls."""

    async def fake_search(query):
        return [{"name": "Cold as Ice"}]

    monkeypatch.setattr(achv_handlers.builders, "build_info_results", fake_search)
    assert await achv_handlers._listed_achievement(POST, "cai") == (None, False)


async def test_rolling_an_initialism_works_end_to_end(context, monkeypatch):
    """The reported bug: /roll dygy said nobody could get it."""

    async def fake_search(query):
        return [{"name": "Did you guard yourself?"}]

    monkeypatch.setattr(achv_handlers.builders, "build_info_results", fake_search)
    msg = await roll(context, "dygy")

    assert "Rolling <b>Did you guard yourself?</b>" in msg.last_reply
    assert "Mango" in msg.last_reply


async def test_rolling_a_two_letter_initialism_works_end_to_end(context, monkeypatch):
    """The three-letter floor is gone, so "ds" has to reach Double Shot through the real
    search — not a stub. Patched back to the genuine builder for exactly that reason: a
    fake here would pass whatever the floor did."""
    catalogue = [{"name": "Double Shot", "desc": "Shoot twice", "type": "instantaneous", "notes": ""}]

    async def _no_fts(query):
        return []

    monkeypatch.setattr(db, "get_achievements", lambda: catalogue)
    monkeypatch.setattr(db, "search_achievements", _no_fts)
    monkeypatch.setattr(achv_handlers.builders, "build_info_results", _REAL_SEARCH)

    msg = await roll(context, "ds", winner="KAI \N{SPARKLES}", monkeypatch=monkeypatch)

    assert "Rolling <b>Double Shot</b>" in msg.last_reply
    assert "KAI" in msg.last_reply


async def test_an_ambiguous_query_says_so_rather_than_denying_it(context):
    msg = await roll(context, "d")
    assert "matches more than one" in msg.last_reply


# --- The command -------------------------------------------------------------


async def test_the_roll_names_the_candidates_and_the_winner(context, monkeypatch):
    msg = await roll(context, "double shot", winner="Ludwig \N{CRESCENT MOON}", monkeypatch=monkeypatch)

    assert "Rolling <b>Double Shot</b>" in msg.last_reply
    assert "Ludwig" in msg.last_reply and "KAI" in msg.last_reply
    assert "Winner is <b>Ludwig \N{CRESCENT MOON}</b>" in msg.last_reply


async def test_the_winner_is_drawn_from_the_candidates(context):
    """Whoever wins, they must be one of the people the post actually listed."""
    seen = set()
    for _ in range(30):
        msg = await roll(context, "double shot")
        seen.add(msg.last_reply.split("Winner is <b>")[1].split("</b>")[0])

    assert seen <= {
        "shu . \N{COMBINING RING ABOVE}\N{SUBSCRIPT PLUS SIGN} \N{DIVISION SIGN} .",
        "Ludwig \N{CRESCENT MOON}",
        "KAI \N{SPARKLES}",
    }
    assert len(seen) > 1, "and it should not always be the same one"


async def test_rolling_between_one_player_says_so_instead(context):
    """Pretending to roll a field of one reads as rigged."""
    msg = await roll(context, "traffic control")

    assert "only one who can get" in msg.last_reply
    assert "Rolling" not in msg.last_reply


async def test_an_achievement_nobody_can_get_is_reported(context):
    msg = await roll(context, "Cold as Ice")
    assert "Nobody in that list" in msg.last_reply


async def test_a_reply_to_something_that_is_not_a_list_says_so(context):
    msg = await roll(context, "double shot", replied_text="just a normal message")
    assert "Possible Achievements" in msg.last_reply


async def test_roll_with_no_reply_explains_itself(context):
    msg = message("/roll double shot", from_user=FakeUser(1, "Ren"))
    context.args = ["double", "shot"]
    await achv_handlers.roll_cmd(FakeUpdate(message=msg), context)

    assert "Reply to a" in msg.last_reply


async def test_roll_with_no_achievement_explains_itself(context):
    msg = message("/roll", from_user=FakeUser(1, "Ren"), reply_to_message=bot_message(POST))
    context.args = []
    await achv_handlers.roll_cmd(FakeUpdate(message=msg), context)

    assert "Reply to a" in msg.last_reply


async def test_a_name_with_html_in_it_is_escaped(context, monkeypatch):
    """A real player in this group is called "ᐝѕнαяиαѕ <🌸> 🥉"."""
    brackets = "\N{MODIFIER LETTER SMALL TURNED ALPHA}ѕнαяиαѕ <\N{CHERRY BLOSSOM}>"
    post = "Possible Achievements:\n\n{}\n - Double Shot\n\nomu\n - Double Shot\n".format(brackets)
    msg = await roll(context, "double shot", replied_text=post, winner=brackets, monkeypatch=monkeypatch)

    assert "&lt;" in msg.last_reply
    assert "<\N{CHERRY BLOSSOM}>" not in msg.last_reply


# --- Naming the winner tappably ----------------------------------------------
#
# The names in a roll are read back out of somebody else's message, so an id exists only
# if that message carried one. When it did, saying who won should be as tappable as every
# other name this bot prints; when it did not, the plain name is all there is.


def post_mentioning(players, achievement="Double Shot"):
    """A Possible Achievements post whose player names are real text_mentions."""
    text = "Possible Achievements:\n\n"
    entities = []
    for uid, name in players:
        entities.append(
            FakeEntity(
                "text_mention",
                offset=len(text.encode("utf-16-le")) // 2,
                length=len(name.encode("utf-16-le")) // 2,
                user=FakeUser(uid, name),
            )
        )
        text += "{}\n - {}\n\n".format(name, achievement)
    return text, entities


async def test_a_winner_the_post_mentioned_is_rendered_as_a_mention(context, monkeypatch):
    text, entities = post_mentioning([(7, "Mango"), (8, "omu")])
    msg = await roll(
        context, "double shot", replied_text=text, entities=entities, winner="Mango", monkeypatch=monkeypatch
    )

    assert "<a href='tg://user?id=7'>Mango</a>" in msg.last_reply
    assert "<a href='tg://user?id=8'>omu</a>" in msg.last_reply, "the candidates too"


async def test_a_post_with_plain_names_still_rolls(context, monkeypatch):
    """The incumbent's posts are plain text. Inventing a link would point at nobody."""
    msg = await roll(context, "double shot", winner="Ludwig \N{CRESCENT MOON}", monkeypatch=monkeypatch)

    assert "tg://user" not in msg.last_reply
    assert "Winner is <b>Ludwig \N{CRESCENT MOON}</b>" in msg.last_reply


async def test_a_mentioned_name_with_html_in_it_is_still_escaped(context, monkeypatch):
    brackets = "\N{MODIFIER LETTER SMALL TURNED ALPHA}ѕнαяиαѕ <\N{CHERRY BLOSSOM}>"
    text, entities = post_mentioning([(7, brackets), (8, "omu")])
    msg = await roll(
        context, "double shot", replied_text=text, entities=entities, winner=brackets, monkeypatch=monkeypatch
    )

    assert "&lt;" in msg.last_reply
    assert "<\N{CHERRY BLOSSOM}>" not in msg.last_reply
    assert "tg://user?id=7" in msg.last_reply


async def test_only_one_candidate_is_mentioned_too(context):
    text, entities = post_mentioning([(7, "Mango")])
    msg = await roll(context, "double shot", replied_text=text, entities=entities)

    assert "only one who can get" in msg.last_reply
    assert "<a href='tg://user?id=7'>Mango</a>" in msg.last_reply
