"""Redis-backed persistence for python-telegram-bot.

python-telegram-bot ships only non-durable persistence (PicklePersistence writes a
file — no good on Railway/read-only-rootfs — and DictPersistence is in-memory). This
adds a durable Redis backend, modelled on ptbcontrib's postgres_persistence, which
uses the same trick: subclass ``DictPersistence`` (it already implements every
get/update method plus JSON (de)serialization) and only add load-at-startup /
write-on-change against the store.

The entire persistence blob (bot_data / chat_data / user_data / callback_data /
conversations, each already a JSON string from DictPersistence) is stored under one
Redis key as a JSON object. Only JSON-serializable data can be persisted — a
DictPersistence constraint.

Failure handling favours availability: a Redis outage at load or save is logged and
swallowed rather than crashing the bot; persistence silently degrades until Redis is
back. The initial read is synchronous (no event loop exists yet at construction);
all later writes use the async client.

A write that would change nothing is skipped, and the connection is dropped once it has
gone unused — see ``_save`` and ``_disconnect_when_idle``. Together those are what let an
idle bot fall silent, which is what lets Railway put it to sleep.
"""

import asyncio
import json

import redis
import redis.asyncio as aioredis
import structlog
from telegram.ext import DictPersistence

logger = structlog.get_logger(__name__)


def _clean(value):
    """Normalise a stored *_json field for DictPersistence.__init__.

    DictPersistence's *_json properties emit the literal "null" for empty state,
    but its constructor rejects "null" — it only accepts "" or a JSON object. Map
    both "null" and a missing key back to "" so a round-trip through Redis works.
    """
    return "" if value in (None, "null") else value


class RedisPersistence(DictPersistence):
    """DictPersistence whose state is durably mirrored to a single Redis key.

    Args:
        url: redis:// connection URL.
        key: the Redis key holding the JSON blob (default "ptb:persistence").
        on_flush: if True, only write to Redis on flush() (bot shutdown) instead of
            after every change. Trades durability for fewer writes.
    """

    def __init__(self, url, key="ptb:persistence", on_flush=False, idle_disconnect=60.0, **kwargs):
        self._key = key
        self._on_flush = on_flush
        self._redis = aioredis.Redis.from_url(url)
        self._idle_disconnect = idle_disconnect
        self._disconnect_handle = None
        self._disconnect_task = None

        data = self._load_sync(url, key)

        super().__init__(
            bot_data_json=_clean(data.get("bot_data")),
            chat_data_json=_clean(data.get("chat_data")),
            user_data_json=_clean(data.get("user_data")),
            callback_data_json=_clean(data.get("callback_data")),
            conversations_json=_clean(data.get("conversations")),
            **kwargs,
        )

        # What Redis is believed to hold, so _save can skip a write that would change
        # nothing. Seeded from the load rather than left empty, so a bot that boots and is
        # never spoken to settles into silence instead of rewriting the blob it just read.
        #
        # "Settles into" rather than "starts": PTB's get_bot_data() turns a None bot_data
        # into {} the first time it is read, which is a change to the blob and so costs one
        # write per process. That is a write at startup, not a write a minute, and it is
        # the startup ones that cost nothing — the container is awake anyway.
        self._last_saved = self._blob()

    @staticmethod
    def _load_sync(url, key):
        """One-time blocking read at startup via a short-lived sync client.

        Runs before the event loop exists, so it can't use the async client. Any
        failure yields empty data (the bot still starts, without prior state)."""
        client = redis.Redis.from_url(url)
        try:
            raw = client.get(key)
            data = json.loads(raw) if raw else {}
            logger.info("redis_persistence_loaded", key=key, present=bool(raw))
            return data
        except Exception:
            logger.exception("redis_persistence_load_failed", key=key)
            return {}
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _blob(self):
        """The exact bytes that represent the current state in Redis."""
        return json.dumps(
            {
                "bot_data": self.bot_data_json,
                "chat_data": self.chat_data_json,
                "user_data": self.user_data_json,
                "callback_data": self.callback_data_json,
                "conversations": self.conversations_json,
            }
        )

    async def _save(self):
        """Write the state blob, unless Redis already holds exactly these bytes.

        PTB's persistence loop calls update_bot_data and update_callback_data every
        `update_interval` seconds (60 by default) whether or not anything changed, and both
        land here. Writing regardless meant a Redis round-trip a minute for the life of the
        process — and a round-trip is outbound traffic, which is the only thing Railway
        looks at to decide a service is idle. A bot nobody was talking to could therefore
        never be put to sleep, so the serverless flag on the service did nothing.

        DictPersistence already drops an update that changes nothing; this is that same
        idea one layer down, where the network is. The comparison is on the serialized blob
        rather than the dicts, which makes it exact rather than approximate: the write is
        skipped only when the bytes are the ones already there.
        """
        blob = self._blob()
        if blob == self._last_saved:
            return
        try:
            await self._redis.set(self._key, blob)
        except Exception:
            # Never let a Redis blip break a handler; state stays in memory. _last_saved is
            # deliberately not advanced, so the next attempt retries this write instead of
            # assuming it landed.
            logger.exception("redis_persistence_save_failed", key=self._key)
            return
        self._last_saved = blob
        self._disconnect_when_idle()

    def _disconnect_when_idle(self):
        """Drop the connection once it has gone unused for a while.

        Skipping unchanged writes is not enough on its own, because an idle *socket* is
        not a silent one. Redis ships `tcp-keepalive 300` and `timeout 0`, so it probes an
        idle client every 300 seconds and never hangs up first — and the bot's TCP stack
        answers every probe. Railway's threshold is those same 5 minutes, so the two ends
        kept each other awake for ever over a connection neither was using: the bot never
        slept, and neither did Redis.

        This is the same fix as `max_inactive_connection_lifetime` on the asyncpg pool, and
        it is why Postgres was already sleeping while Redis was not. Closing the pool does
        not break it — redis-py opens a new connection on the next command — so the cost of
        being wrong here is one reconnect, not an error.

        The timer is local and fires no traffic of its own.
        """
        if not self._idle_disconnect:
            return
        if self._disconnect_handle is not None:
            self._disconnect_handle.cancel()
        self._disconnect_handle = asyncio.get_running_loop().call_later(self._idle_disconnect, self._start_disconnect)

    def _start_disconnect(self):
        # call_later cannot await, so the close runs as a task. The reference is kept
        # because asyncio only holds a weak one and would be free to collect it mid-close.
        self._disconnect_task = asyncio.ensure_future(self._disconnect())

    async def _disconnect(self):
        self._disconnect_handle = None
        try:
            await self._redis.connection_pool.disconnect()
        except Exception:
            # Nothing here is worth failing over: the next command reconnects anyway.
            logger.exception("redis_persistence_disconnect_failed", key=self._key)

    # Each update_* stores into DictPersistence's in-memory dict (via super), then
    # mirrors to Redis unless deferring to flush().

    async def update_bot_data(self, data):
        await super().update_bot_data(data)
        if not self._on_flush:
            await self._save()

    async def update_chat_data(self, chat_id, data):
        await super().update_chat_data(chat_id, data)
        if not self._on_flush:
            await self._save()

    async def update_user_data(self, user_id, data):
        await super().update_user_data(user_id, data)
        if not self._on_flush:
            await self._save()

    async def update_callback_data(self, data):
        await super().update_callback_data(data)
        if not self._on_flush:
            await self._save()

    async def update_conversation(self, name, key, new_state):
        await super().update_conversation(name, key, new_state)
        if not self._on_flush:
            await self._save()

    async def flush(self):
        """Final write on shutdown, then close the connection."""
        if self._disconnect_handle is not None:
            self._disconnect_handle.cancel()
            self._disconnect_handle = None
        await self._save()
        try:
            await self._redis.aclose()
        except Exception:
            pass
