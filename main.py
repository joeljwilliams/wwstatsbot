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

import os
import asyncio
import logging
import datetime
import html

import httpx
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    InlineQueryHandler,
    ContextTypes,
)

from unidecode import unidecode
import db
import health
import templates as t

import wwstats

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration is read from environment variables (for containers / k8s), with
# a fallback to a local config.py module for development. Env vars win.
try:
    from config import (
        BOT_TOKEN as _CFG_TOKEN,
        LOG_GROUP_ID as _CFG_LOG_GROUP,
    )
except ImportError:
    _CFG_TOKEN, _CFG_LOG_GROUP = None, None

try:
    from config import DATABASE_URL as _CFG_DATABASE_URL
except ImportError:
    _CFG_DATABASE_URL = None

try:
    from config import SUPERUSER_ID as _CFG_SUPERUSER_ID
except ImportError:
    _CFG_SUPERUSER_ID = None

BOT_TOKEN = os.environ.get("BOT_TOKEN", _CFG_TOKEN)
LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", _CFG_LOG_GROUP or 0)) or None
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
DATABASE_URL = os.environ.get("DATABASE_URL", _CFG_DATABASE_URL)
SUPERUSER_ID = int(os.environ.get("SUPERUSER_ID", _CFG_SUPERUSER_ID or 0)) or None

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set (env var BOT_TOKEN or config.py).")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set (env var DATABASE_URL or config.py).")

BASE = "https://www.tgwerewolf.com/Stats"

# Shared async HTTP client, reused across all handlers. Created at startup,
# closed on shutdown (see main()).
client = httpx.AsyncClient(timeout=15)


# --- Stats API helpers (async) ---------------------------------------------

async def get_stats(user_id):
    r = await client.get(BASE + "/PlayerStats/", params={"pid": user_id, "json": "true"})
    return r.json()


async def get_achievement_count(user_id):
    r = await client.get(BASE + "/PlayerAchievements/", params={"pid": user_id, "json": "true"})
    return len(r.json())


async def get_kills(user_id):
    r = await client.get(BASE + "/PlayerKills/", params={"pid": user_id, "json": "true"})
    return r.json()


async def get_killed_by(user_id):
    r = await client.get(BASE + "/PlayerKilledBy/", params={"pid": user_id, "json": "true"})
    return r.json()


async def get_deaths(user_id):
    r = await client.get(BASE + "/PlayerDeaths/", params={"pid": user_id, "json": "true"})
    return r.json()


async def get_achievements(user_id):
    r = await client.get(BASE + "/PlayerAchievements/", params={"pid": user_id, "json": "true"})
    return r.json()


# --- Message builders (reused by commands and inline query) ----------------

async def build_kills_msg(user_id, name):
    kills = await get_kills(user_id)
    msg = t.KILLS_HEADER.format(user_id=user_id, name=name)
    for k in kills:
        msg += t.COUNT_ROW.format(count=k['times'], label=html.escape(k['name']))
    return msg


async def build_killed_by_msg(user_id, name):
    killedby = await get_killed_by(user_id)
    msg = t.KILLED_BY_HEADER.format(user_id=user_id, name=name)
    for k in killedby:
        msg += t.COUNT_ROW.format(count=k['times'], label=html.escape(k['name']))
    return msg


async def build_deaths_msg(user_id, name):
    deaths = await get_deaths(user_id)
    stats = await get_stats(user_id)
    msg = t.DEATHS_HEADER.format(user_id=user_id, name=name)
    for d in deaths:
        # The total per kill method is derived from the percentage in the JSON,
        # so the value is approximate rather than exact.
        total = round((stats['gamesPlayed'] - stats['survived']['total']) * float(d['percent']) / 100)
        msg += t.DEATH_ROW.format(percent=d['percent'], method=d['method'], total=total)
    return msg


async def build_stats_msg(user_id, name, by_id=False):
    stats = await get_stats(user_id)
    achievements = await get_achievement_count(user_id)

    if not stats:
        template = t.NO_GAMES_BY_ID if by_id else t.NO_GAMES
        return template.format(user_id=user_id, name=name)

    name_template = t.STATS_NAME_BY_ID if by_id else t.STATS_NAME
    msg = name_template.format(user_id=user_id, name=name, role=stats['mostCommonRole'])
    msg += t.STATS_ACHIEVEMENTS.format(count=achievements)
    msg += t.STATS_WON.format(total=stats['won']['total'], percent=stats['won']['percent'])
    msg += t.STATS_LOST.format(total=stats['lost']['total'], percent=stats['lost']['percent'])
    msg += t.STATS_SURVIVED.format(total=stats['survived']['total'], percent=stats['survived']['percent'])
    msg += t.STATS_TOTAL.format(total=stats['gamesPlayed'])
    if stats['mostKilled']:
        msg += t.STATS_MOST_KILLED.format(
            times=stats['mostKilled']['times'], name=html.escape(stats['mostKilled']['name']))
    if stats['mostKilledBy']:
        msg += t.STATS_MOST_KILLED_BY.format(
            times=stats['mostKilledBy']['times'], name=html.escape(stats['mostKilledBy']['name']))
    return msg


async def build_info_results(search):
    """Full-text achievement search (name / name-initialism / description), with
    a substring-on-name fallback when FTS finds nothing."""
    matches = await db.search_achievements(search)
    if matches:
        return matches
    # FTS found nothing (e.g. a stopword-only query, or a mid-word substring that
    # prefix matching can't catch). Fall back to the old case-insensitive
    # substring-on-name scan over the in-memory cache.
    s = search.lower()
    return [a for a in db.get_achievements() if s in a['name'].lower()]


def format_single_achv(achv):
    """HTML block for one achievement, including the type and notes fields."""
    msg = t.ACHV_CARD.format(
        name=html.escape(achv['name']),
        desc=html.escape(achv['desc']),
        type=achv.get('type', 'instantaneous'),
    )
    notes = achv.get('notes', '')
    if notes:
        # Expandable blockquote (Bot API 7.0+) so long notes collapse by default.
        msg += t.ACHV_CARD_NOTES.format(notes=html.escape(notes))
    return msg


def resolve_target(update):
    """Resolve (user_id, name) from a message: reply target if present, else sender."""
    if update.message.reply_to_message is not None:
        user = update.message.reply_to_message.from_user
    else:
        user = update.message.from_user
    return user.id, html.escape(user.first_name)


# --- Command handlers ------------------------------------------------------

async def display_kills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, name = resolve_target(update)
    print("%s - %s (%d) - kills" % (str(datetime.datetime.now() + datetime.timedelta(hours=8)), unidecode(name), user_id))
    msg = await build_kills_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_killed_by(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, name = resolve_target(update)
    print("%s - %s (%d) - killed by" % (str(datetime.datetime.now() + datetime.timedelta(hours=8)), unidecode(name), user_id))
    msg = await build_killed_by_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_deaths(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, name = resolve_target(update)
    print("%s - %s (%d) - deaths" % (str(datetime.datetime.now() + datetime.timedelta(hours=8)), unidecode(name), user_id))
    msg = await build_deaths_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    user_id, name = resolve_target(update)
    print("%s - %s (%d) - search %s" % (str(datetime.datetime.now() + datetime.timedelta(hours=8)), unidecode(name), user_id, args))

    if len(args) == 0:
        msg = "Invalid parameter! Syntax:\n<code>/search [achievement_to_search]</code>\n"
    else:
        found_counter = 0
        achv = await get_achievements(user_id)
        msg = "Attained achievements of <a href='tg://user?id={}'>{}</a> found:\n".format(user_id, name)
        for item in range(len(achv)):
            achv_name = "{}".format(achv[item]['name'])
            found_this = False

            for n in range(len(achv_name.split())):
                for word in range(len(args)):
                    if achv_name.split()[n].lower().startswith(args[word].lower()):
                        msg += "<code>{}</code>\n".format(achv_name)
                        found_this = True
                        found_counter += 1
                        break
                if found_this:
                    break

        if found_counter == 0:
            msg += "<b>No matching achievements found!</b>\n"

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

    print("%s - %s (%s) - stats" % (str(datetime.datetime.now() + datetime.timedelta(hours=8)), unidecode(str(name)), user_id))

    msg = await build_stats_msg(user_id, name, by_id=by_id)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "Use /stats for stats. Use /achievements or /achv for achivement list."
    msg += "\n\nThis is an edited version to the old `@wolfcardbot`.\n"
    msg += "Click [here](https://github.com/jeffffc/wwstatsbot) for the source code of the current project."
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


async def startme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == 'private':
        await update.message.reply_text("Thank you for starting me. "
                                        "Use /stats and /achievements to check your related stats!")
    else:
        return


async def display_achv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    name = html.escape(update.message.from_user.first_name)

    print("%s - %s (%d) - achv" % (str(datetime.datetime.now() + datetime.timedelta(hours=8)), unidecode(name), user_id))

    msgs = await wwstats.check(user_id, client)

    try:
        for msg in msgs:
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        if update.message.chat.type != 'private':
            await update.message.reply_text("I have sent you your achievement list in PM.")
    except Exception:
        url = "telegram.me/{}".format(context.bot.username)
        keyboard = [[InlineKeyboardButton("Start Me!", url=url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("You have to start me in PM first.", reply_markup=reply_markup)


async def display_achv_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    user_id = update.message.from_user.id
    name = html.escape(update.message.from_user.first_name)

    search = ""
    if len(args) > 0:
        search = ' '.join(args)
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        search = update.message.reply_to_message.text

    print("%s - %s (%d) - info %s" % (
        str(datetime.datetime.now() + datetime.timedelta(hours=8)), unidecode(name), user_id, args))

    if len(search) == 0:
        msg = "Invalid parameter! Syntax:\n<code>/info [achievement_to_search]</code>\n"
    elif len(search) < 3:
        msg = "Please enter at least 3 letters to search for!\n"
    else:
        found = await build_info_results(search)
        if not found:
            msg = "No matching achievements found!\n"
        elif len(found) == 1:
            msg = format_single_achv(found[0])
        else:
            msg = "<b>Multiple achievements found!</b>\nTry one of these:\n"
            msg += "\n".join("<code>/info {}</code>".format(achv['name']) for achv in found) + "\n"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# --- Admin roles & commands ------------------------------------------------

def is_superuser(user_id):
    return SUPERUSER_ID is not None and user_id == SUPERUSER_ID


async def is_admin_user(user_id):
    return is_superuser(user_id) or await db.is_admin(user_id)


def _resolve_admin_target(update, context):
    """Resolve (user_id, username, first_name) for admin management: the replied-to
    user if present, else a numeric user id passed as the first arg. None if neither."""
    if update.message.reply_to_message is not None:
        u = update.message.reply_to_message.from_user
        return u.id, u.username, u.first_name
    if context.args:
        try:
            return int(context.args[0]), None, None
        except ValueError:
            return None
    return None


async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superuser(update.message.from_user.id):
        await update.message.reply_text("Only the superuser can add admins.")
        return
    target = _resolve_admin_target(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: reply to a user with /addadmin, or /addadmin <user_id>.")
        return
    user_id, username, first_name = target
    await db.add_admin(user_id, username, first_name, update.message.from_user.id)
    label = html.escape(first_name) if first_name else str(user_id)
    await update.message.reply_text(
        "Added <a href='tg://user?id={}'>{}</a> as an admin.".format(user_id, label),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def del_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superuser(update.message.from_user.id):
        await update.message.reply_text("Only the superuser can remove admins.")
        return
    target = _resolve_admin_target(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: reply to a user with /deladmin, or /deladmin <user_id>.")
        return
    removed = await db.remove_admin(target[0])
    await update.message.reply_text(
        "Removed admin {}.".format(target[0]) if removed else "That user is not an admin.")


async def list_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superuser(update.message.from_user.id):
        await update.message.reply_text("Only the superuser can list admins.")
        return
    rows = await db.list_admins()
    if not rows:
        await update.message.reply_text("No admins yet.")
        return
    lines = ["<b>Admins:</b>"]
    for r in rows:
        name = html.escape(r['first_name']) if r['first_name'] else "(unknown)"
        uname = " @{}".format(html.escape(r['username'])) if r['username'] else ""
        lines.append("<code>{}</code> {}{}".format(r['user_id'], name, uname))
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def set_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_user(update.message.from_user.id):
        await update.message.reply_text("Only admins can edit notes.")
        return
    replied = update.message.reply_to_message
    if replied is None or not replied.text:
        await update.message.reply_text(
            "Reply to an achievement /info card with <code>/setnote &lt;note&gt;</code>.",
            parse_mode=ParseMode.HTML)
        return
    note = ' '.join(context.args).strip()
    if not note:
        await update.message.reply_text("Please provide the note text: /setnote <note>.")
        return
    # The achievement name is the first non-empty line of the replied card's plain text.
    title = next((line.strip() for line in replied.text.splitlines() if line.strip()), "")
    match = next((a for a in db.get_achievements() if a['name'] == title), None)
    if match is None:
        await update.message.reply_text(
            "Could not identify the achievement from that message. "
            "Reply to a single /info card.")
        return
    await db.update_notes(match['name'], note)
    updated = next((a for a in db.get_achievements() if a['name'] == match['name']), match)
    await update.message.reply_text(
        "Note updated.\n\n" + format_single_achv(updated),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# --- Inline query ----------------------------------------------------------

def _article(result_id, title, html_text, description=None):
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            html_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        ),
    )


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    user = update.inline_query.from_user
    name = html.escape(user.first_name)

    if not query:
        # Empty query: 4 stat cards for the querying user, fetched in parallel.
        stats_msg, kills_msg, killedby_msg, deaths_msg = await asyncio.gather(
            build_stats_msg(user.id, name),
            build_kills_msg(user.id, name),
            build_killed_by_msg(user.id, name),
            build_deaths_msg(user.id, name),
        )
        results = [
            _article("stats", "My Stats", stats_msg),
            _article("kills", "My Kills", kills_msg),
            _article("killedby", "My Killed By", killedby_msg),
            _article("deaths", "My Deaths", deaths_msg),
        ]
    else:
        # Typed text: achievement search, same behaviour as /info.
        matches = await build_info_results(query)
        if not matches:
            results = [_article("none", "No matching achievements", "No matching achievements found.")]
        else:
            results = [
                _article(m['name'], m['name'], format_single_achv(m), description=m['desc'])
                for m in matches[:50]
            ]

    await update.inline_query.answer(results, cache_time=30, is_personal=True)


# --- Error handling & startup ----------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    e = str(error).lower()
    if "timed out" in e or "not modified" in e or "query_id_invalid" in e:
        return
    logger.error("Update caused error: %s", error)
    if not LOG_GROUP_ID:
        return
    try:
        await context.bot.send_message(LOG_GROUP_ID, str(error), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Failed to report error to log group")


async def _post_init(application: Application):
    # Bring up the database before reporting ready to k8s.
    await db.init_pool(DATABASE_URL)
    await db.ensure_schema()
    await db.seed_achievements()
    await db.load_cache()
    health.set_ready(True)


async def _post_shutdown(application: Application):
    health.set_ready(False)
    await client.aclose()
    await db.close_pool()


def main():
    health.start_health_server(HEALTH_PORT)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler('start', startme))
    app.add_handler(CommandHandler('stats', display_stats))
    app.add_handler(CommandHandler('kills', display_kills))
    app.add_handler(CommandHandler('killedby', display_killed_by))
    app.add_handler(CommandHandler('deaths', display_deaths))
    app.add_handler(CommandHandler(['search', 'sch'], display_search))
    app.add_handler(CommandHandler('about', display_about))
    app.add_handler(CommandHandler(['achievements', 'achv'], display_achv))
    app.add_handler(CommandHandler(['info', 'getachv'], display_achv_info))
    app.add_handler(CommandHandler('addadmin', add_admin_cmd))
    app.add_handler(CommandHandler('deladmin', del_admin_cmd))
    app.add_handler(CommandHandler('admins', list_admins_cmd))
    app.add_handler(CommandHandler('setnote', set_note_cmd))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_error_handler(error_handler)

    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
