"""The global error handler: every unhandled handler exception arrives here."""

import structlog
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import settings

logger = structlog.get_logger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    e = str(error).lower()
    # These are matched against Telegram's own error text, which is always English. They are
    # NOT messages and must never be moved into templates.py or translated: a translated
    # swallow-list would stop matching, and every routine long-poll timeout would start
    # reporting to the log group.
    if "timed out" in e or "not modified" in e or "query_id_invalid" in e:
        return
    logger.error("update_error", exc_info=error)
    if not settings.LOG_GROUP_ID:
        return
    try:
        await context.bot.send_message(settings.LOG_GROUP_ID, str(error), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("log_group_report_failed")
