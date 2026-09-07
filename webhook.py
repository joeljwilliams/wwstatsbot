"""Receiving updates over a webhook, on the same port as the health probes.

Telegram will either push updates to a URL or wait for `getUpdates` to ask; this module is
the first half. PTB ships its own webhook server, and it is deliberately not used: it
serves exactly one route — the update path — while the platform this bot runs on exposes
exactly one port and expects `/healthz` on it. Two servers cannot both have that port, so
the health server (which already owns it) grows one more route and hands what arrives here.

What lives here is only the intake: authenticate the request, turn a body into an `Update`,
and put it on the queue PTB's `Application.start()` is already draining. Everything after
that is the same code path polling feeds, which is the point — a handler cannot tell how
its update arrived.

The receiver is called **from the health server's thread**, not from the event loop, which
is why the queue is fed through `call_soon_threadsafe`: `asyncio.Queue` is not thread-safe,
and putting to it directly from another thread loses updates in a way that looks like
Telegram never sent them.
"""

import hmac
import json

import structlog
from telegram import Update

logger = structlog.get_logger(__name__)

# Telegram's own header, carrying the token given to setWebhook. It is the only thing
# separating a real update from anybody who has guessed the URL, so a request without it
# is refused rather than trusted.
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def receiver(application, path, secret_token, loop):
    """Build the callable the health server hands each POST.

    Returns a function of (request_path, headers, body) -> HTTP status code. It answers with
    a status and nothing else: Telegram ignores the body of a webhook response, and
    anything we said in it would only be read by whoever was probing us.
    """

    def receive(request_path, headers, body):
        if request_path != path:
            return 404

        secret = headers.get(SECRET_HEADER)

        # compare_digest rather than ==, so the comparison cannot be timed to recover the
        # token a character at a time. A missing header is a str vs None comparison, hence
        # the "or" — compare_digest raises on None.
        if not hmac.compare_digest(secret or "", secret_token):
            logger.warning("webhook_rejected", reason="bad_secret", path=request_path)
            return 401

        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("webhook_rejected", reason="bad_json", error=str(exc))
            return 400

        try:
            update = Update.de_json(data, application.bot)
        except (TypeError, KeyError, ValueError) as exc:
            # Valid JSON that is not an update: de_json raises rather than returning None
            # (a missing update_id is a TypeError from Update's own constructor). Only
            # somebody holding the secret can get this far, so this is a malformed request
            # rather than anything Telegram sent — it is refused and logged, not queued.
            logger.warning("webhook_rejected", reason="not_an_update", error=str(exc))
            return 400

        # put_nowait, because the queue is unbounded and this thread must not block the
        # health server waiting on the event loop.
        loop.call_soon_threadsafe(application.update_queue.put_nowait, update)
        return 200

    return receive
