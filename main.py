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
    BotCommand,
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


# Notes are stored in a single TEXT column but hold up to two sub-fields, each
# on its own line prefixed by a marker emoji (added automatically on write):
#   📝 <memo>        the main note (may span multiple lines)
#   🎲 <probability> the odds of attaining the achievement
# parse_notes/serialize_notes are the only encoders; storage stays schema-free
# and human-readable in the /db console. Fields are emitted in this order.
NOTE_MEMO = "\N{MEMO}"
NOTE_DIE = "\N{GAME DIE}"
_NOTE_MARKERS = [("memo", NOTE_MEMO), ("prob", NOTE_DIE)]
_PROB_KEYWORDS = {"prob", "probability"}


def parse_notes(raw):
    """Split a stored notes blob into {'memo': ..., 'prob': ...}.

    A line starting with a field marker begins that field; any text before the
    first marker is treated as the memo (back-compat with old plain notes).
    """
    marker_to_key = {marker: key for key, marker in _NOTE_MARKERS}
    buf = {key: [] for key, _ in _NOTE_MARKERS}
    current = "memo"
    for line in (raw or "").splitlines():
        stripped = line.lstrip()
        marker = next((m for m in marker_to_key if stripped.startswith(m)), None)
        if marker:
            current = marker_to_key[marker]
            buf[current].append(stripped[len(marker):].lstrip())
        else:
            buf[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in buf.items()}


def serialize_notes(fields):
    """Render a {'memo', 'prob'} dict back to the marker-prefixed storage form,
    omitting empty fields. Returns '' when both are empty."""
    parts = []
    for key, marker in _NOTE_MARKERS:
        value = fields.get(key, "").strip()
        if value:
            parts.append("{} {}".format(marker, value))
    return "\n".join(parts)


def format_single_achv(achv):
    """HTML block for one achievement, including the type and notes fields."""
    msg = t.ACHV_CARD.format(
        name=html.escape(achv['name']),
        desc=html.escape(achv['desc']),
        type=achv.get('type', 'instantaneous'),
    )
    # Normalise through parse/serialize so display is always canonical (markers
    # present and ordered) even for legacy or /db-console-edited notes.
    notes = serialize_notes(parse_notes(achv.get('notes', '')))
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
        else:
            # Results are rank-ordered (name hits first), so the top match is the
            # best answer — show it rather than making the user pick from a list.
            msg = format_single_achv(found[0])

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


def _achv_from_reply(replied):
    """Find the achievement whose /info card was replied to, by its title line
    (the first non-empty line of the card's plain text). None if no match."""
    title = next((line.strip() for line in replied.text.splitlines() if line.strip()), "")
    return next((a for a in db.get_achievements() if a['name'] == title), None)


def _split_note_field(arg):
    """Return (field_key, text) for a /setnote argument. A leading 'prob' /
    'probability' keyword selects the probability field; otherwise it's the memo
    and the whole argument is the text (line breaks preserved)."""
    tokens = arg.split(None, 1)
    if tokens and tokens[0].lower() in _PROB_KEYWORDS:
        return "prob", (tokens[1].strip() if len(tokens) > 1 else "")
    return "memo", arg


async def set_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_user(update.message.from_user.id):
        await update.message.reply_text("Only admins can edit notes.")
        return
    replied = update.message.reply_to_message
    if replied is None or not replied.text:
        await update.message.reply_text(
            "Reply to an achievement /info card with <code>/setnote &lt;note&gt;</code> "
            "or <code>/setnote prob &lt;probability&gt;</code>.",
            parse_mode=ParseMode.HTML)
        return
    # Take the text after the command verbatim so line breaks in the note are
    # preserved (context.args tokenises on whitespace and would flatten them).
    parts = update.message.text.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    field, text = _split_note_field(arg)
    if not text:
        await update.message.reply_text(
            "Please provide the text: <code>/setnote &lt;note&gt;</code> or "
            "<code>/setnote prob &lt;probability&gt;</code>. "
            "Use /clearnote to remove a field.",
            parse_mode=ParseMode.HTML)
        return
    match = _achv_from_reply(replied)
    if match is None:
        await update.message.reply_text(
            "Could not identify the achievement from that message. "
            "Reply to a single /info card.")
        return
    # Merge into the existing fields so the other field is preserved.
    fields = parse_notes(match.get('notes', ''))
    fields[field] = text
    await db.update_notes(match['name'], serialize_notes(fields))
    updated = next((a for a in db.get_achievements() if a['name'] == match['name']), match)
    await update.message.reply_text(
        "Note updated.\n\n" + format_single_achv(updated),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def clear_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_user(update.message.from_user.id):
        await update.message.reply_text("Only admins can edit notes.")
        return
    replied = update.message.reply_to_message
    if replied is None or not replied.text:
        await update.message.reply_text(
            "Reply to an achievement /info card with <code>/clearnote</code> "
            "(memo), <code>/clearnote prob</code>, or <code>/clearnote all</code>.",
            parse_mode=ParseMode.HTML)
        return
    match = _achv_from_reply(replied)
    if match is None:
        await update.message.reply_text(
            "Could not identify the achievement from that message. "
            "Reply to a single /info card.")
        return
    which = (context.args[0].lower() if context.args else "")
    if which == "all":
        targets = ["memo", "prob"]
    elif which in _PROB_KEYWORDS:
        targets = ["prob"]
    else:
        targets = ["memo"]
    fields = parse_notes(match.get('notes', ''))
    for key in targets:
        fields[key] = ""
    await db.update_notes(match['name'], serialize_notes(fields))
    updated = next((a for a in db.get_achievements() if a['name'] == match['name']), match)
    await update.message.reply_text(
        "Note updated.\n\n" + format_single_achv(updated),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# Telegram caps messages at 4096 chars; keep the SQL result well under that.
_DB_MAX_ROWS = 50
_DB_MAX_CHARS = 3500


def _format_sql_result(columns, rows, status):
    """Render a run_sql result as an HTML <pre> block for Telegram."""
    if not columns:
        # Non-SELECT (UPDATE/INSERT/DDL/...): just the command tag.
        return "<pre>{}</pre>".format(html.escape(status or "OK"))
    shown = rows[:_DB_MAX_ROWS]
    lines = [" | ".join(columns)]
    lines += [" | ".join("NULL" if v is None else str(v) for v in r) for r in shown]
    body = "\n".join(lines)
    if len(body) > _DB_MAX_CHARS:
        body = body[:_DB_MAX_CHARS] + "\n… (truncated)"
    footer = "\n({} row{})".format(len(rows), "" if len(rows) == 1 else "s")
    if len(rows) > len(shown):
        footer += ", showing first {}".format(len(shown))
    return "<pre>{}</pre>{}".format(html.escape(body), footer)


async def db_console_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superuser(update.message.from_user.id):
        await update.message.reply_text("Only the superuser can run raw SQL.")
        return
    # Take everything after the command verbatim (preserves newlines/whitespace),
    # rather than context.args which collapses whitespace.
    parts = update.message.text.split(None, 1)
    sql = parts[1].strip() if len(parts) > 1 else ""
    if not sql:
        await update.message.reply_text(
            "Usage: <code>/db &lt;sql&gt;</code>\nRuns a single SQL statement.",
            parse_mode=ParseMode.HTML)
        return
    print("%s - superuser (%d) - db %r" % (
        str(datetime.datetime.now() + datetime.timedelta(hours=8)),
        update.message.from_user.id, sql))
    try:
        columns, rows, status = await db.run_sql(sql)
    except Exception as e:
        await update.message.reply_text(
            "<b>SQL error:</b>\n<pre>{}</pre>".format(html.escape(str(e))),
            parse_mode=ParseMode.HTML)
        return
    # A statement that wasn't a plain SELECT may have changed the achievements
    # table; refresh the in-memory cache so the bot stays consistent.
    if not (status or "").upper().startswith("SELECT"):
        await db.load_cache()
    await update.message.reply_text(
        _format_sql_result(columns, rows, status),
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


# Public commands shown in Telegram's command menu (the "/" list and Menu
# button). Admin/superuser commands (addadmin, deladmin, admins, setnote, db)
# are intentionally omitted. Command aliases are omitted too — only the primary
# verb is listed to keep the menu clean.
PUBLIC_COMMANDS = [
    BotCommand("stats", "Your game stats (or reply to another player)"),
    BotCommand("kills", "Players you've killed the most"),
    BotCommand("killedby", "Players who've killed you the most"),
    BotCommand("deaths", "Your most common causes of death"),
    BotCommand("search", "Search your attained achievements"),
    BotCommand("achievements", "List all achievements"),
    BotCommand("info", "Look up an achievement by name"),
    BotCommand("about", "About this bot"),
    BotCommand("start", "Start the bot in a private chat"),
]


async def _post_init(application: Application):
    # Bring up the database before reporting ready to k8s.
    await db.init_pool(DATABASE_URL)
    await db.ensure_schema()
    await db.seed_achievements()
    await db.load_cache()
    await application.bot.set_my_commands(PUBLIC_COMMANDS)
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
    app.add_handler(CommandHandler('clearnote', clear_note_cmd))
    app.add_handler(CommandHandler('db', db_console_cmd))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_error_handler(error_handler)

    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
