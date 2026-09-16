"""What keeps the container awake.

Railway's Serverless mode stops a container after ~5 minutes with no *outbound* traffic
and wakes it on the next inbound request, so the flag on the service does nothing unless
the bot can actually fall silent. Two things guaranteed it never did, and each is pinned
by a test: the Postgres pool holding a connection open (here), and PTB's persistence loop
writing to Redis every 60 seconds whether or not anything changed
(`tests/test_persistence.py`).

Nothing else would catch a regression. An idle bot that chatters looks exactly like an
idle bot that does not — it just never sleeps, and the only symptom is a bill.

These need no database: what is under test is the arguments the pool is built with, not
the pool.
"""

import asyncpg
import pytest

from wwstatsbot.data import db

# Railway's threshold, and the trap. asyncpg's own default for
# max_inactive_connection_lifetime is this same 300s, so with the default the pool fell
# quiet at exactly the moment the window would otherwise have closed — the five minutes
# never started counting, and the service stayed awake for ever.
RAILWAY_SLEEP_SECONDS = 300


@pytest.fixture
def captured_pool_args(monkeypatch):
    """Build the pool against a fake asyncpg and hand back the keyword arguments."""
    captured = {}

    async def fake_create_pool(dsn, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    yield captured
    db._pool = None


async def test_idle_connections_are_dropped_well_inside_the_sleep_window(captured_pool_args):
    await db.init_pool("postgresql://user:pass@localhost/db")

    idle = captured_pool_args["max_inactive_connection_lifetime"]
    assert idle < RAILWAY_SLEEP_SECONDS, (
        "an idle Postgres connection is outbound traffic waiting to happen; at or above "
        "the sleep threshold the pool goes quiet only once the window has already passed"
    )
    # Headroom, not just inequality: the quiet five minutes can only start once the last
    # connection has gone, so a value just under the threshold would double the time to
    # sleep rather than enable it.
    assert idle <= RAILWAY_SLEEP_SECONDS / 2


async def test_the_pool_holds_nothing_open_when_nothing_is_using_it(captured_pool_args):
    await db.init_pool("postgresql://user:pass@localhost/db")

    assert captured_pool_args["min_size"] == 0


async def test_the_pool_can_still_serve_concurrent_work(captured_pool_args):
    """min_size=0 is about idling, not about capacity — /schall fans out."""
    await db.init_pool("postgresql://user:pass@localhost/db")

    assert captured_pool_args["max_size"] >= 5
