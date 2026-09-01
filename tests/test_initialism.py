"""Short queries resolved as initialisms — "hp" for Helpful Paranoia.

Two letters used to be refused outright ("please enter at least 3 letters"), because
full-text search cannot answer a fragment that short: it can only prefix-match, and
"hp" as a prefix hits words beginning "hp", none of which is Helpful Paranoia. Read as
an initialism the same two letters have exactly one answer, so the floor is gone and
`build_info_results` puts exact-initialism hits in front instead.

Two properties are pinned here, and they pull against each other:

* a short query must *reach* its initialism — the point of the change;
* a short query must not *lose* the prefix search it already had. Inline mode never had
  the three-letter floor, so "he" has always found Helpful Paranoia and Here's Johnny
  as the user types, and turning short queries into initialism-only lookups would have
  quietly broken as-you-type for every two-letter prefix.

The Python initialism in db.py mirrors the SQL expression generating `search_tsv`;
tests/test_db.py holds the Postgres-gated test that the two agree.
"""

from conftest import FakeContext, FakeInlineQuery, FakeUpdate, FakeUser, message

import builders
import db
from handlers import achievements as achv_handlers
from handlers import inline as inline_handlers
from handlers import search as search_handlers

# --- The initialism itself -------------------------------------------------------


def test_initialism_takes_the_first_letter_of_each_word():
    assert db.initialism("Helpful Paranoia") == "HP"
    assert db.initialism("Welcome to Hell") == "WtH"


def test_apostrophes_are_stripped_before_words_are_split():
    """The bug the SQL expression records: "Should've" is one word, not two."""
    assert db.initialism("Should've Said Something") == "SSS"
    assert db.initialism("Should’ve Said Something") == "SSS"


def test_punctuation_contributes_no_letters():
    assert db.initialism("Good Choice... For You") == "GCFY"
    assert db.initialism("O HAI DER!") == "OHD"


# --- search_initialism -----------------------------------------------------------


def test_search_initialism_is_case_insensitive(achievements):
    assert [a["name"] for a in db.search_initialism("lb")] == ["Liquid Business"]
    assert [a["name"] for a in db.search_initialism("LB")] == ["Liquid Business"]


def test_search_initialism_is_exact_not_a_prefix(achievements):
    """ "b" must not return every B-initialled achievement. /info and /roll treat the
    first result as *the* answer, so a broad guess is worse than no answer."""
    assert db.search_initialism("b") == []


def test_search_initialism_strips_apostrophes_like_the_index(achievements):
    """Here's Johnny! is HJ, not HsJ — the contraction is one word."""
    assert [a["name"] for a in db.search_initialism("hj")] == ["Here's Johnny!"]


# --- build_info_results ----------------------------------------------------------


async def test_a_two_letter_query_finds_its_initialism(achievements, no_fts):
    found = await builders.build_info_results("lb")
    assert found[0]["name"] == "Liquid Business"


async def test_a_one_letter_query_finds_its_initialism(achievements, no_fts):
    found = await builders.build_info_results("e")
    assert found[0]["name"] == "Explorer"


async def test_an_initialism_hit_outranks_the_substring_fallback(achievements, no_fts):
    """ "e" is Explorer's initialism and a substring of most other names. /info shows the
    first result and nothing else, so the initialism has to come first or the change buys
    nothing for the command that needed it most."""
    found = await builders.build_info_results("e")
    assert found[0]["name"] == "Explorer"
    assert len(found) > 1, "the ordinary substring results must still be there behind it"


async def test_a_short_query_still_reaches_the_ordinary_search(achievements, no_fts):
    """No regression for inline as-you-type: "bu" is nobody's initialism and must keep
    finding Busy Night by substring."""
    names = [a["name"] for a in await builders.build_info_results("bu")]
    assert "Busy Night" in names


async def test_initialism_hits_do_not_duplicate_the_ordinary_results(achievements, no_fts):
    """A query can reach the same achievement both ways; it must appear once."""
    names = [a["name"] for a in await builders.build_info_results("e")]
    assert names.count("Explorer") == 1


async def test_longer_queries_are_left_to_full_text_search(monkeypatch, achievements):
    """Three letters and up keep the FTS ranking /info depends on: search_tsv already
    indexes initialisms at weight B, so nothing needs reordering in front of it."""
    called = []

    async def _fts(query):
        called.append(query)
        return [a for a in achievements if a["name"] == "Busy Night"]

    def _must_not_run(query):
        raise AssertionError("search_initialism ran for a long query: {!r}".format(query))

    monkeypatch.setattr(db, "search_achievements", _fts)
    monkeypatch.setattr(db, "search_initialism", _must_not_run)

    found = await builders.build_info_results("night")
    assert called == ["night"]
    assert [a["name"] for a in found] == ["Busy Night"]


# --- The commands ----------------------------------------------------------------


async def test_info_answers_a_two_letter_query(achievements, no_fts):
    msg = message("/info lb")
    await achv_handlers.display_achv_info(FakeUpdate(message=msg), FakeContext(args=["lb"]))
    assert "Liquid Business" in msg.last_reply


async def test_search_answers_a_two_letter_query(achievements, no_fts, stats_api):
    msg = message("/sch lb")
    await search_handlers.display_search(FakeUpdate(message=msg), FakeContext(args=["lb"]))
    assert "Liquid Business" in msg.last_reply


async def test_inline_answers_a_two_letter_query(achievements, no_fts, stats_api):
    inline = FakeInlineQuery(query="lb", from_user=FakeUser(7, "Alice"))
    await inline_handlers.inline_query(FakeUpdate(inline_query=inline), FakeContext())
    assert inline.results[0].title == "Liquid Business"
