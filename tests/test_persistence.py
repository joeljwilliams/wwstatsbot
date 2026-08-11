"""RedisPersistence — durable bot_data for PTB, backed by fakeredis.

Two behaviours are load-bearing and easy to break:

* **The "null" quirk.** DictPersistence's `*_json` properties emit the literal string
  "null" for empty state, but its constructor rejects "null" — it accepts only "" or a
  JSON object. Without `_clean`, a round-trip through Redis raises on startup.
* **Availability over durability.** A Redis outage must be logged and swallowed, never
  crash a handler; persistence silently degrades to in-memory until Redis returns.
"""

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
    """This is the "null" path end to end — it used to blow up on construction."""
    persistence = RedisPersistence(url="redis://x", key=KEY)
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
