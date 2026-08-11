"""Player statistics: /stats, /kills, /killedby, /deaths.

Thin handlers over builders.py — the messages themselves are built there because inline
mode renders the same ones.
"""

import html

import structlog
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from unidecode import unidecode

import builders
from handlers.common import resolve_target

logger = structlog.get_logger(__name__)


async def display_kills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, name = resolve_target(update)
    logger.info("command", command="kills", user_id=user_id, user=unidecode(name))
    msg = await builders.build_kills_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_killed_by(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, name = resolve_target(update)
    logger.info("command", command="killedby", user_id=user_id, user=unidecode(name))
    msg = await builders.build_killed_by_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_deaths(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, name = resolve_target(update)
    logger.info("command", command="deaths", user_id=user_id, user=unidecode(name))
    msg = await builders.build_deaths_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    by_id = False
    if update.message.reply_to_message is not None:
        user_id, name = resolve_target(update)
    else:
        if args:
            try:
                user_id = int(args[0])
                name = args[0]
                by_id = True
            except ValueError:
                user_id = update.message.from_user.id
                name = html.escape(update.message.from_user.first_name)
        else:
            user_id = update.message.from_user.id
            name = html.escape(update.message.from_user.first_name)

    logger.info("command", command="stats", user_id=user_id, user=unidecode(str(name)), by_id=by_id)

    msg = await builders.build_stats_msg(user_id, name, by_id=by_id)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
