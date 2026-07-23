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
from achvlist import ACHV
import health

import wwstats

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration is read from environment variables (for containers / k8s), with
# a fallback to a local config.py module for development. Env vars win.
try:
    from config import BOT_TOKEN as _CFG_TOKEN, LOG_GROUP_ID as _CFG_LOG_GROUP
except ImportError:
    _CFG_TOKEN, _CFG_LOG_GROUP = None, None

BOT_TOKEN = os.environ.get("BOT_TOKEN", _CFG_TOKEN)
LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", _CFG_LOG_GROUP or 0)) or None
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set (env var BOT_TOKEN or config.py).")

BASE = "http://www.tgwerewolf.com/Stats"

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
    msg = "Players <a href='tg://user?id={}'> {}</a> most killed:\n".format(user_id, name)
    for n in range(len(kills)):
        msg += "<code>{:<5}</code> <b>{}</b>\n".format(kills[n]['times'], html.escape(kills[n]['name']))
    return msg


async def build_killed_by_msg(user_id, name):
    killedby = await get_killed_by(user_id)
    msg = "Players who killed <a href='tg://user?id={}'>{}</a> most:\n".format(user_id, name)
    for n in range(len(killedby)):
        msg += "<code>{:<5}</code> <b>{}</b>\n".format(killedby[n]['times'], html.escape(killedby[n]['name']))
    return msg


async def build_deaths_msg(user_id, name):
    deaths = await get_deaths(user_id)
    stats = await get_stats(user_id)
    msg = "Types of deaths that <a href='tg://user?id={}'>{}</a> most had:\n".format(user_id, name)
    for n in range(len(deaths)):
        """ The total of deaths for each kill method is calculated based on the percentage
        gave by the JSON data. Because of that, the calculated value is not totally accurate."""
        totalMethod = ((stats['gamesPlayed'] - stats['survived']['total']) * float(deaths[n]['percent']) / 100)
        msg += "<code>{}%</code>   <b>{}</b>   <code>(approx. {})</code>\n".format(
            deaths[n]['percent'], deaths[n]['method'], round(totalMethod))
    return msg


async def build_stats_msg(user_id, name, by_id=False):
    stats = await get_stats(user_id)
    achievements = await get_achievement_count(user_id)

    if stats:
        msg = "<a href='tg://user?id={}'>{} the {}</a>\n".format(user_id, name, stats['mostCommonRole']) if not by_id else "{} the {}\n".format(name, stats['mostCommonRole'])
        msg += "<code>{:<5}</code> Achievements Unlocked!\n".format(achievements)
        msg += "<code>{:<5}</code> Games Won <code>({}%)</code>\n".format(stats['won']['total'], stats['won']['percent'])
        msg += "<code>{:<5}</code> Games Lost <code>({}%)</code>\n".format(stats['lost']['total'], stats['lost']['percent'])
        msg += "<code>{:<5}</code> Games Survived <code>({}%)</code>\n".format(
            stats['survived']['total'], stats['survived']['percent'])
        msg += "<code>{:<5}</code> Total Games\n".format(stats['gamesPlayed'])
        if stats['mostKilled']:
            msg += "<code>{:<5}</code> times I've gleefully killed {}\n".format(
                stats['mostKilled']['times'], html.escape(stats['mostKilled']['name']))
        if stats['mostKilledBy']:
            msg += "<code>{:<5}</code> times I've been slaughted by {}\n\n".format(
                stats['mostKilledBy']['times'], html.escape(stats['mostKilledBy']['name']))
    else:
        msg = "<a href='tg://user?id={}'>{}</a> has not played any games.".format(user_id, name) if not by_id else "{} has not played any games.".format(name)
    return msg


def build_info_results(search):
    """Return the list of ACHV dicts whose name contains `search` (case-insensitive)."""
    found = []
    for item in range(len(ACHV)):
        achv_name = "{}".format(ACHV[item]['name'])
        if search.lower() in achv_name.lower():
            found.append(ACHV[item])
    return found


def format_single_achv(achv):
    """HTML block for one achievement, including the new type and notes fields."""
    msg = "<b>Achievement info:</b>\n\n" \
          "<b>{}</b>\n{}\n".format(html.escape(achv['name']), html.escape(achv['desc']))
    msg += "Type: <code>{}</code>\n".format(achv.get('type', 'instantaneous'))
    notes = achv.get('notes', '')
    if notes:
        msg += "Notes: {}\n".format(html.escape(notes))
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
        found = build_info_results(search)
        if not found:
            msg = "No matching achievements found!\n"
        elif len(found) == 1:
            msg = format_single_achv(found[0])
        else:
            msg = "<b>Multiple achievements found!</b>\nTry one of these:\n"
            msg += "\n".join("<code>/info {}</code>".format(achv['name']) for achv in found) + "\n"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


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
        # Empty query: 4 stat cards for the querying user.
        results = [
            _article("kills", "My Kills", await build_kills_msg(user.id, name)),
            _article("killedby", "My Killed By", await build_killed_by_msg(user.id, name)),
            _article("deaths", "My Deaths", await build_deaths_msg(user.id, name)),
            _article("stats", "My Stats", await build_stats_msg(user.id, name)),
        ]
    else:
        # Typed text: achievement search, same behaviour as /info.
        matches = build_info_results(query)
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
    # Bot is initialised and about to start polling -> report ready to k8s.
    health.set_ready(True)


async def _post_shutdown(application: Application):
    health.set_ready(False)
    await client.aclose()


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
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_error_handler(error_handler)

    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
