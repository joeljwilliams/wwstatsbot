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
import templates as t
from handlers import achievements, admin, errors, gamesession, inline, misc, search, stats
from logging_config import configure_logging

logger = structlog.get_logger(__name__)


# Public commands shown in Telegram's command menu (the "/" list and Menu
# button). Admin/superuser commands (addadmin, deladmin, admins, setnote, db)
# are intentionally omitted. Command aliases are omitted too — only the primary
# verb is listed to keep the menu clean. schall and allinfo are omitted for the
# same reason: /search and /info now switch to that behaviour themselves when
# they reply to a bot message, so the explicit spellings are only kept working
# for muscle memory, not advertised.
# The command word itself is never translated — Telegram matches on it — so only the
# descriptions come from templates. Telegram accepts a separate menu per language, which is
# what makes a localised "/" list possible (see _post_init).
PUBLIC_COMMANDS = [
    BotCommand("stats", t.CMD_STATS),
    BotCommand("kills", t.CMD_KILLS),
    BotCommand("killedby", t.CMD_KILLEDBY),
    BotCommand("deaths", t.CMD_DEATHS),
    BotCommand("search", t.CMD_SEARCH),
    BotCommand("achievements", t.CMD_ACHIEVEMENTS),
    BotCommand("info", t.CMD_INFO),
    BotCommand("about", t.CMD_ABOUT),
    BotCommand("version", t.CMD_VERSION),
    BotCommand("start", t.CMD_START),
]


async def _post_init(application: Application):
    # Bring up the database before reporting ready to k8s.
    await db.init_pool(settings.DATABASE_URL)
    await db.ensure_schema()
    await db.seed_achievements()
    await db.load_cache()
    # Rules reference achievements by name, so they can only be seeded once the
    # achievements themselves exist.
    await db.seed_rules()
    await db.load_rules_cache()
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
    # The stand-in achievement manager. These four command words belong to the *real*
    # manager, and Telegram delivers every slash command to every bot in the group, so each
    # handler stays silent unless this chat has a session (see handlers/gamesession.py).
    app.add_handler(CommandHandler("gs", gamesession.start_session_cmd))
    app.add_handler(CommandHandler("role", gamesession.role_cmd))
    app.add_handler(CommandHandler("rm", gamesession.rolemodel_cmd))
    app.add_handler(CommandHandler("love", gamesession.love_cmd))
    app.add_handler(CommandHandler("dead", gamesession.dead_cmd))
    app.add_handler(CommandHandler("ad", gamesession.follow_roster_cmd))
    app.add_handler(CommandHandler("steal", gamesession.steal_cmd))
    app.add_handler(CommandHandler("alt", gamesession.alt_cmd))
    app.add_handler(CommandHandler("la", gamesession.list_achievements_cmd))
    app.add_handler(CommandHandler("gsend", gamesession.end_session_cmd))
    app.add_handler(CallbackQueryHandler(gamesession.stop_callback, pattern=r"^standin:"))
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
