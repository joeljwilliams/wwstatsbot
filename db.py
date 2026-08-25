"""Postgres data layer for wwstatsbot (asyncpg + raw SQL).

Holds the connection pool, the achievement/admin SQL, and an in-memory cache of
the achievements table. The achievement list is small and read on hot paths
(inline queries, /info, /achv), so it is loaded into memory once at startup and
refreshed after every edit; callers read it synchronously via get_achievements().
"""

import asyncpg
import structlog

from achvlist import ACHV
from rulelist import RULES

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool = None

# In-memory cache of the achievements table, ordered by sort_order. Each item is
# a dict shaped like the old achvlist.ACHV entries (name/desc/type/notes + flags).
_ACHIEVEMENTS = []

# In-memory cache of achievement_rules, keyed by achievement name. Read once per rendered
# player per achievement, so it gets the same treatment as _ACHIEVEMENTS: loaded at
# startup, reloaded after every write.
_RULES = {}

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

-- Feasibility rules: which achievements a given role composition can still produce, and
-- for whom. Deliberately its own table rather than columns on `achievements`, for one
-- decisive reason: seed_achievements() is ON CONFLICT DO NOTHING, so on any database that
-- has already been seeded -- which is every deployed one -- a rule shipped as a new
-- achievements column would never actually be written. A separate table gets its own
-- upsert semantics (see seed_rules) instead of inheriting ones designed to protect
-- hand-edited notes.
--
-- Keyed by achievement *name* because that is the only stable identifier: `id` is a
-- SERIAL and differs between databases. ON UPDATE CASCADE keeps a rule attached through a
-- rename via the /db console; ON DELETE CASCADE means removing an achievement takes its
-- rule with it rather than leaving an orphan that blocks the delete.
CREATE TABLE IF NOT EXISTS achievement_rules (
    achievement TEXT PRIMARY KEY
        REFERENCES achievements(name) ON UPDATE CASCADE ON DELETE CASCADE,
    -- check | maybe | always | skip -- see rulelist.TIERS.
    tier        TEXT NOT NULL,
    -- Who can earn it: 'any', role ids, 'tag:<tag>', 'team:<team>', comma-separated.
    subject     TEXT NOT NULL DEFAULT '',
    -- Boolean expression over the composition, evaluated in a sandbox.
    expr        TEXT NOT NULL DEFAULT 'True',
    note        TEXT NOT NULL DEFAULT '',
    -- Set by /setrule. Deploys skip edited rows, so a live correction is not undone by
    -- the next release; /resetrule clears it and the next startup restores the canonical
    -- rule.
    edited      BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
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


# --- Feasibility rules ------------------------------------------------------


async def seed_rules():
    """Idempotently seed achievement_rules from rulelist.RULES.

    Deliberately *not* the DO NOTHING used for achievements. Rules are code-shaped data:
    when a rule is found to be wrong, the fix has to reach every deployment, and DO NOTHING
    would mean the corrected rule never landed anywhere the old one already existed.

    `WHERE edited = FALSE` is what makes that safe to combine with live editing. A deploy
    refreshes every rule nobody has touched; a rule corrected mid-game with /setrule is
    left exactly as the admin left it, rather than being silently reverted by the next
    release. /resetrule clears the flag to opt a rule back into the canonical version.

    Must run after seed_achievements(): the foreign key means a rule for an achievement
    that does not exist yet is rejected. tests/test_rules.py pins both lists against each
    other so that mismatch is caught in CI rather than at a production startup.
    """
    async with _pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO achievement_rules (achievement, tier, subject, expr, note)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (achievement) DO UPDATE
                SET tier = EXCLUDED.tier,
                    subject = EXCLUDED.subject,
                    expr = EXCLUDED.expr,
                    note = EXCLUDED.note,
                    updated_at = now()
                WHERE achievement_rules.edited = FALSE
            """,
            [(r["name"], r["tier"], r["subject"], r["expr"], r["note"]) for r in RULES],
        )
    count = await _scalar("SELECT count(*) FROM achievement_rules")
    edited = await _scalar("SELECT count(*) FROM achievement_rules WHERE edited")
    logger.info("rules_seeded", rows=count, edited=edited)


async def load_rules_cache():
    """Reload the in-memory rule cache. Same contract as load_cache(): call after writes."""
    global _RULES
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.achievement, r.tier, r.subject, r.expr, r.note, r.edited
            FROM achievement_rules r
            JOIN achievements a ON a.name = r.achievement
            ORDER BY a.sort_order, a.id
            """
        )
    _RULES = {
        r["achievement"]: {
            "tier": r["tier"],
            "subject": r["subject"],
            "expr": r["expr"],
            "note": r["note"],
            "edited": r["edited"],
        }
        for r in rows
    }
    logger.info("rule_cache_loaded", entries=len(_RULES))


def get_rules():
    """The cached rules, keyed by achievement name (synchronous, hot-path accessor).

    Ordered by the achievements' own sort_order, so a rendered list comes out in the same
    order as /achievements rather than alphabetically or by insertion.
    """
    return _RULES


async def update_rule(achievement, tier, subject, expr, note):
    """Overwrite one rule and mark it hand-edited. Returns True if a row matched.

    The `edited` flag is the whole point: it opts this rule out of being overwritten by
    the next deploy's seed. Callers must be superuser-gated -- `expr` is evaluated at
    render time, so this is closer to /db than to /setnote.
    """
    async with _pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE achievement_rules
               SET tier = $2, subject = $3, expr = $4, note = $5,
                   edited = TRUE, updated_at = now()
             WHERE achievement = $1
            """,
            achievement,
            tier,
            subject,
            expr,
            note,
        )
    matched = result != "UPDATE 0"
    if matched:
        await load_rules_cache()
    return matched


async def reset_rule(achievement):
    """Clear the hand-edited flag so the next startup restores the canonical rule.

    Does not restore it here: seeding is what owns the canonical values, and having one
    place that writes them keeps "what will this be after a restart" answerable.
    """
    async with _pool.acquire() as conn:
        result = await conn.execute("UPDATE achievement_rules SET edited = FALSE WHERE achievement = $1", achievement)
    matched = result != "UPDATE 0"
    if matched:
        await load_rules_cache()
    return matched


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
