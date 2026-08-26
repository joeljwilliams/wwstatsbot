"""Postgres data layer — the raw SQL, the cache, and full-text search.

These need a real Postgres and are skipped unless TEST_DATABASE_URL is set. CI runs
postgres:18 to match Railway; version parity matters here specifically because what is
being pinned is text-search behaviour (tsvector generation, the 'english' stemmer,
ts_rank ordering), which is server-side and version-sensitive.

    docker run -d -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest

The FTS tests below encode two failures the schema comments record:

* the name-initialism column must use the **same** 'english' config as the query, or
  words the stemmer rewrites (a trailing y -> i: "dygy" indexed, "dygi" queried) stop
  matching themselves;
* `ts_rank` alone cannot tell a real hit from a stemmer collision — 'english' folds both
  "busy" and "business" to the lexeme "busi" — so the ILIKE boost in the ORDER BY is what
  keeps a genuine full-word match on top;
* apostrophes must be stripped *before* words are reduced to initials, or a contraction
  reads as two words ("Should've" -> S and v, giving "SvSS" instead of "SSS").

And one about the schema itself: `search_tsv` is dropped and recreated on every
`ensure_schema()`, because `ADD COLUMN IF NOT EXISTS` silently skips an existing column and
let the expression in the code diverge from the one in the database.
"""

import os

import pytest

import db
from achvlist import ACHV

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# CI sets REQUIRE_POSTGRES=1 so a missing database is a failure rather than a skip.
# Without it, a broken service container or a mistyped URL leaves the whole data layer
# unexercised while the build still reports green — the tests would simply skip. Locally
# the default stays "skip", so `uv run pytest` needs no infrastructure.
REQUIRE_POSTGRES = os.environ.get("REQUIRE_POSTGRES") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DATABASE_URL and not REQUIRE_POSTGRES,
        reason="set TEST_DATABASE_URL to run the Postgres data-layer tests",
    ),
]


def test_database_url_is_configured():
    """Guard the guard: with REQUIRE_POSTGRES set, an absent URL must fail loudly."""
    assert TEST_DATABASE_URL, (
        "REQUIRE_POSTGRES=1 but TEST_DATABASE_URL is unset — the Postgres service is "
        "not reachable, so the data-layer tests below would have silently skipped."
    )


@pytest.fixture
async def pool():
    """A clean schema per test: the tables are dropped, recreated and seeded.

    Dropping is safe because this only ever runs against TEST_DATABASE_URL, never the
    DATABASE_URL the bot uses.
    """
    await db.init_pool(TEST_DATABASE_URL)
    async with db._pool.acquire() as conn:
        # achievement_rules holds a foreign key onto achievements, so it has to be named
        # here too: dropping achievements alone fails while a dependent table exists.
        await conn.execute("DROP TABLE IF EXISTS achievement_rules, achievements, admins")
    await db.ensure_schema()
    yield db._pool
    async with db._pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS achievement_rules, achievements, admins")
    await db.close_pool()


@pytest.fixture
async def seeded(pool):
    await db.seed_achievements()
    await db.load_cache()
    return pool


# --- Schema ----------------------------------------------------------------------


async def test_ensure_schema_is_idempotent(pool):
    """It runs on every startup, so a second call must not raise."""
    await db.ensure_schema()
    await db.ensure_schema()


async def test_search_column_and_index_exist(pool):
    async with pool.acquire() as conn:
        column = await conn.fetchval(
            "SELECT is_generated FROM information_schema.columns "
            "WHERE table_name = 'achievements' AND column_name = 'search_tsv'"
        )
        index = await conn.fetchval("SELECT indexname FROM pg_indexes WHERE indexname = 'achievements_search_tsv_idx'")
    assert column == "ALWAYS", "search_tsv must be a generated STORED column"
    assert index == "achievements_search_tsv_idx"


# --- Seeding ---------------------------------------------------------------------


async def test_seed_inserts_the_whole_list(pool):
    await db.seed_achievements()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM achievements")
    assert count == len(ACHV)


async def test_seed_never_clobbers_an_edited_row(seeded):
    """ON CONFLICT DO NOTHING: a restart must not undo an admin's /setnote."""
    await db.update_notes("Welcome to Hell", "\N{MEMO} admin edit")
    await db.seed_achievements()  # simulate a redeploy
    await db.load_cache()

    entry = next(a for a in db.get_achievements() if a["name"] == "Welcome to Hell")
    assert entry["notes"] == "\N{MEMO} admin edit"


async def test_seed_is_idempotent(pool):
    await db.seed_achievements()
    await db.seed_achievements()
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM achievements") == len(ACHV)


# --- The in-memory cache ---------------------------------------------------------


async def test_cache_preserves_the_legacy_achv_shape(seeded):
    """Handler code reads 'desc' and uses .get() on the flags, which are present only
    when true. Anything else silently breaks every consumer."""
    cache = db.get_achievements()
    assert cache, "the cache should not be empty after load_cache()"

    for entry in cache:
        assert set(entry) >= {"name", "desc", "type", "notes"}
        assert "description" not in entry
        # Flags are omitted when false, never set to False.
        assert entry.get("inactive") in (None, True)
        assert entry.get("not_via_playing") in (None, True)


async def test_cache_flags_are_set_for_the_rows_that_have_them(seeded):
    cache = {a["name"]: a for a in db.get_achievements()}
    assert cache["Explorer"]["inactive"] is True
    assert "not_via_playing" not in cache["Explorer"]
    assert cache["Here's Johnny!"]["not_via_playing"] is True
    assert "inactive" not in cache["Welcome to Hell"]


async def test_cache_is_ordered_by_sort_order(seeded):
    """The seed order is the display order for /achv."""
    names = [a["name"] for a in db.get_achievements()]
    assert names[0] == ACHV[0]["name"]
    assert names == [a["name"] for a in ACHV]


async def test_update_notes_refreshes_the_cache(seeded):
    """The synchronous cache is the read path, so a write must reload it."""
    assert await db.update_notes("Welcome to Hell", "\N{MEMO} fresh") is True
    entry = next(a for a in db.get_achievements() if a["name"] == "Welcome to Hell")
    assert entry["notes"] == "\N{MEMO} fresh"


async def test_update_notes_reports_a_miss(seeded):
    assert await db.update_notes("No Such Achievement", "x") is False


# --- Full-text search -------------------------------------------------------------


async def test_search_matches_a_name(seeded):
    names = [a["name"] for a in await db.search_achievements("Welcome to Hell")]
    assert "Welcome to Hell" in names


async def test_search_ranks_name_hits_above_description_hits(seeded):
    """Callers treat results[0] as "the answer", so the ordering is the contract."""
    results = await db.search_achievements("Dedicated")
    assert results[0]["name"] == "Dedicated"


async def test_search_prefix_matches_for_as_you_type(seeded):
    """Inline queries search on every keystroke, so lexemes are prefix-matched."""
    names = [a["name"] for a in await db.search_achievements("Enochlo")]
    assert "Enochlophobia" in names


async def test_search_matches_a_name_initialism(seeded):
    """The weight-B column: "wth" should find "Welcome to Hell"."""
    names = [a["name"] for a in await db.search_achievements("wth")]
    assert "Welcome to Hell" in names


@pytest.mark.parametrize(
    "query, expected",
    [
        # The exact case the schema comment records: "Did you guard yourself?" has the
        # initialism "Dygy", which the english stemmer rewrites to "dygi". Indexing with
        # 'simple' while querying with 'english' made this term fail to match itself.
        ("dygy", "Did you guard yourself?"),
        ("gcfy", "Good Choice... For You"),
    ],
)
async def test_initialism_survives_the_english_stemmer(seeded, query, expected):
    """Both sides of the search must use the same text-search config.

    A trailing y -> i rewrite is the visible symptom; the underlying rule is that
    search_tsv and the tsquery have to agree on stemming or terms silently disappear.
    """
    names = [a["name"] for a in await db.search_achievements(query)]
    assert expected in names


async def test_exact_text_beats_a_stemmer_collision(seeded):
    """'english' folds "busy" and "business" to the same lexeme "busi", so ts_rank
    alone would rank "Busy Night" alongside "Liquid Business". The ILIKE boost in the
    ORDER BY is what puts the genuine full-word match first."""
    results = await db.search_achievements("business")
    assert results, "expected at least one match for 'business'"
    assert results[0]["name"] == "Liquid Business"


async def test_search_tolerates_punctuation_in_the_query(seeded):
    """Names contain punctuation ("O HAI DER!", "Spy vs Spy"). Building the tsquery
    from lexemes rather than raw input means this can never be invalid syntax."""
    for query in ["O HAI DER!", "Spy vs Spy", "I've Got Your Back", "!!!", "a & b", "'"]:
        await db.search_achievements(query)  # must not raise


async def test_search_returns_nothing_for_a_stopword_only_query(seeded):
    """to_tsvector('english', 'the of') is empty, so tsq is NULL and nothing matches.
    build_info_results() is what falls back to a substring scan in this case."""
    assert await db.search_achievements("the of and") == []


async def test_search_returns_the_cache_entry_shape(seeded):
    for entry in await db.search_achievements("Welcome"):
        assert set(entry) >= {"name", "desc", "type", "notes"}
        assert "description" not in entry
        assert entry.get("inactive") in (None, True)


async def test_search_reflects_an_edited_note_without_a_reindex(seeded):
    """search_tsv is a generated STORED column, so Postgres recomputes it on write —
    there is no trigger and nothing to keep in sync by hand."""
    await db.update_notes("Welcome to Hell", "\N{MEMO} zzunlikelytoken")
    # Notes aren't indexed (only name/initialism/description are), so this asserts the
    # write path stayed healthy rather than that notes became searchable.
    names = [a["name"] for a in await db.search_achievements("Welcome to Hell")]
    assert "Welcome to Hell" in names


# --- Admins ----------------------------------------------------------------------


async def test_admin_lifecycle(pool):
    assert await db.is_admin(1) is False
    await db.add_admin(1, "alice", "Alice", added_by=999)
    assert await db.is_admin(1) is True

    rows = await db.list_admins()
    assert [(r["user_id"], r["username"]) for r in rows] == [(1, "alice")]

    assert await db.remove_admin(1) is True
    assert await db.is_admin(1) is False
    assert await db.remove_admin(1) is False  # already gone


async def test_add_admin_is_an_upsert(pool):
    await db.add_admin(1, "old", "Old Name", added_by=999)
    await db.add_admin(1, "new", "New Name", added_by=888)

    rows = await db.list_admins()
    assert len(rows) == 1
    assert rows[0]["username"] == "new"
    assert rows[0]["first_name"] == "New Name"


async def test_admins_are_listed_in_creation_order(pool):
    await db.add_admin(1, "first", "First", added_by=999)
    await db.add_admin(2, "second", "Second", added_by=999)
    assert [r["user_id"] for r in await db.list_admins()] == [1, 2]


# --- Raw SQL console --------------------------------------------------------------


async def test_run_sql_returns_columns_rows_and_status_for_a_select(seeded):
    columns, rows, status = await db.run_sql("SELECT name FROM achievements LIMIT 2")
    assert columns == ["name"]
    assert len(rows) == 2
    assert status.startswith("SELECT")


async def test_run_sql_returns_a_status_tag_for_a_write(seeded):
    columns, rows, status = await db.run_sql("UPDATE achievements SET notes = '' WHERE name = 'Welcome to Hell'")
    assert columns == []
    assert rows == []
    assert status == "UPDATE 1"


async def test_run_sql_propagates_errors_to_the_caller(pool):
    """db_console_cmd catches these and reports them; run_sql must not swallow them."""
    import asyncpg

    with pytest.raises(asyncpg.exceptions.UndefinedTableError):
        await db.run_sql("SELECT * FROM definitely_not_a_table")


# --- Initialisms and contractions -------------------------------------------------

# Every achievement whose name contains an apostrophe, with the initialism a user would
# actually type. Before apostrophes were stripped first, each of these indexed a spurious
# extra letter from the contraction suffix and none of them could be found this way.
CONTRACTION_INITIALISMS = [
    ("sss", "Should've Said Something"),
    ("igyb", "I've Got Your Back"),
    ("hj", "Here's Johnny!"),
    ("twydsh", "That's Why You Don't Stay Home"),
    ("ap", "Alzheimer's Patient"),
    ("ihniwid", "I Have No Idea What I'm Doing"),
    ("indb", "I'M NOT DRUN-- *BURPPP*"),
    ("nib", "Now I'm Blind"),
    ("ts", "Today's Special!"),
]


@pytest.mark.parametrize("query, expected", CONTRACTION_INITIALISMS)
async def test_initialism_ignores_apostrophes(seeded, query, expected):
    r"""The reported bug: "SSS" must find "Should've Said Something".

    \w does not match an apostrophe, so reducing words to initials without stripping it
    first treated the contraction suffix as its own word — "Should've" contributed both S
    and v, indexing "SvSS". Only "SVSS" matched, which no user would type.
    """
    names = [a["name"] for a in await db.search_achievements(query)]
    assert expected in names, "{!r} did not find {!r}".format(query, expected)


async def test_the_old_buggy_initialism_no_longer_matches(seeded):
    """ "SVSS" was the only spelling that worked; it should not linger as an alias."""
    names = [a["name"] for a in await db.search_achievements("svss")]
    assert "Should've Said Something" not in names


async def test_stripping_apostrophes_does_not_break_other_initialisms(seeded):
    """Names without contractions must be unaffected, including the two the stemmer
    rewrites and one where a lowercase word legitimately contributes a letter."""
    for query, expected in [
        ("wth", "Welcome to Hell"),
        ("dygy", "Did you guard yourself?"),
        ("gcfy", "Good Choice... For You"),
        ("svs", "Spy vs Spy"),
        ("ohd", "O HAI DER!"),
    ]:
        names = [a["name"] for a in await db.search_achievements(query)]
        assert expected in names, "{!r} no longer finds {!r}".format(query, expected)


# --- The schema must not silently diverge from the code ---------------------------

# The generated-column definition as it shipped before the apostrophe fix. Used to build a
# deliberately stale database and prove ensure_schema() repairs it.
_STALE_SEARCH_COLUMN = """
ALTER TABLE achievements
    ADD COLUMN search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
        setweight(
            to_tsvector('english',
                regexp_replace(
                    regexp_replace(coalesce(name, ''), '(\\w)\\w*', '\\1', 'g'),
                    '[^a-zA-Z0-9]', '', 'g')),
            'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')
    ) STORED;
CREATE INDEX achievements_search_tsv_idx ON achievements USING GIN (search_tsv);
"""


async def test_ensure_schema_rebuilds_a_stale_search_column(pool):
    """The migration this fix needed, and the case the `pool` fixture cannot express.

    Every other test here starts from dropped tables, so it always sees the current
    expression — which is precisely why a naive fix would have passed the whole suite and
    changed nothing in production. This test instead builds the *old* column on a table
    that is not dropped, mirroring a live database, and asserts ensure_schema() replaces
    it rather than skipping it as ADD COLUMN IF NOT EXISTS used to.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS achievements_search_tsv_idx")
        await conn.execute("ALTER TABLE achievements DROP COLUMN search_tsv")
        await conn.execute(_STALE_SEARCH_COLUMN)
    await db.seed_achievements()

    # Confirm the stale definition really is stale, or the test proves nothing.
    stale = [a["name"] for a in await db.search_achievements("sss")]
    assert "Should've Said Something" not in stale, (
        "the stale column already behaves correctly — this test is no longer meaningful"
    )

    await db.ensure_schema()

    fixed = [a["name"] for a in await db.search_achievements("sss")]
    assert "Should've Said Something" in fixed, "ensure_schema() did not rebuild the column"


async def test_the_search_index_survives_the_rebuild(seeded):
    """Dropping the column drops its index; ensure_schema must put it back, or every
    search silently degrades to a sequential scan."""
    await db.ensure_schema()
    async with db._pool.acquire() as conn:
        index = await conn.fetchval("SELECT indexname FROM pg_indexes WHERE indexname = 'achievements_search_tsv_idx'")
    assert index == "achievements_search_tsv_idx"


async def test_ensure_schema_is_idempotent_over_repeated_startups(seeded):
    """It now performs real DDL on every call, so "safe to run repeatedly" is a stronger
    claim than before: notes must survive, and search must keep working."""
    await db.update_notes("Welcome to Hell", "\N{MEMO} keep me")
    for _ in range(3):
        await db.ensure_schema()
    await db.load_cache()

    entry = next(a for a in db.get_achievements() if a["name"] == "Welcome to Hell")
    assert entry["notes"] == "\N{MEMO} keep me", "a rebuild must not touch source data"
    names = [a["name"] for a in await db.search_achievements("sss")]
    assert "Should've Said Something" in names


# --- Feasibility rules -----------------------------------------------------------
#
# The rules table exists to be *both* deployable and editable, and those pull in opposite
# directions: a deploy must be able to correct a wrong rule everywhere, while a rule
# corrected live with /setrule must survive the next release. `ON CONFLICT DO UPDATE ...
# WHERE edited = FALSE` is the whole mechanism, so these tests pin both halves of it.


@pytest.fixture
async def ruled(seeded):
    await db.seed_rules()
    await db.load_rules_cache()
    return seeded


async def test_seed_rules_populates_every_achievement(ruled):
    from rulelist import RULES

    assert len(db.get_rules()) == len(RULES) == len(ACHV)


async def test_seed_rules_is_idempotent(ruled):
    """It runs on every startup, so a second pass must not duplicate or raise."""
    await db.seed_rules()
    await db.seed_rules()
    await db.load_rules_cache()
    assert len(db.get_rules()) == len(ACHV)


async def test_a_deploy_updates_a_rule_nobody_has_edited(ruled):
    """The half that DO NOTHING could not do: a corrected rule has to reach a live database."""
    async with db._pool.acquire() as conn:
        await conn.execute("UPDATE achievement_rules SET expr = 'False' WHERE achievement = 'Cold as Ice'")
    await db.seed_rules()
    await db.load_rules_cache()
    assert db.get_rules()["Cold as Ice"]["expr"] == "ispresent('harlot')"


async def test_a_deploy_leaves_a_hand_edited_rule_alone(ruled):
    """A rule fixed mid-game must not be silently reverted by the next release."""
    await db.update_rule("Cold as Ice", "check", "snow_wolf", "ispresent('cupid')", "edited live")
    await db.seed_rules()
    await db.load_rules_cache()

    rule = db.get_rules()["Cold as Ice"]
    assert rule["expr"] == "ispresent('cupid')"
    assert rule["edited"] is True


async def test_resetting_a_rule_lets_the_next_deploy_restore_it(ruled):
    await db.update_rule("Cold as Ice", "check", "snow_wolf", "ispresent('cupid')", "edited live")
    assert await db.reset_rule("Cold as Ice")
    await db.seed_rules()
    await db.load_rules_cache()

    rule = db.get_rules()["Cold as Ice"]
    assert rule["expr"] == "ispresent('harlot')"
    assert rule["edited"] is False


async def test_update_rule_reports_an_unknown_achievement(ruled):
    assert await db.update_rule("No Such Achievement", "check", "any", "True", "") is False
    assert await db.reset_rule("No Such Achievement") is False


async def test_rules_come_back_in_achievement_order(ruled):
    """Rendered lists follow /achievements' order, not the alphabet or insertion order."""
    assert list(db.get_rules()) == [a["name"] for a in ACHV]


async def test_a_rule_cannot_outlive_its_achievement(ruled):
    """ON DELETE CASCADE: an orphaned rule would block the delete or linger unreachable."""
    async with db._pool.acquire() as conn:
        await conn.execute("DELETE FROM achievements WHERE name = 'Cold as Ice'")
        remaining = await conn.fetchval("SELECT count(*) FROM achievement_rules WHERE achievement = 'Cold as Ice'")
    assert remaining == 0


async def test_a_rename_carries_the_rule_with_it(ruled):
    """ON UPDATE CASCADE: /db can rename an achievement without orphaning its rule."""
    async with db._pool.acquire() as conn:
        await conn.execute("UPDATE achievements SET name = 'Cold as Ice 2' WHERE name = 'Cold as Ice'")
        expr = await conn.fetchval("SELECT expr FROM achievement_rules WHERE achievement = 'Cold as Ice 2'")
    assert expr == "ispresent('harlot')"
