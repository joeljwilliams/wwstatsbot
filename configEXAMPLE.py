BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQURSTUVWXYZABCDEFGHI"
LOG_GROUP_ID = -1001234567890
DATABASE_URL = "postgresql://user:pass@host:5432/wwstatsbot"
SUPERUSER_ID = 123456789
# Optional: enables durable persistence (e.g. /allinfo buttons survive restarts).
# Leave unset/None for in-memory only.
REDIS_URL = None
# Optional: run on a webhook instead of long polling. Set the public base URL Telegram
# should call — no path, that is WEBHOOK_PATH — and the bot serves it on HEALTH_PORT
# alongside /healthz. Leave unset to poll, which needs no inbound connectivity at all.
WEBHOOK_URL = None
# Optional: the secret Telegram echoes back in X-Telegram-Bot-Api-Secret-Token. One is
# generated per boot when unset, so leaving this alone is safe — set it only if something
# in front of the bot needs to know the value.
WEBHOOK_SECRET = None
