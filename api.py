"""Client for the public tgwerewolf.com stats API.

Every player statistic the bot reports comes from here. The API is read-only, unauthenticated
and keyed by Telegram user id (`pid`), which is why plain `@username` mentions can never be
looked up — they carry no id.

One `httpx.AsyncClient` is shared by every handler rather than created per request, so
connections are pooled across the bot's lifetime. It is created at import (inert until used)
and closed from main's post_shutdown hook via close().

Consumers must reference the client through the module — ``api.client`` — never
``from api import client``. A `from` import binds the object at import time, so the test
suite's MockTransport swap would not be seen by code holding the original, and the suite
would start making real requests to tgwerewolf.com while still passing.
"""

import httpx

BASE = "https://www.tgwerewolf.com/Stats"

# Shared async HTTP client, reused across all handlers. Created at import, closed on
# shutdown (see close(), called from main's post_shutdown hook).
client = httpx.AsyncClient(timeout=15)


async def _get(path, user_id):
    """GET one stats endpoint for a player and return the decoded JSON.

    Every endpoint takes the same two query parameters, and `json=true` is not optional:
    without it the API serves an HTML page and the decode fails somewhere downstream,
    nowhere near the call that omitted it. Putting it in one place means it cannot be
    forgotten when an endpoint is added.

    `client` is looked up at call time rather than bound as a default, so the test suite's
    MockTransport swap is seen.
    """
    r = await client.get(BASE + path, params={"pid": user_id, "json": "true"})
    return r.json()


async def get_stats(user_id):
    return await _get("/PlayerStats/", user_id)


async def get_kills(user_id):
    return await _get("/PlayerKills/", user_id)


async def get_killed_by(user_id):
    return await _get("/PlayerKilledBy/", user_id)


async def get_deaths(user_id):
    return await _get("/PlayerDeaths/", user_id)


async def get_achievements(user_id):
    return await _get("/PlayerAchievements/", user_id)


async def get_achievement_count(user_id):
    """The one fetcher that isn't a passthrough: /stats needs only the total."""
    return len(await get_achievements(user_id))


async def close():
    """Release the shared connection pool. Called from main's post_shutdown hook."""
    await client.aclose()
