"""Runtime configuration.

Every setting is read from an environment variable, falling back to a local `config.py`
module for development. **Environment wins**, which is what lets the container and
Railway ignore `config.py` entirely.

Named `settings.py`, not `config.py`: `config.py` is the gitignored local secrets file
this module *reads from*, so a repo module by that name would collide with (and on a
careless checkout, clobber) every developer's credentials.

Consumers must reference these through the module — ``settings.SUPERUSER_ID``, never
``from settings import SUPERUSER_ID``. A `from` import copies the value into the
importing module's namespace at import time, which both defeats monkeypatching in tests
and means two modules could disagree about the same setting. Keeping one authoritative
binding per name is the point.

Importing this module is side-effect-free apart from reading the environment: it must
stay safe to import from a test process that has no token and no database. Fail-fast on
missing required settings happens in require(), called from main().
"""

import hashlib
import os

# The local development fallback. Each import is guarded separately because config.py is
# optional (absent in every container) and may legitimately define only some names — an
# older checkout predating REDIS_URL, for instance, should still start.
try:
    from config import BOT_TOKEN as _CFG_TOKEN
except ImportError:
    _CFG_TOKEN = None

try:
    from config import LOG_GROUP_ID as _CFG_LOG_GROUP
except ImportError:
    _CFG_LOG_GROUP = None

try:
    from config import DATABASE_URL as _CFG_DATABASE_URL
except ImportError:
    _CFG_DATABASE_URL = None

try:
    from config import SUPERUSER_ID as _CFG_SUPERUSER_ID
except ImportError:
    _CFG_SUPERUSER_ID = None

try:
    from config import REDIS_URL as _CFG_REDIS_URL
except ImportError:
    _CFG_REDIS_URL = None

try:
    from config import WEBHOOK_URL as _CFG_WEBHOOK_URL
except ImportError:
    _CFG_WEBHOOK_URL = None

try:
    from config import WEBHOOK_SECRET as _CFG_WEBHOOK_SECRET
except ImportError:
    _CFG_WEBHOOK_SECRET = None


# Required. require() enforces these; they are read here so importing stays harmless.
BOT_TOKEN = os.environ.get("BOT_TOKEN", _CFG_TOKEN)
DATABASE_URL = os.environ.get("DATABASE_URL", _CFG_DATABASE_URL)

# Optional. The `int(...) or None` idiom maps both "unset" and an explicit 0 to None, so
# `is not None` checks downstream can't be satisfied by a falsy id.
LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", _CFG_LOG_GROUP or 0)) or None
SUPERUSER_ID = int(os.environ.get("SUPERUSER_ID", _CFG_SUPERUSER_ID or 0)) or None
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
# Enables durable /allinfo and /sch buttons (and any other bot_data) across restarts.
# Unset -> in-memory only.
REDIS_URL = os.environ.get("REDIS_URL", _CFG_REDIS_URL)

# --- Webhook -----------------------------------------------------------------
#
# Setting WEBHOOK_URL is what switches the bot from long polling to a webhook; unset, the
# bot polls exactly as it always has, so an existing deployment is unaffected by this
# existing. The URL is the **public base** Telegram should call — scheme and host, no path:
# the path is WEBHOOK_PATH and is appended here, so the two cannot disagree.
#
# There is no separate webhook port. The health server owns the one exposed port and takes
# the POST too (see health.py), because the platform exposes one port per service and wants
# the health path on it.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", _CFG_WEBHOOK_URL)
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/telegram")


# The token Telegram echoes in X-Telegram-Bot-Api-Secret-Token, and the only thing that
# distinguishes a real update from anyone who has guessed the URL. Never empty: a webhook
# with no secret accepts forged updates from the whole internet, and that must not be what
# forgetting a variable gets you.
#
# **Derived, not generated.** A per-boot random token was tried and is broken by any
# deployment where two containers overlap — which is every rolling deploy, Railway's
# included, even at one replica. Each container registers its own secret with setWebhook,
# the last one to start wins, and the other rejects every update Telegram sends with a 401
# while looking perfectly healthy. Observed exactly that on dev.
#
# A digest of the bot token is stable across restarts and identical in every replica with
# no configuration at all, which is what makes "secure by default" actually work here. It
# is not the token: SHA-256 is one-way, so leaking the webhook secret does not leak the
# credential it came from. Namespaced so the same token used elsewhere cannot collide.
def _derived_secret():
    if not BOT_TOKEN:
        # No token means no webhook is possible anyway; require() reports the real problem.
        return None
    return hashlib.sha256("wwstatsbot-webhook:{}".format(BOT_TOKEN).encode()).hexdigest()


WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", _CFG_WEBHOOK_SECRET) or _derived_secret()


def webhook_endpoint():
    """The full URL to register with Telegram, or None when polling.

    Joined here so a trailing slash on WEBHOOK_URL cannot produce a double slash that
    silently never matches the path the server is listening on.
    """
    if not WEBHOOK_URL:
        return None
    return "{}/{}".format(WEBHOOK_URL.rstrip("/"), WEBHOOK_PATH.lstrip("/"))


def require():
    """Fail fast on missing required settings. Called from main(), not at import.

    Importing must stay side-effect-free enough to be safe from a test process, which has
    neither a bot token nor a database: exiting at import time meant `import main` killed
    the interpreter, so nothing in the app could be tested.
    """
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set (env var BOT_TOKEN or config.py).")
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set (env var DATABASE_URL or config.py).")
