"""Postgres data layer for wwstatsbot (asyncpg + raw SQL).

Holds the connection pool, the achievement/admin SQL, and an in-memory cache of
the achievements table. The achievement list is small and read on hot paths
(inline queries, /info, /achv), so it is loaded into memory once at startup and
refreshed after every edit; callers read it synchronously via get_achievements().
"""

import asyncpg
import structlog

from achvlist import ACHV

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool = None

# In-memory cache of the achievements table, ordered by sort_order. Each item is
# a dict shaped like the old achvlist.ACHV entries (name/desc/type/notes + flags).
_ACHIEVEMENTS = []

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS achievements (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL DEFAULT 'instantaneous',
    notes           TEXT NOT NULL DEFAULT '',
    inactive        BOOLEAN NOT NULL DEFAULT FALSE,
    not_via_playing BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admins (
    user_id     BIGINT PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    added_by    BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Full-text search vector over achievements. A generated STORED column, so
-- Postgres recomputes it automatically on every insert/update (e.g. note edits)
-- with no trigger. Weighted name (A) > name-initialism (B) > description (C).
--
-- The column is DROPPED AND RECREATED on every startup rather than added with
-- ADD COLUMN IF NOT EXISTS. That looks wasteful and is deliberate. IF NOT EXISTS
-- silently *skips* an existing column, so editing the expression below changed nothing
-- on a live database — while every test passed, because the test fixture drops the
-- tables and therefore always saw the new definition. A derived column that can
-- silently disagree with the code defining it generates exactly this class of bug: the
-- initialism search shipped broken for months (see the apostrophe note below).
--
-- Rebuilding is safe and cheap. The column is GENERATED, so it holds no source data --
-- nothing can be lost. The table is a fixed catalogue of ~110 rows, and the drop, add
-- and reindex measure ~4ms in total. Startup does this once, before readiness.
ALTER TABLE achievements DROP COLUMN IF EXISTS search_tsv;
ALTER TABLE achievements
    ADD COLUMN search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
        -- Same 'english' config as the query (search_achievements) so the
        -- initialism is stemmed identically on both sides. Using 'simple' here
        -- broke queries whose initialism the english stemmer rewrites (e.g. a
        -- trailing y -> i: "dygy" indexed as dygy, queried as dygi).
        setweight(
            to_tsvector('english',
                -- Apostrophes are stripped FIRST, before words are reduced to initials.
                -- \w does not match an apostrophe, so "Should've" would otherwise read as
                -- two words and contribute both S and v: the initialism came out "SvSS",
                -- meaning a user typing the obvious "SSS" matched nothing. 9 of 109 names
                -- contain a contraction and were all affected the same way.
                regexp_replace(
                    regexp_replace(
                        regexp_replace(coalesce(name, ''), '[''’]', '', 'g'),
                        '(\w)\w*', '\1', 'g'),
                    '[^a-zA-Z0-9]', '', 'g')),
            'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')
    ) STORED;

-- Dropping the column above drops this index with it, so it is always recreated.
CREATE INDEX IF NOT EXISTS achievements_search_tsv_idx
    ON achievements USING GIN (search_tsv);
"""


async def init_pool(dsn):
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    logger.info("postgres_pool_created", min_size=1, max_size=5)


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def ensure_schema():
    async with _pool.acquire() as conn:
        await conn.execute(_SCHEMA)
    logger.info("schema_ensured")


async def seed_achievements():
    """Idempotently seed the achievements table from achvlist.ACHV.

    ON CONFLICT (name) DO NOTHING means restarts never clobber values that admins
    have edited since the initial seed.
    """
    async with _pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO achievements
                (name, description, type, notes, inactive, not_via_playing, sort_order)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (name) DO NOTHING
            """,
            [
                (
                    a["name"],
                    a.get("desc", ""),
                    a.get("type", "instantaneous"),
                    a.get("notes", ""),
                    "inactive" in a,
                    "not_via_playing" in a,
                    i,
                )
                for i, a in enumerate(ACHV)
            ],
        )
    count = await _scalar("SELECT count(*) FROM achievements")
    logger.info("achievements_seeded", rows=count)


async def load_cache():
    """Reload the in-memory achievement cache from the database."""
    global _ACHIEVEMENTS
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT name, description, type, notes, inactive, not_via_playing
            FROM achievements
            ORDER BY sort_order, id
            """
        )
    achievements = []
    for r in rows:
        entry = {
            "name": r["name"],
            "desc": r["description"],
            "type": r["type"],
            "notes": r["notes"],
        }
        # Preserve the old ACHV shape: flags present only when true.
        if r["inactive"]:
            entry["inactive"] = True
        if r["not_via_playing"]:
            entry["not_via_playing"] = True
        achievements.append(entry)
    _ACHIEVEMENTS = achievements
    logger.info("achievement_cache_loaded", entries=len(_ACHIEVEMENTS))


def get_achievements():
    """Return the cached achievement list (synchronous, hot-path accessor)."""
    return _ACHIEVEMENTS


async def update_notes(name, notes):
    """Set the notes for an achievement by exact name. Returns True if a row matched."""
    async with _pool.acquire() as conn:
        result = await conn.execute("UPDATE achievements SET notes = $2 WHERE name = $1", name, notes)
    matched = result != "UPDATE 0"
    if matched:
        await load_cache()
    return matched


# --- Search -----------------------------------------------------------------

# Full-text search. The query text is run through to_tsvector with the SAME
# config as the indexed column, then each resulting lexeme is prefix-matched
# (:*) so inline as-you-type works. Building the tsquery from lexemes (rather
# than raw input) means punctuation in names ("O HAI DER!", "Spy vs Spy") can
# never produce invalid tsquery syntax and needs no Python-side escaping.
_SEARCH_SQL = """
WITH q AS (
    SELECT to_tsquery('english', string_agg(lexeme || ':*', ' & ')) AS tsq
    FROM unnest(to_tsvector('english', $1))
)
SELECT a.name, a.description, a.type, a.notes, a.inactive, a.not_via_playing
FROM achievements a, q
WHERE q.tsq IS NOT NULL
  AND a.search_tsv @@ q.tsq
ORDER BY
    -- Exact match on the raw, un-stemmed text wins first. ts_rank can't tell a
    -- real hit from a stemmer collision (the 'english' config folds both "busy"
    -- and "business" to the lexeme "busi"), so searching "business" would rank
    -- "Busy Night" alongside "Liquid Business". Boosting rows whose literal text
    -- contains the query pushes those genuine full-word matches to the top.
    (a.name ILIKE '%' || $1 || '%' OR a.description ILIKE '%' || $1 || '%') DESC,
    ts_rank(a.search_tsv, q.tsq) DESC, a.sort_order, a.id
"""


async def search_achievements(query):
    """Full-text search: name (A) / name-initialism (B) / description (C).

    Prefix-matches each lexeme (as-you-type) and ranks by ts_rank so name hits
    sort first. Returns dicts shaped like get_achievements() entries.
    """
    async with _pool.acquire() as conn:
        rows = await conn.fetch(_SEARCH_SQL, query)
    results = []
    for r in rows:
        entry = {
            "name": r["name"],
            "desc": r["description"],
            "type": r["type"],
            "notes": r["notes"],
        }
        if r["inactive"]:
            entry["inactive"] = True
        if r["not_via_playing"]:
            entry["not_via_playing"] = True
        results.append(entry)
    return results


# --- Admins ----------------------------------------------------------------


async def is_admin(user_id):
    return await _scalar("SELECT 1 FROM admins WHERE user_id = $1", user_id) is not None


async def add_admin(user_id, username, first_name, added_by):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO admins (user_id, username, first_name, added_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE
                SET username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    added_by = EXCLUDED.added_by
            """,
            user_id,
            username,
            first_name,
            added_by,
        )


async def remove_admin(user_id):
    """Remove an admin. Returns True if a row was deleted."""
    async with _pool.acquire() as conn:
        result = await conn.execute("DELETE FROM admins WHERE user_id = $1", user_id)
    return result != "DELETE 0"


async def list_admins():
    async with _pool.acquire() as conn:
        return await conn.fetch("SELECT user_id, username, first_name FROM admins ORDER BY created_at")


async def _scalar(query, *args):
    async with _pool.acquire() as conn:
        return await conn.fetchval(query, *args)


# --- Raw SQL console (superuser only) --------------------------------------


async def run_sql(sql):
    """Execute an arbitrary single SQL statement and return (columns, rows, status).

    Prepared so we get both the result set (for SELECT/RETURNING) and the command
    status tag (e.g. "UPDATE 3", "CREATE INDEX") from one execution. Callers must
    gate this behind the superuser check — it runs whatever SQL it's given.
    """
    async with _pool.acquire() as conn:
        stmt = await conn.prepare(sql)
        columns = [a.name for a in stmt.get_attributes()]
        rows = await stmt.fetch()
        status = stmt.get_statusmsg()
    return columns, [tuple(r) for r in rows], status
