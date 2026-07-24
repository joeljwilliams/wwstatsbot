"""Postgres data layer for wwstatsbot (asyncpg + raw SQL).

Holds the connection pool, the achievement/admin SQL, and an in-memory cache of
the achievements table. The achievement list is small and read on hot paths
(inline queries, /info, /achv), so it is loaded into memory once at startup and
refreshed after every edit; callers read it synchronously via get_achievements().
"""

import logging

import asyncpg

from achvlist import ACHV

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool = None

# In-memory cache of the achievements table, ordered by sort_order. Each item is
# a dict shaped like the old achvlist.ACHV entries (name/desc/type/notes + flags).
_ACHIEVEMENTS = []

_SCHEMA = """
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
"""


async def init_pool(dsn):
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    logger.info("Postgres pool created")


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def ensure_schema():
    async with _pool.acquire() as conn:
        await conn.execute(_SCHEMA)
    logger.info("Schema ensured")


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
    logger.info("Achievements seeded (table now has %s rows)", count)


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
    logger.info("Achievement cache loaded (%d entries)", len(_ACHIEVEMENTS))


def get_achievements():
    """Return the cached achievement list (synchronous, hot-path accessor)."""
    return _ACHIEVEMENTS


async def update_notes(name, notes):
    """Set the notes for an achievement by exact name. Returns True if a row matched."""
    async with _pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE achievements SET notes = $2 WHERE name = $1", name, notes
        )
    matched = result != "UPDATE 0"
    if matched:
        await load_cache()
    return matched


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
            user_id, username, first_name, added_by,
        )


async def remove_admin(user_id):
    """Remove an admin. Returns True if a row was deleted."""
    async with _pool.acquire() as conn:
        result = await conn.execute("DELETE FROM admins WHERE user_id = $1", user_id)
    return result != "DELETE 0"


async def list_admins():
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, username, first_name FROM admins ORDER BY created_at"
        )


async def _scalar(query, *args):
    async with _pool.acquire() as conn:
        return await conn.fetchval(query, *args)
