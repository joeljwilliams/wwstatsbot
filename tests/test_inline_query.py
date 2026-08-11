"""Inline queries (@wwstatsbot ... from any chat).

A whole user-facing surface that was previously untested, and one that reuses the same
builders and search path as the slash commands — so moving `builders.py` or `api.py`
during the split can break it while every command test still passes.

Two distinct behaviours share the handler: an empty query returns the querying user's
four stat cards, and typed text becomes an achievement search identical to /info.
"""

from conftest import FakeContext, FakeInlineQuery, FakeUpdate, FakeUser

import builders
import db
from handlers import inline as inline_handlers


async def answer(query="", user=None):
    """Run the handler and hand back the FakeInlineQuery holding the results."""
    inline = FakeInlineQuery(query=query, from_user=user or FakeUser(7, "Alice"))
    await inline_handlers.inline_query(FakeUpdate(inline_query=inline), FakeContext())
    return inline


# --- Empty query: the user's own stats ------------------------------------------


async def test_empty_query_offers_the_four_stat_cards(stats_api):
    inline = await answer("")
    assert [r.id for r in inline.results] == ["stats", "kills", "killedby", "deaths"]


async def test_empty_query_titles(stats_api):
    inline = await answer("")
    assert [r.title for r in inline.results] == ["My Stats", "My Kills", "My Killed By", "My Deaths"]


async def test_empty_query_cards_carry_the_rendered_message(stats_api):
    inline = await answer("")
    by_id = {r.id: r.input_message_content.message_text for r in inline.results}
    assert "Alice the Villager" in by_id["stats"]
    assert "most killed:" in by_id["kills"]
    assert "who killed" in by_id["killedby"]
    assert "Types of deaths" in by_id["deaths"]


async def test_whitespace_only_query_is_treated_as_empty(stats_api):
    inline = await answer("   ")
    assert [r.id for r in inline.results] == ["stats", "kills", "killedby", "deaths"]


async def test_empty_query_uses_the_querying_users_id(stats_api):
    """Inline queries have no chat context, so the sender is always the subject."""
    await answer("", user=FakeUser(4242, "Bob"))
    assert all(request.url.params.get("pid") == "4242" for request in stats_api.requests)


# --- Typed query: achievement search --------------------------------------------


async def test_text_query_searches_achievements(achievements, no_fts, stats_api):
    inline = await answer("busy")
    assert [r.id for r in inline.results] == ["Busy Night"]
    assert inline.results[0].title == "Busy Night"


async def test_text_query_results_carry_the_full_card(achievements, no_fts, stats_api):
    inline = await answer("busy")
    text = inline.results[0].input_message_content.message_text
    assert text == builders.format_single_achv(next(a for a in achievements if a["name"] == "Busy Night"))


async def test_text_query_description_is_the_achievement_description(achievements, no_fts, stats_api):
    inline = await answer("busy")
    assert inline.results[0].description == "Be visited by four different roles in one night"


async def test_no_match_returns_a_single_explanatory_result(achievements, no_fts, stats_api):
    """An empty result list shows Telegram's blank menu, which reads as a broken bot."""
    inline = await answer("zzzznotanachievement")
    assert len(inline.results) == 1
    assert inline.results[0].id == "none"
    assert inline.results[0].title == "No matching achievements"


async def test_results_are_capped_at_fifty(monkeypatch, no_fts, stats_api):
    """Telegram rejects an inline answer with more than 50 results."""
    many = [{"name": "Achv {}".format(i), "desc": "d", "type": "game-end", "notes": ""} for i in range(80)]
    monkeypatch.setattr(db, "get_achievements", lambda: many)

    inline = await answer("Achv")
    assert len(inline.results) == 50


async def test_a_short_query_is_still_searched(achievements, no_fts, stats_api):
    """Unlike /search and /info, inline has no 3-character minimum — it answers on
    every keystroke, so a 1-character query must not error."""
    inline = await answer("b")
    assert inline.answers, "a single-character query must still be answered"


# --- The answer envelope ---------------------------------------------------------


async def test_answers_are_personal_and_briefly_cached(achievements, no_fts, stats_api):
    """is_personal matters for correctness, not just efficiency: results are built from
    the querying user's own stats, so a shared cache would leak them between users."""
    inline = await answer("busy")
    assert inline.answers[-1]["is_personal"] is True
    assert inline.answers[-1]["cache_time"] == 30


async def test_message_content_disables_link_previews(achievements, no_fts, stats_api):
    """Cards contain tg://user links; a preview would render an unwanted attachment."""
    inline = await answer("busy")
    content = inline.results[0].input_message_content
    assert content.link_preview_options.is_disabled is True


async def test_message_content_is_html(achievements, no_fts, stats_api):
    inline = await answer("busy")
    assert inline.results[0].input_message_content.parse_mode == "HTML"
