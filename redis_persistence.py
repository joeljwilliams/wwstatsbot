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
"""

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

    def __init__(self, url, key="ptb:persistence", on_flush=False, **kwargs):
        self._key = key
        self._on_flush = on_flush
        self._redis = aioredis.Redis.from_url(url)

        data = self._load_sync(url, key)

        super().__init__(
            bot_data_json=_clean(data.get("bot_data")),
            chat_data_json=_clean(data.get("chat_data")),
            user_data_json=_clean(data.get("user_data")),
            callback_data_json=_clean(data.get("callback_data")),
            conversations_json=_clean(data.get("conversations")),
            **kwargs,
        )

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

    async def _save(self):
        blob = {
            "bot_data": self.bot_data_json,
            "chat_data": self.chat_data_json,
            "user_data": self.user_data_json,
            "callback_data": self.callback_data_json,
            "conversations": self.conversations_json,
        }
        try:
            await self._redis.set(self._key, json.dumps(blob))
        except Exception:
            # Never let a Redis blip break a handler; state stays in memory.
            logger.exception("redis_persistence_save_failed", key=self._key)

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
        await self._save()
        try:
            await self._redis.aclose()
        except Exception:
            pass
