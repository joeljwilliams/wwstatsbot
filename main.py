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
import html
import re
import secrets

import httpx
import structlog
from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    MessageEntity,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes,
)

from unidecode import unidecode
import db
import health
import templates as t
import version
from logging_config import configure_logging

import wwstats

configure_logging()
logger = structlog.get_logger(__name__)

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

try:
    from config import REDIS_URL as _CFG_REDIS_URL
except ImportError:
    _CFG_REDIS_URL = None

BOT_TOKEN = os.environ.get("BOT_TOKEN", _CFG_TOKEN)
LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", _CFG_LOG_GROUP or 0)) or None
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
DATABASE_URL = os.environ.get("DATABASE_URL", _CFG_DATABASE_URL)
SUPERUSER_ID = int(os.environ.get("SUPERUSER_ID", _CFG_SUPERUSER_ID or 0)) or None
# Optional: enables durable /allinfo buttons (and any other bot_data) across
# restarts. Unset -> in-memory only.
REDIS_URL = os.environ.get("REDIS_URL", _CFG_REDIS_URL)

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
    logger.info("command", command="kills", user_id=user_id, user=unidecode(name))
    msg = await build_kills_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_killed_by(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, name = resolve_target(update)
    logger.info("command", command="killedby", user_id=user_id, user=unidecode(name))
    msg = await build_killed_by_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_deaths(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, name = resolve_target(update)
    logger.info("command", command="deaths", user_id=user_id, user=unidecode(name))
    msg = await build_deaths_msg(user_id, name)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# Cap the /search result list so a broad query can't approach Telegram's 4096
# char message limit; excess matches are summarised by a "…and N more" line.
_SEARCH_MAX_RESULTS = 10


async def display_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Replying to a bot message that mentions players means "check all of them",
    # not "check this message's author" — the author is the bot, whose own stats
    # are empty, so the single-player reading of the reply is never what was meant.
    if _is_bot_player_reply(update.message.reply_to_message):
        await display_search_all(update, context)
        return

    args = context.args
    user_id, name = resolve_target(update)
    logger.info("command", command="search", user_id=user_id, user=unidecode(name), args=args)

    # Search the full achievement list the same way /info does, then annotate
    # each match with whether the target user has already attained it.
    search = ' '.join(args)
    if not search:
        msg = "Invalid parameter! Syntax:\n<code>/search [achievement_to_search]</code>\n"
    elif len(search) < 3:
        msg = "Please enter at least 3 letters to search for!\n"
    else:
        matches = await build_info_results(search)
        attained_names = {a['name'] for a in await get_achievements(user_id)} if matches else set()
        # Drop inactive achievements the user hasn't obtained: they can no longer
        # be earned, so listing them as "not yet" would be misleading. (Inactive
        # ones the user already has are kept, so their collection stays complete.)
        matches = [m for m in matches
                   if not (m.get('inactive') and m['name'] not in attained_names)]
        if not matches:
            msg = "No matching achievements found!\n"
        else:
            msg = t.SEARCH_HEADER.format(query=html.escape(search), user_id=user_id, name=name)
            for m in matches[:_SEARCH_MAX_RESULTS]:
                mark = t.SEARCH_ATTAINED if m['name'] in attained_names else t.SEARCH_NOT_ATTAINED
                msg += t.SEARCH_ROW.format(mark=mark, name=html.escape(m['name']))
            if len(matches) > _SEARCH_MAX_RESULTS:
                msg += t.SEARCH_TRUNCATED.format(extra=len(matches) - _SEARCH_MAX_RESULTS)

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def _mentioned_users(message):
    """Extract (user_id, first_name) for every user directly mentioned in a message.

    Only text_mention entities are usable: they carry a full User (id + name).
    Plain @username mentions have no id, so the stats API (keyed by user id) can't
    be queried for them — those are returned separately as unresolvable names so
    the caller can report them rather than silently drop them.

    Returns (users, unresolved) where users is a de-duplicated, first-seen-ordered
    list of (id, name) and unresolved is a list of @username strings.
    """
    seen = set()
    users = []
    unresolved = []
    # Media messages carry their text in `caption` with caption_entities; plain
    # text messages use `text` with entities. Check both so either kind works.
    entities = list(message.entities or ()) + list(message.caption_entities or ())
    body = message.text if message.text is not None else (message.caption or "")
    for ent in entities:
        if ent.type == MessageEntity.TEXT_MENTION and ent.user is not None:
            u = ent.user
            # A mentioned bot has no player stats, so it could only ever land in
            # the "hasn't obtained it" list — noise, not an answer. Skip bots.
            if u.is_bot or u.id in seen:
                continue
            seen.add(u.id)
            users.append((u.id, u.first_name))
        elif ent.type == MessageEntity.MENTION:
            unresolved.append(body[ent.offset:ent.offset + ent.length])
    return users, unresolved


def _is_bot_player_reply(message):
    """True if `message` is a bot post that directly mentions at least one player.

    That shape — the game bot listing the players of a round — is the one case
    where checking *everyone* mentioned beats checking the message's author, so
    /sch routes itself to the multi-player path when it replies to one.
    """
    if message is None or message.from_user is None or not message.from_user.is_bot:
        return False
    users, _ = _mentioned_users(message)
    return bool(users)


async def _user_has_achievement(user_id, achv_name):
    """True if the player holds the named achievement per the stats API."""
    attained = {a['name'] for a in await get_achievements(user_id)}
    return achv_name in attained


# The two player lists are kept in bot_data so the toggle button can re-render
# either view without re-querying the stats API. Same fixed-size, token-keyed
# store as /allinfo: callback_data is capped at 64 bytes, so only a token fits.
_SCHALL_MAX = 200
_SCHALL_PREFIX = "schall:"
_SCHALL_HAVE = "have"
_SCHALL_MISSING = "missing"


def _store_schall_result(context, payload):
    """Stash a /schall result under a fresh token in bot_data; return the token."""
    store = context.bot_data.setdefault("schall", {})
    token = secrets.token_urlsafe(8)
    store[token] = payload
    while len(store) > _SCHALL_MAX:
        store.pop(next(iter(store)))  # evict oldest (dict preserves insertion order)
    return token


def _render_schall(payload, token, show_have):
    """Render one view of a /schall result: (message_html, toggle_keyboard).

    Only one bucket is listed at a time — the missing players by default — with a
    button that swaps to the other. Names are stored unescaped and escaped here,
    so a re-render after a persistence round-trip escapes exactly once.
    """
    missing, have = payload['missing'], payload['have']
    shown, other = (have, missing) if show_have else (missing, have)

    checked = len(missing) + len(have)
    msg = t.SCHALL_HEADER.format(
        name=html.escape(payload['name']), desc=html.escape(payload['desc']),
        count=checked, plural="" if checked == 1 else "s")
    section = t.SCHALL_HAVE_HEADER if show_have else t.SCHALL_MISSING_HEADER
    msg += section.format(count=len(shown))
    msg += "".join(
        t.SCHALL_USER_ROW.format(user_id=uid, name=html.escape(uname))
        for uid, uname in shown) or t.SCHALL_NONE_ROW
    if payload['unresolved']:
        msg += t.SCHALL_UNRESOLVED.format(
            names=", ".join(html.escape(n) for n in payload['unresolved']))

    label = t.SCHALL_TOGGLE_TO_MISSING if show_have else t.SCHALL_TOGGLE_TO_HAVE
    view = _SCHALL_MISSING if show_have else _SCHALL_HAVE
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
        label.format(count=len(other)),
        callback_data="{}{}:{}".format(_SCHALL_PREFIX, token, view))]])
    return msg, keyboard


async def display_search_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """<achievement> in reply to a message that mentions players.

    Reached two ways: /sch (and /search) route here on their own when replying to
    a bot message that mentions players — see _is_bot_player_reply — and /schall
    still calls it directly for anyone with the old spelling in muscle memory.

    Matches a single achievement the same way /info does, then lists the mentioned
    players who have *not* obtained it, with a button to toggle to those who have.
    """
    args = context.args
    requester_id = update.message.from_user.id
    requester_name = html.escape(update.message.from_user.first_name)
    replied = update.message.reply_to_message
    search = ' '.join(args)

    logger.info("command", command="schall", user_id=requester_id,
                user=unidecode(requester_name), args=args)

    if replied is None:
        await update.message.reply_text(t.SCHALL_NEED_REPLY, parse_mode=ParseMode.HTML)
        return
    if not search:
        await update.message.reply_text(t.SCHALL_USAGE, parse_mode=ParseMode.HTML)
        return
    if len(search) < 3:
        await update.message.reply_text("Please enter at least 3 letters to search for!\n")
        return

    # Single best match, exactly like /info (results are rank-ordered).
    found = await build_info_results(search)
    if not found:
        await update.message.reply_text("No matching achievements found!\n")
        return
    achv = found[0]

    users, unresolved = _mentioned_users(replied)
    if not users:
        note = ("Reply to a message that mentions players directly. "
                "I can't check plain @username mentions (they carry no user id).")
        await update.message.reply_text(note, parse_mode=ParseMode.HTML)
        return

    # Look up every mentioned player's achievements concurrently. A failed lookup
    # (network/API) shouldn't sink the whole command, so those users are reported
    # as uncheckable alongside any @username mentions.
    results = await asyncio.gather(
        *[_user_has_achievement(uid, achv['name']) for uid, _ in users],
        return_exceptions=True,
    )
    have, missing = [], []
    for (uid, uname), result in zip(users, results):
        if isinstance(result, Exception):
            logger.warning("schall_lookup_failed", user_id=uid, error=str(result))
            unresolved.append(uname)
        elif result:
            have.append((uid, uname))
        else:
            missing.append((uid, uname))

    # Both buckets are stored so the toggle can render either view; JSON-backed
    # persistence turns the (id, name) tuples into lists, which unpack the same.
    payload = {
        'name': achv['name'], 'desc': achv['desc'],
        'missing': missing, 'have': have, 'unresolved': unresolved,
    }
    token = _store_schall_result(context, payload)
    msg, keyboard = _render_schall(payload, token, show_have=False)

    await update.message.reply_text(
        msg, reply_markup=keyboard, parse_mode=ParseMode.HTML,
        disable_web_page_preview=True)


async def schall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Swap a /schall message between the not-obtained and obtained lists."""
    query = update.callback_query
    user = query.from_user
    token, _, view = query.data[len(_SCHALL_PREFIX):].partition(":")
    show_have = view == _SCHALL_HAVE
    payload = context.bot_data.get("schall", {}).get(token)

    logger.info("callback", command="schall", user_id=user.id,
                user=unidecode(html.escape(user.first_name)),
                view=view, expired=payload is None)

    if payload is None:
        await query.answer(t.SCHALL_EXPIRED, show_alert=True)
        return

    msg, keyboard = _render_schall(payload, token, show_have)
    try:
        await query.edit_message_text(
            msg, reply_markup=keyboard, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True)
    except BadRequest:
        # Two people tapped the same button at once, so the message already shows
        # this view. Nothing to update — just acknowledge the tap.
        pass
    await query.answer()


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

    msg = await build_stats_msg(user_id, name, by_id=by_id)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def display_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "Use /stats for stats. Use /achievements or /achv for achivement list."
    msg += "\n\nThis is an actively maintained fork of the original `@wolfcardbot` "
    msg += "(originally by Carson True, later edited by @jeffffc)."
    msg += "\nSource for this maintained version: [{repo}]({repo})".format(repo=version.GITHUB_REPO)
    msg += "\nUse /version to see the exact running build."
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


async def display_version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = version.get_version_info()
    logger.info("command", command="version", user_id=update.message.from_user.id,
                commit=info["short_commit"], branch=info["branch"], source=info["source"])
    tmpl = t.VERSION_INFO_LINKED if info["commit_url"] else t.VERSION_INFO_PLAIN
    await update.message.reply_text(
        tmpl.format(**info), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def startme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == 'private':
        await update.message.reply_text("Thank you for starting me. "
                                        "Use /stats and /achievements to check your related stats!")
    else:
        return


async def display_achv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    name = html.escape(update.message.from_user.first_name)

    logger.info("command", command="achievements", user_id=user_id, user=unidecode(name))

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
    replied = update.message.reply_to_message

    # A bare /info replying to a bot means "info for everything that message
    # lists" — the game bot's Possible Achievements post. Given arguments, or
    # replying to a human, it stays the single-achievement lookup below.
    if not args and replied is not None and replied.from_user is not None \
            and replied.from_user.is_bot:
        await all_info_cmd(update, context)
        return

    user_id = update.message.from_user.id
    name = html.escape(update.message.from_user.first_name)

    search = ""
    if len(args) > 0:
        search = ' '.join(args)
    elif replied and replied.text:
        search = replied.text

    logger.info("command", command="info", user_id=user_id, user=unidecode(name), args=args)

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


# A Possible Achievements message nests achievements under the player they're
# available to, and the two levels are told apart by indentation:
#
#   Possible Achievements:
#
#   Ren
#    - Traffic Control
#
# Both the indent and the space after the dash carry weight. Player names sit at
# the left margin and can themselves begin with a dash — "-Mini | ˹ʙᴜ..." is a
# real player — so matching any line that merely starts with "-" scoops names up
# as achievements, and they then get reported as unmatchable (or worse, fuzzy-match
# onto an unrelated achievement).
_ACHV_ROW = re.compile(r"^(?P<indent>[ \t]*)-+[ \t]+(?P<name>\S.*?)\s*$")


def _extract_possible_achievements(text):
    """Extract unique achievement names from a Possible Achievements message.

    Rows are indented dash bullets, e.g. " - Strongest Alpha". Indented rows win;
    a message with none falls back to unindented ones, so text that lost its
    leading spaces on the way in (a copy-paste, a client that trims) still works —
    the dash-then-space requirement keeps dash-prefixed player names out either
    way. Returns names in first-seen order, de-duplicated case-insensitively.
    """
    indented, flat = [], []
    for line in (text or "").splitlines():
        row = _ACHV_ROW.match(line)
        if row is None:
            continue
        (indented if row.group('indent') else flat).append(row.group('name'))

    seen = set()
    names = []
    for candidate in (indented or flat):
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(candidate)
    return names


def _best_achievement_match(name):
    """Find an achievement by exact case-insensitive name, then fuzzy fallback."""
    key = name.casefold()
    exact = next((a for a in db.get_achievements() if a['name'].casefold() == key), None)
    return exact


async def _resolve_achievement_cards(names):
    """Resolve achievement names to info cards. Returns (cards, not_found_names)."""
    cards = []
    not_found = []
    for name in names:
        match = _best_achievement_match(name)
        if match is None:
            fuzzy = await build_info_results(name)
            if fuzzy:
                match = fuzzy[0]
        if match is None:
            not_found.append(name)
            continue
        cards.append(format_single_achv(match))
    return cards, not_found


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


# Pending /info card sets: token -> list of achievement names. Populated when a
# bare /info replies to a list of achievements, consumed when a user taps the inline
# button so each interested user gets the cards in their own PM (no need to re-run
# the command). We store names (not rendered cards) and re-render on tap, so notes
# stay fresh and the payload is tiny.
#
# The store lives in application.bot_data, so with a persistence backend configured
# (see REDIS_URL) it survives restarts; without one it's in-memory and a stale button
# just reports "expired". The dict is bounded (insertion-ordered eviction).
_ALLINFO_MAX = 200
_ALLINFO_PREFIX = "allinfo:"
# callback_data is capped at 64 bytes, so a button carries only an action, the
# token, and — when paging — the card index it wants; the cards themselves are
# re-resolved from the token on every tap.
_ALLINFO_PM = "pm"      # allinfo:pm:<token>       — open the pager in the tapper's PM
_ALLINFO_PAGE = "p"     # allinfo:p:<token>:<idx>  — show card <idx>
_ALLINFO_ALL = "all"    # allinfo:all:<token>      — send every card as its own message
_ALLINFO_ACTIONS = (_ALLINFO_PM, _ALLINFO_PAGE, _ALLINFO_ALL)


def _store_allinfo_names(context, names):
    """Stash achievement names under a fresh token in bot_data; return the token."""
    store = context.bot_data.setdefault("allinfo", {})
    token = secrets.token_urlsafe(8)
    store[token] = names
    while len(store) > _ALLINFO_MAX:
        store.pop(next(iter(store)))  # evict oldest (dict preserves insertion order)
    return token


def _allinfo_unmatched(not_found):
    """The "couldn't match these names" line, capped so it can't run away."""
    names = ", ".join(html.escape(n) for n in not_found[:10])
    if len(not_found) > 10:
        names += ", ..."
    return t.ALLINFO_NOT_MATCHED.format(names=names)


def _render_allinfo_page(cards, index, token):
    """Render one card of a /info result set: (message_html, keyboard).

    Prev/Next wrap around modulo the card count, so the keyboard keeps the same
    shape on every page — a button never moves out from under the user's thumb at
    the ends of the list. A single card gets no keyboard at all: there is nothing
    to page through, and "send all" would just repeat what's already on screen.
    """
    total = len(cards)
    msg = cards[index] + t.ALLINFO_PAGE_FOOTER.format(index=index + 1, total=total)
    if total == 1:
        return msg, None

    def page(target):
        return "{}{}:{}:{}".format(_ALLINFO_PREFIX, _ALLINFO_PAGE, token, target % total)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t.ALLINFO_PREV, callback_data=page(index - 1)),
         InlineKeyboardButton(t.ALLINFO_NEXT, callback_data=page(index + 1))],
        [InlineKeyboardButton(
            t.ALLINFO_SEND_ALL.format(count=total),
            callback_data="{}{}:{}".format(_ALLINFO_PREFIX, _ALLINFO_ALL, token))],
    ])
    return msg, keyboard


async def _deliver_to_pm(context, query, sends):
    """Send (text, keyboard) pairs to the user who tapped `query`. True on success.

    A failure is almost always that the user has never started the bot in PM, so
    we can't message them at all. A callback answer can't carry a button, so the
    alert spells out the fix and they can tap again once the chat exists.
    """
    try:
        for text, keyboard in sends:
            await context.bot.send_message(
                chat_id=query.from_user.id, text=text, reply_markup=keyboard,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception:
        await query.answer(t.ALLINFO_NO_PM, show_alert=True)
        return False
    return True


async def all_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    name = html.escape(update.message.from_user.first_name)
    replied = update.message.reply_to_message

    logger.info("command", command="allinfo", user_id=user_id, user=unidecode(name))

    if replied is None:
        await update.message.reply_text(t.ALLINFO_NEED_REPLY, parse_mode=ParseMode.HTML)
        return

    source_text = replied.text or replied.caption or ""
    achv_names = _extract_possible_achievements(source_text)
    if not achv_names:
        await update.message.reply_text(t.ALLINFO_NO_ACHIEVEMENTS, parse_mode=ParseMode.HTML)
        return

    cards, not_found = await _resolve_achievement_cards(achv_names)
    if not cards:
        await update.message.reply_text(t.ALLINFO_NO_MATCH)
        return

    token = _store_allinfo_names(context, achv_names)

    # In a private chat the pager can go straight into the conversation; offering
    # to PM someone who is already in their PM would just add a hop.
    if update.message.chat.type == 'private':
        if not_found:
            await update.message.reply_text(
                _allinfo_unmatched(not_found), parse_mode=ParseMode.HTML)
        msg, keyboard = _render_allinfo_page(cards, 0, token)
        await update.message.reply_text(
            msg, reply_markup=keyboard, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True)
        return

    # In a group, post one message with a button instead: everyone who wants the
    # cards taps it and gets their own pager in PM, rather than one person's
    # paging being visible to — and shared with — the whole chat.
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
        t.ALLINFO_PM_BUTTON,
        callback_data="{}{}:{}".format(_ALLINFO_PREFIX, _ALLINFO_PM, token))]])
    prompt = t.ALLINFO_PROMPT.format(
        count=len(cards), plural="" if len(cards) == 1 else "s")
    if not_found:
        prompt += "\n\n" + _allinfo_unmatched(not_found)
    await update.message.reply_text(
        prompt, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def all_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Serve the /info card pager: open it in a PM, page it, or send every card."""
    query = update.callback_query
    user = query.from_user
    action, _, rest = query.data[len(_ALLINFO_PREFIX):].partition(":")
    if action not in _ALLINFO_ACTIONS:
        # A button posted before the pager existed carried a bare token, and its
        # message may still be sitting in a group. Treat it as the PM hand-off.
        action, rest = _ALLINFO_PM, query.data[len(_ALLINFO_PREFIX):]
    token, _, raw_index = rest.partition(":")
    names = context.bot_data.get("allinfo", {}).get(token)

    logger.info("callback", command="allinfo", user_id=user.id,
                user=unidecode(html.escape(user.first_name)), action=action,
                count=len(names) if names else 0, expired=names is None)

    if names is None:
        await query.answer(t.ALLINFO_EXPIRED, show_alert=True)
        return

    # Re-render now so notes reflect the latest edits.
    cards, _ = await _resolve_achievement_cards(names)
    if not cards:
        await query.answer(t.ALLINFO_GONE, show_alert=True)
        return

    if action == _ALLINFO_PAGE:
        # Modulo rather than a bounds check: a /setnote edit between taps can
        # change how many names still resolve, and the index in the button the
        # user just tapped was written before that.
        index = int(raw_index) % len(cards) if raw_index.isdigit() else 0
        msg, keyboard = _render_allinfo_page(cards, index, token)
        try:
            await query.edit_message_text(
                msg, reply_markup=keyboard, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True)
        except BadRequest:
            # Two taps raced and the message already shows this card. Nothing to
            # update — just acknowledge the tap.
            pass
        await query.answer()
        return

    if action == _ALLINFO_ALL:
        if await _deliver_to_pm(context, query, [(card, None) for card in cards]):
            await query.answer(t.ALLINFO_SENT_ALL.format(
                count=len(cards), plural="" if len(cards) == 1 else "s"))
        return

    if await _deliver_to_pm(context, query, [_render_allinfo_page(cards, 0, token)]):
        await query.answer(t.ALLINFO_SENT_PAGER)


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
    logger.info("command", command="db", user_id=update.message.from_user.id, sql=sql)
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
    logger.error("update_error", exc_info=error)
    if not LOG_GROUP_ID:
        return
    try:
        await context.bot.send_message(LOG_GROUP_ID, str(error), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("log_group_report_failed")


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

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
    )
    # Durable persistence for bot_data (e.g. /allinfo buttons survive restarts) when
    # a Redis backend is configured; otherwise state is in-memory only.
    if REDIS_URL:
        from redis_persistence import RedisPersistence
        builder = builder.persistence(RedisPersistence(url=REDIS_URL))
        logger.info("persistence_enabled", backend="redis")
    else:
        logger.info("persistence_disabled")
    app = builder.build()

    app.add_handler(CommandHandler('start', startme))
    app.add_handler(CommandHandler('stats', display_stats))
    app.add_handler(CommandHandler('kills', display_kills))
    app.add_handler(CommandHandler('killedby', display_killed_by))
    app.add_handler(CommandHandler('deaths', display_deaths))
    app.add_handler(CommandHandler(['search', 'sch'], display_search))
    app.add_handler(CommandHandler('schall', display_search_all))
    app.add_handler(CommandHandler('about', display_about))
    app.add_handler(CommandHandler('version', display_version))
    app.add_handler(CommandHandler(['achievements', 'achv'], display_achv))
    app.add_handler(CommandHandler(['info', 'getachv'], display_achv_info))
    app.add_handler(CommandHandler('allinfo', all_info_cmd))
    app.add_handler(CallbackQueryHandler(all_info_callback, pattern=r"^allinfo:"))
    app.add_handler(CallbackQueryHandler(schall_callback, pattern=r"^schall:"))
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
