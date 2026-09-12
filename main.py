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

import asyncio
import signal

import structlog
from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

import api
import db
import health
import playerdata
import settings
import templates as t
import webhook
from handlers import achievements, admin, errors, gamesession, inline, misc, search, stats, welcome
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
    await db.load_alts_cache()
    # New achievements are noticed inside a lookup, under builders that have no `context`
    # and no business sending anything, so the bot they are announced with is handed over
    # here rather than threaded down. Nothing is posted until LOG_GROUP_ID is also set.
    playerdata.set_announcer(application.bot)
    await application.bot.set_my_commands(PUBLIC_COMMANDS)
    health.set_ready(True)


async def _post_shutdown(application: Application):
    health.set_ready(False)
    playerdata.set_announcer(None)
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
    app.add_handler(CommandHandler("welcome", welcome.welcome_cmd))
    app.add_handler(CommandHandler("about", misc.display_about))
    app.add_handler(CommandHandler("version", misc.display_version))
    app.add_handler(CommandHandler(["achievements", "achv"], achievements.display_achv))
    app.add_handler(CommandHandler(["info", "getachv"], achievements.display_achv_info))
    app.add_handler(CommandHandler("allinfo", achievements.all_info_cmd))
    app.add_handler(CommandHandler("roll", achievements.roll_cmd))
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
    app.add_handler(CommandHandler("gm", gamesession.game_management_cmd))
    app.add_handler(CommandHandler("la", gamesession.list_achievements_cmd))
    # Lynch order. Short words another bot in the room may own, so all three answer only
    # when addressed — see the module section in handlers/gamesession.py.
    app.add_handler(CommandHandler(["lo", "lynchorder"], gamesession.lynch_order_cmd))
    app.add_handler(CommandHandler(["slo", "setlynchorder"], gamesession.set_lynch_order_cmd))
    app.add_handler(CommandHandler(["rslo", "resetlynchorder"], gamesession.reset_lynch_order_cmd))
    app.add_handler(CommandHandler("gsend", gamesession.end_session_cmd))
    app.add_handler(CallbackQueryHandler(gamesession.stop_callback, pattern=r"^standin:"))
    # Join announcements. A service message, not a command, so it arrives as an ordinary
    # message update — no allowed_updates change needed, unlike a ChatMemberHandler.
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome.greet_new_members))
    # The Arsonist's doused list, read on sight when somebody forwards it. Reaching this at
    # all depends on the bot's group privacy mode being *off*; with it on, Telegram delivers
    # only commands and this handler would never fire in a group. The filter narrows the
    # traffic and the handler's own text match decides — it stays silent on every other
    # forward.
    app.add_handler(MessageHandler(filters.FORWARDED & (filters.TEXT | filters.CAPTION), gamesession.doused_forward))
    # Everything another bot says in a group, and the only handler that will ever see it.
    # Group -1 so it runs *before* the command words above and stops the update there: with
    # bot-to-bot communication enabled, a bare /gs or /gm off from some other bot in the room
    # would otherwise be a command issued to us. See the module section in
    # handlers/gamesession.py for what it does with the game bot's own messages.
    app.add_handler(
        MessageHandler(gamesession.FROM_A_BOT & (filters.TEXT | filters.CAPTION), gamesession.game_bot_message),
        group=-1,
    )
    app.add_handler(InlineQueryHandler(inline.inline_query))
    app.add_error_handler(errors.error_handler)

    return app


async def _serve_webhook(app):
    """Run the bot on a webhook, feeding PTB's queue from the health server's port.

    PTB's own `run_webhook` is deliberately not used: it starts a second HTTP server, and
    there is only one port to have. So the lifecycle is driven by hand — the pieces are the
    same ones `run_polling` uses, minus the Updater, which is what makes the update queue
    ours to fill.

    **The lifecycle hooks have to be called by hand too.** `initialize()` does not run
    post_init, and `shutdown()` does not run post_shutdown — PTB only calls those from
    run_polling/run_webhook, which is easy to miss and silent when missed: the bot comes up,
    answers HTTP, and then every handler fails on a database pool that was never created.
    They are read off the application rather than called directly, so a hook added in
    build_application cannot be forgotten here.

    Order matters. post_init is what creates the pool, loads the caches and publishes the
    command menu, so the receiver is installed only after it has run — until then the POST
    route answers 503 and Telegram redelivers, which is exactly what should happen to an
    update that arrives before the bot can serve it.
    """
    await app.initialize()
    if app.post_init:
        await app.post_init(app)

    health.set_update_receiver(
        webhook.receiver(app, settings.WEBHOOK_PATH, settings.WEBHOOK_SECRET, asyncio.get_running_loop())
    )
    endpoint = settings.webhook_endpoint()
    await app.bot.set_webhook(
        url=endpoint,
        secret_token=settings.WEBHOOK_SECRET,
        # Same as polling: a restart should not replay whatever queued while we were down.
        drop_pending_updates=True,
    )
    await app.start()
    logger.info("webhook_started", endpoint=endpoint, path=settings.WEBHOOK_PATH, port=settings.HEALTH_PORT)

    await _wait_for_stop()

    logger.info("webhook_stopping")
    # Refuse updates first: from here on there is no queue worth putting one on, and a 503
    # has Telegram redeliver it to whatever comes up next.
    health.set_update_receiver(None)
    await app.stop()
    if app.post_stop:
        await app.post_stop(app)
    # The webhook registration is left in place on purpose: deleting it would drop whatever
    # Telegram delivers between now and the next boot. A later start in polling mode clears
    # it anyway — PTB always calls deleteWebhook before getUpdates.
    await app.shutdown()
    if app.post_shutdown:
        await app.post_shutdown(app)


async def _wait_for_stop():
    """Block until the process is asked to stop.

    SIGTERM is how the platform asks, and without handling it the process is killed
    mid-update with post_shutdown never run — leaving the pool and the HTTP client to be
    reclaimed by the process dying rather than closed. Its own function so a test can drive
    the shutdown path without sending itself a signal.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signalled in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signalled, stop.set)
    await stop.wait()


def start(app):
    """Run the bot in the configured mode: webhook when WEBHOOK_URL is set, else polling."""
    if settings.WEBHOOK_URL:
        asyncio.run(_serve_webhook(app))
    else:
        app.run_polling(drop_pending_updates=True)


def main():
    configure_logging()
    settings.require()
    health.start_health_server(settings.HEALTH_PORT)
    start(build_application())


if __name__ == "__main__":
    main()
