"""RedisPersistence — durable bot_data for PTB, backed by fakeredis.

Two behaviours are load-bearing and easy to break:

* **The "null" quirk.** DictPersistence's `*_json` properties emit the literal string
  "null" for empty state, but its constructor rejects "null" — it accepts only "" or a
  JSON object. Without `_clean`, a round-trip through Redis raises on startup.
* **Availability over durability.** A Redis outage must be logged and swallowed, never
  crash a handler; persistence silently degrades to in-memory until Redis returns.
* **Silence when nothing changed, and no socket left open.** PTB calls update_bot_data
  every `update_interval`
  whether or not anything changed. Writing regardless is a Redis round-trip a minute for
  the life of the process, which is outbound traffic, which is the only thing Railway
  looks at to decide a service is idle — so the bot could never be put to sleep. An
  unchanged blob must not reach Redis. Skipping the write is only half of it: Redis ships
  `tcp-keepalive 300` and `timeout 0`, so an idle connection is probed every 300 seconds
  and answered, which kept both ends awake over a socket neither was using. The connection
  has to go too. See tests/test_idle_quiet.py for the Postgres half.
"""

import asyncio
import json

import fakeredis
import pytest

import redis_persistence
from redis_persistence import RedisPersistence

KEY = "ptb:persistence:test"


@pytest.fixture
def fake_redis(monkeypatch):
    """Route both the sync (startup read) and async (writes) clients to one fake server.

    RedisPersistence deliberately uses a blocking client for its single startup read —
    no event loop exists at construction — so both have to be patched.
    """
    server = fakeredis.FakeServer()

    def sync_from_url(url, **kwargs):
        return fakeredis.FakeStrictRedis(server=server)

    def async_from_url(url, **kwargs):
        return fakeredis.FakeAsyncRedis(server=server)

    monkeypatch.setattr(redis_persistence.redis.Redis, "from_url", staticmethod(sync_from_url))
    monkeypatch.setattr(redis_persistence.aioredis.Redis, "from_url", staticmethod(async_from_url))
    return fakeredis.FakeStrictRedis(server=server)


# --- _clean ----------------------------------------------------------------------


def test_clean_maps_the_null_literal_to_empty_string():
    """The whole reason _clean exists: DictPersistence rejects "null"."""
    assert redis_persistence._clean("null") == ""


def test_clean_maps_missing_to_empty_string():
    assert redis_persistence._clean(None) == ""


def test_clean_passes_real_json_through():
    assert redis_persistence._clean('{"a": 1}') == '{"a": 1}'


# --- Round-trip ------------------------------------------------------------------


async def test_bot_data_survives_a_restart(fake_redis):
    persistence = RedisPersistence(url="redis://x", key=KEY)
    await persistence.update_bot_data({"allinfo": {"tok": ["Busy Night"]}})

    # A "restart": a brand-new instance reading the same key.
    revived = RedisPersistence(url="redis://x", key=KEY)
    assert await revived.get_bot_data() == {"allinfo": {"tok": ["Busy Night"]}}


async def test_empty_state_round_trips_without_raising(fake_redis):
    """This is the "null" path end to end — it used to blow up on construction.

    The write is carried by user_data so that bot_data is left untouched and still
    serializes to the literal "null" this is about. A bare flush() would no longer store
    anything at all: an unchanged blob is deliberately not written.
    """
    persistence = RedisPersistence(url="redis://x", key=KEY)
    await persistence.update_user_data(1, {"u": 1})
    await persistence.flush()

    stored = json.loads(fake_redis.get(KEY))
    assert stored["bot_data"] in (None, "null", "")

    revived = RedisPersistence(url="redis://x", key=KEY)
    assert await revived.get_bot_data() == {}


async def test_every_state_bucket_is_written(fake_redis):
    persistence = RedisPersistence(url="redis://x", key=KEY)
    await persistence.update_user_data(1, {"u": 1})
    await persistence.update_chat_data(-100, {"c": 2})
    await persistence.update_conversation("conv", (1, 1), "state")

    stored = json.loads(fake_redis.get(KEY))
    assert set(stored) == {"bot_data", "chat_data", "user_data", "callback_data", "conversations"}
    revived = RedisPersistence(url="redis://x", key=KEY)
    assert await revived.get_user_data() == {1: {"u": 1}}
    assert await revived.get_chat_data() == {-100: {"c": 2}}


async def test_writes_are_immediate_by_default(fake_redis):
    persistence = RedisPersistence(url="redis://x", key=KEY)
    await persistence.update_bot_data({"a": 1})
    assert fake_redis.get(KEY) is not None


async def test_on_flush_defers_writes_until_shutdown(fake_redis):
    persistence = RedisPersistence(url="redis://x", key=KEY, on_flush=True)
    await persistence.update_bot_data({"a": 1})
    assert fake_redis.get(KEY) is None  # nothing written yet
    await persistence.flush()
    assert fake_redis.get(KEY) is not None


# --- Failure handling favours availability ---------------------------------------


def test_a_load_failure_still_yields_a_working_instance(monkeypatch):
    """Redis down at startup: the bot must boot, just without prior state."""

    class Boom:
        def get(self, key):
            raise ConnectionError("redis is down")

        def close(self):
            pass

    monkeypatch.setattr(redis_persistence.redis.Redis, "from_url", staticmethod(lambda url, **kw: Boom()))
    monkeypatch.setattr(redis_persistence.aioredis.Redis, "from_url", staticmethod(lambda url, **kw: object()))

    persistence = RedisPersistence(url="redis://x", key=KEY)
    assert persistence is not None


async def test_a_save_failure_does_not_propagate(fake_redis, monkeypatch):
    """A Redis blip mid-handler must not surface as a failed command to the user."""
    persistence = RedisPersistence(url="redis://x", key=KEY)

    async def boom(*args, **kwargs):
        raise ConnectionError("redis went away")

    monkeypatch.setattr(persistence._redis, "set", boom)
    await persistence.update_bot_data({"a": 1})  # must not raise
    assert await persistence.get_bot_data() == {"a": 1}  # in-memory state still correct


# --- Writing only what changed ---------------------------------------------------


def _record_writes(persistence):
    """Record every Redis SET, leaving the write itself intact."""
    written = []
    original = persistence._redis.set

    async def recording(key, value):
        written.append(value)
        return await original(key, value)

    persistence._redis.set = recording
    return written


async def test_an_unchanged_update_is_never_written(fake_redis):
    """The write that stopped the bot ever being idle enough to sleep.

    PTB's persistence loop calls both of these every update_interval regardless of whether
    anything changed, so this is the path a bot nobody is talking to runs forever.
    """
    persistence = RedisPersistence(url="redis://x", key=KEY)
    await persistence.update_bot_data({"a": 1})
    await persistence.update_callback_data(([], {}))

    written = _record_writes(persistence)
    for _ in range(5):  # five turns of the loop, nothing changing
        await persistence.update_bot_data({"a": 1})
        await persistence.update_callback_data(([], {}))

    assert written == []


async def test_a_real_change_is_still_written_immediately(fake_redis):
    persistence = RedisPersistence(url="redis://x", key=KEY)
    await persistence.update_bot_data({"a": 1})

    written = _record_writes(persistence)
    await persistence.update_bot_data({"a": 2})

    assert len(written) == 1
    assert json.loads(json.loads(written[0])["bot_data"]) == {"a": 2}


async def test_a_bot_that_boots_and_is_never_spoken_to_falls_silent(fake_redis):
    """The shape that matters for sleeping: writes at startup, then nothing.

    Driven the way PTB drives it — the getters first, because get_bot_data() turns a None
    bot_data into {} and that counts as a change, which is why this is "at most one write"
    rather than none. What must not happen is a write on every turn of the loop: five
    minutes of silence is the whole requirement, and one write at boot does not spend it.
    """
    seed = RedisPersistence(url="redis://x", key=KEY)
    await seed.update_bot_data({"a": 1})

    revived = RedisPersistence(url="redis://x", key=KEY)
    await revived.get_bot_data()
    await revived.get_callback_data()

    written = _record_writes(revived)
    for _ in range(5):  # five turns of the loop, nobody talking to the bot
        await revived.update_bot_data({"a": 1})
        await revived.update_callback_data(await revived.get_callback_data())
    await revived.flush()

    assert len(written) <= 1, "an idle bot must not write once per interval"


async def test_a_failed_write_is_retried_rather_than_assumed(fake_redis):
    """_last_saved may only advance on a write that actually landed.

    Otherwise a blip would be recorded as success and the change — unchanged ever after —
    would never be written at all.
    """
    persistence = RedisPersistence(url="redis://x", key=KEY)

    async def boom(*args, **kwargs):
        raise ConnectionError("redis went away")

    persistence._redis.set = boom
    await persistence.update_bot_data({"a": 1})  # swallowed
    assert fake_redis.get(KEY) is None

    del persistence._redis.set  # Redis comes back
    await persistence.update_bot_data({"a": 1})  # same data, still never stored

    assert json.loads(json.loads(fake_redis.get(KEY))["bot_data"]) == {"a": 1}


async def test_an_idle_connection_is_dropped_rather_than_left_open(fake_redis):
    """Skipping the write is not enough — an idle socket is not a silent one.

    Redis probes an idle client every `tcp-keepalive` seconds (300 by default) and never
    hangs up first (`timeout 0`), and the bot answers every probe. Railway's sleep
    threshold is those same 5 minutes, so a connection nobody was using kept both
    containers awake indefinitely. Postgres was already sleeping precisely because the
    asyncpg pool lets its connections go.
    """
    persistence = RedisPersistence(url="redis://x", key=KEY, idle_disconnect=0.01)

    disconnected = []
    original = persistence._redis.connection_pool.disconnect

    async def recording(*args, **kwargs):
        disconnected.append(True)
        return await original(*args, **kwargs)

    persistence._redis.connection_pool.disconnect = recording

    await persistence.update_bot_data({"a": 1})
    assert disconnected == [], "the connection is dropped only once it has gone idle"

    await asyncio.sleep(0.05)
    assert disconnected == [True]


async def test_a_further_write_defers_the_disconnect(fake_redis):
    """A busy bot must not reconnect between every write."""
    persistence = RedisPersistence(url="redis://x", key=KEY, idle_disconnect=0.05)

    disconnected = []
    original = persistence._redis.connection_pool.disconnect

    async def recording(*args, **kwargs):
        disconnected.append(True)
        return await original(*args, **kwargs)

    persistence._redis.connection_pool.disconnect = recording

    for i in range(4):  # writes closer together than the idle window
        await persistence.update_bot_data({"a": i})
        await asyncio.sleep(0.02)
    assert disconnected == []

    await asyncio.sleep(0.1)
    assert disconnected == [True]


async def test_the_data_is_still_there_after_a_disconnect(fake_redis):
    """Closing the pool is not destructive — redis-py reconnects on the next command."""
    persistence = RedisPersistence(url="redis://x", key=KEY, idle_disconnect=0.01)
    await persistence.update_bot_data({"a": 1})
    await asyncio.sleep(0.05)

    await persistence.update_bot_data({"a": 2})  # must reconnect, not raise
    assert json.loads(json.loads(fake_redis.get(KEY))["bot_data"]) == {"a": 2}
