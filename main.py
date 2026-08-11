#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# wolfcardbot.py - Extracts Werewolf for Telegram Stats & Displays in Chat
# author - Carson True
# license - GPL

# edited by @jeffffc
# /search by @jamiscs
# /info by @Olgabrezel
# ptb v22 async rewrite + inline query support

import structlog
from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    InlineQueryHandler,
)

import api
import db
import health
import settings
from handlers import achievements, admin, errors, inline, misc, search, stats
from logging_config import configure_logging

logger = structlog.get_logger(__name__)


# Public commands shown in Telegram's command menu (the "/" list and Menu
# button). Admin/superuser commands (addadmin, deladmin, admins, setnote, db)
# are intentionally omitted. Command aliases are omitted too — only the primary
# verb is listed to keep the menu clean. schall and allinfo are omitted for the
# same reason: /search and /info now switch to that behaviour themselves when
# they reply to a bot message, so the explicit spellings are only kept working
# for muscle memory, not advertised.
PUBLIC_COMMANDS = [
    BotCommand("stats", "Your game stats (or reply to another player)"),
    BotCommand("kills", "Players you've killed the most"),
    BotCommand("killedby", "Players who've killed you the most"),
    BotCommand("deaths", "Your most common causes of death"),
    BotCommand("search", "Search your achievements, or reply to a player list to check everyone"),
    BotCommand("achievements", "List all achievements"),
    BotCommand("info", "Look up an achievement, or reply to a list to get them all"),
    BotCommand("about", "About this bot"),
    BotCommand("version", "Show the running bot version"),
    BotCommand("start", "Start the bot in a private chat"),
]


async def _post_init(application: Application):
    # Bring up the database before reporting ready to k8s.
    await db.init_pool(settings.DATABASE_URL)
    await db.ensure_schema()
    await db.seed_achievements()
    await db.load_cache()
    await application.bot.set_my_commands(PUBLIC_COMMANDS)
    health.set_ready(True)


async def _post_shutdown(application: Application):
    health.set_ready(False)
    await api.close()
    await db.close_pool()


def build_application():
    """Construct the Application with every handler registered.

    Separate from main() so the wiring can be asserted without starting the health
    server or entering the polling loop. That matters because the registration table
    below is the one place a command can silently cease to exist: drop a line and the
    handler still passes its own tests while being unreachable from Telegram.
    """
    builder = Application.builder().token(settings.BOT_TOKEN).post_init(_post_init).post_shutdown(_post_shutdown)
    # Durable persistence for bot_data (e.g. /allinfo buttons survive restarts) when
    # a Redis backend is configured; otherwise state is in-memory only.
    if settings.REDIS_URL:
        from redis_persistence import RedisPersistence

        builder = builder.persistence(RedisPersistence(url=settings.REDIS_URL))
        logger.info("persistence_enabled", backend="redis")
    else:
        logger.info("persistence_disabled")
    app = builder.build()

    app.add_handler(CommandHandler("start", misc.startme))
    app.add_handler(CommandHandler("stats", stats.display_stats))
    app.add_handler(CommandHandler("kills", stats.display_kills))
    app.add_handler(CommandHandler("killedby", stats.display_killed_by))
    app.add_handler(CommandHandler("deaths", stats.display_deaths))
    app.add_handler(CommandHandler(["search", "sch"], search.display_search))
    app.add_handler(CommandHandler("schall", search.display_search_all))
    app.add_handler(CommandHandler("about", misc.display_about))
    app.add_handler(CommandHandler("version", misc.display_version))
    app.add_handler(CommandHandler(["achievements", "achv"], achievements.display_achv))
    app.add_handler(CommandHandler(["info", "getachv"], achievements.display_achv_info))
    app.add_handler(CommandHandler("allinfo", achievements.all_info_cmd))
    app.add_handler(CallbackQueryHandler(achievements.all_info_callback, pattern=r"^allinfo:"))
    app.add_handler(CallbackQueryHandler(search.schall_callback, pattern=r"^schall:"))
    app.add_handler(CommandHandler("addadmin", admin.add_admin_cmd))
    app.add_handler(CommandHandler("deladmin", admin.del_admin_cmd))
    app.add_handler(CommandHandler("admins", admin.list_admins_cmd))
    app.add_handler(CommandHandler("setnote", admin.set_note_cmd))
    app.add_handler(CommandHandler("clearnote", admin.clear_note_cmd))
    app.add_handler(CommandHandler("db", admin.db_console_cmd))
    app.add_handler(InlineQueryHandler(inline.inline_query))
    app.add_error_handler(errors.error_handler)

    return app


def main():
    configure_logging()
    settings.require()
    health.start_health_server(settings.HEALTH_PORT)
    build_application().run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
