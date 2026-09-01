"""Achievement search: /search, /sch and the multi-player /schall.

/sch reroutes to display_search_all when it replies to a bot message that mentions
players — the game bot listing a round's participants. That call stays intra-module by
design (see handlers/__init__.py), so it remains an ordinary global lookup and the test
suite can patch it in place.
"""

import asyncio
import html
import secrets
import time

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from unidecode import unidecode

import api
import builders
import templates as t
from handlers.common import is_admin_user, mentioned_users, resolve_target

logger = structlog.get_logger(__name__)


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
    search = " ".join(args)
    if not search:
        msg = t.SEARCH_USAGE
    else:
        matches = await builders.build_info_results(search)
        attained_names = {a["name"] for a in await api.get_achievements(user_id)} if matches else set()
        # Drop inactive achievements the user hasn't obtained: they can no longer
        # be earned, so listing them as "not yet" would be misleading. (Inactive
        # ones the user already has are kept, so their collection stays complete.)
        matches = [m for m in matches if not (m.get("inactive") and m["name"] not in attained_names)]
        if not matches:
            msg = t.NO_MATCHES
        else:
            msg = t.SEARCH_HEADER.format(query=html.escape(search), user_id=user_id, name=name)
            for m in matches[:_SEARCH_MAX_RESULTS]:
                mark = t.SEARCH_ATTAINED if m["name"] in attained_names else t.SEARCH_NOT_ATTAINED
                msg += t.SEARCH_ROW.format(mark=mark, name=html.escape(m["name"]))
            if len(matches) > _SEARCH_MAX_RESULTS:
                msg += t.SEARCH_TRUNCATED.format(extra=len(matches) - _SEARCH_MAX_RESULTS)

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def _is_bot_player_reply(message):
    """True if `message` is a bot post that directly mentions at least one player.

    That shape — the game bot listing the players of a round — is the one case
    where checking *everyone* mentioned beats checking the message's author, so
    /sch routes itself to the multi-player path when it replies to one.
    """
    if message is None or message.from_user is None or not message.from_user.is_bot:
        return False
    users, _ = mentioned_users(message)
    return bool(users)


async def _user_has_achievement(user_id, achv_name):
    """True if the player holds the named achievement per the stats API."""
    attained = {a["name"] for a in await api.get_achievements(user_id)}
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


# /schall with no reply re-uses the players from this chat's last reply-based run, so
# checking a second achievement against the same roster doesn't mean scrolling back to the
# player list. It lives in chat_data, which is per-chat (one group's line-up can never leak
# into another) and is persisted by RedisPersistence when REDIS_URL is set.
#
# It expires after an hour: a game group's roster changes every round, and silently checking
# last night's players would be worse than refusing. The reply always says how old the list
# is, so even inside the hour a remembered result is never mistaken for a fresh one.
_SCHALL_CACHE_KEY = "schall_players"
_SCHALL_CACHE_TTL = 60 * 60
_SCHALL_CACHE_TTL_LABEL = t.SCHALL_TTL_LABEL.format(count=_SCHALL_CACHE_TTL // 60)


def _now():
    """Wall clock, wrapped so tests can control the cache's age."""
    return time.time()


def _describe_age(seconds):
    """Compact age for the cache notice: "just now", "12m ago"."""
    minutes = int(seconds // 60)
    return "just now" if minutes < 1 else "{}m ago".format(minutes)


def _remember_players(context, users, unresolved):
    """Cache this chat's player list. Stored JSON-serializable for persistence."""
    context.chat_data[_SCHALL_CACHE_KEY] = {
        "users": [[uid, name] for uid, name in users],
        "unresolved": list(unresolved),
        "at": _now(),
    }


def _recall_players(context):
    """This chat's remembered players as (users, unresolved, age_seconds).

    Returns None when nothing is remembered, and ("stale") when what is remembered is
    older than the TTL — the caller distinguishes the two because "reply to a list" and
    "your list expired" are different things to be told.
    """
    cached = context.chat_data.get(_SCHALL_CACHE_KEY)
    if not cached:
        return None
    age = _now() - cached["at"]
    if age > _SCHALL_CACHE_TTL:
        # Drop it rather than leave it to be re-checked on every future call.
        context.chat_data.pop(_SCHALL_CACHE_KEY, None)
        return "stale"
    # JSON turns the stored pairs into lists; normalise back to tuples so the rest of the
    # handler cannot tell a cached run from a fresh one.
    users = [(uid, name) for uid, name in cached["users"]]
    return users, list(cached["unresolved"]), age


def _render_schall(payload, token, show_have):
    """Render one view of a /schall result: (message_html, toggle_keyboard).

    Only one bucket is listed at a time — the missing players by default — with a
    button that swaps to the other. Names are stored unescaped and escaped here,
    so a re-render after a persistence round-trip escapes exactly once.
    """
    missing, have = payload["missing"], payload["have"]
    shown, other = (have, missing) if show_have else (missing, have)

    checked = len(missing) + len(have)
    msg = t.SCHALL_HEADER.format(
        name=html.escape(payload["name"]),
        desc=html.escape(payload["desc"]),
        count=checked,
        plural="" if checked == 1 else "s",
    )
    # Payloads stored before this field existed have no key, hence .get().
    if payload.get("from_cache_age"):
        msg += t.SCHALL_FROM_CACHE.format(age=payload["from_cache_age"])
    section = t.SCHALL_HAVE_HEADER if show_have else t.SCHALL_MISSING_HEADER
    msg += section.format(count=len(shown))
    msg += (
        "".join(t.SCHALL_USER_ROW.format(user_id=uid, name=html.escape(uname)) for uid, uname in shown)
        or t.SCHALL_NONE_ROW
    )
    if payload["unresolved"]:
        msg += t.SCHALL_UNRESOLVED.format(names=", ".join(html.escape(n) for n in payload["unresolved"]))

    label = t.SCHALL_TOGGLE_TO_MISSING if show_have else t.SCHALL_TOGGLE_TO_HAVE
    view = _SCHALL_MISSING if show_have else _SCHALL_HAVE
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label.format(count=len(other)), callback_data="{}{}:{}".format(_SCHALL_PREFIX, token, view)
                )
            ]
        ]
    )
    return msg, keyboard


async def display_search_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """<achievement> in reply to a message that mentions players.

    Reached three ways: /sch (and /search) route here on their own when replying to a bot
    message that mentions players — see _is_bot_player_reply — /schall calls it directly
    with a reply, and /schall *without* a reply re-checks this chat's remembered list.

    /sch with no reply deliberately still means "check my own achievements"; only /schall
    reads the cache, so the advertised command keeps its established meaning.

    Matches a single achievement the same way /info does, then lists the mentioned
    players who have *not* obtained it, with a button to toggle to those who have.
    """
    args = context.args
    requester_id = update.message.from_user.id
    requester_name = html.escape(update.message.from_user.first_name)
    replied = update.message.reply_to_message
    search = " ".join(args)

    logger.info("command", command="schall", user_id=requester_id, user=unidecode(requester_name), args=args)

    if not search:
        await update.message.reply_text(t.SCHALL_USAGE, parse_mode=ParseMode.HTML)
        return

    # Where the players come from: a reply, or this chat's remembered list.
    cached_age = None
    if replied is not None:
        users, unresolved = mentioned_users(replied)
        if users:
            # Only remember a list that is actually checkable, so replying to a message
            # of bare @usernames cannot wipe a good one.
            _remember_players(context, users, unresolved)
    else:
        remembered = _recall_players(context)
        if remembered is None:
            await update.message.reply_text(
                t.SCHALL_NO_REPLY_NO_CACHE.format(ttl=_SCHALL_CACHE_TTL_LABEL), parse_mode=ParseMode.HTML
            )
            return
        if remembered == "stale":
            await update.message.reply_text(
                t.SCHALL_CACHE_STALE.format(ttl=_SCHALL_CACHE_TTL_LABEL), parse_mode=ParseMode.HTML
            )
            return
        users, unresolved, cached_age = remembered

    if not users:
        await update.message.reply_text(t.SCHALL_NEEDS_DIRECT_MENTIONS, parse_mode=ParseMode.HTML)
        return

    # Single best match, exactly like /info (results are rank-ordered).
    found = await builders.build_info_results(search)
    if not found:
        await update.message.reply_text(t.NO_MATCHES)
        return
    achv = found[0]

    # Look up every mentioned player's achievements concurrently. A failed lookup
    # (network/API) shouldn't sink the whole command, so those users are reported
    # as uncheckable alongside any @username mentions.
    results = await asyncio.gather(
        *[_user_has_achievement(uid, achv["name"]) for uid, _ in users],
        return_exceptions=True,
    )
    have, missing = [], []
    # strict=True can never trigger — gather returns exactly one result per awaitable —
    # but it keeps the pairing honest if either side is ever built separately.
    for (uid, uname), result in zip(users, results, strict=True):
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
        "name": achv["name"],
        "desc": achv["desc"],
        "missing": missing,
        "have": have,
        "unresolved": unresolved,
        # Who may work the toggle. Stored rather than read from the callback's message,
        # because Telegram does not tell us who sent the message a button is attached to.
        "requested_by": requester_id,
        "requested_by_name": update.message.from_user.first_name,
        # None on a fresh run. Frozen at run time on purpose — the toggle re-renders the
        # same result, so a growing age (eventually exceeding the TTL) would misdescribe it.
        "from_cache_age": None if cached_age is None else _describe_age(cached_age),
    }
    token = _store_schall_result(context, payload)
    msg, keyboard = _render_schall(payload, token, show_have=False)

    await update.message.reply_text(
        msg, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def _may_toggle(user_id, payload):
    """Whether `user_id` may flip this list's view.

    The requester may, because it is their question. Admins may, because they moderate.
    Anyone else taps and gets told — before this, whoever tapped last decided what
    everyone else saw, which in a busy group meant a list flipping under the person who
    asked for it.

    Payloads stored before this existed carry no owner. Those stay open to everyone rather
    than becoming unusable: with REDIS_URL set they survive a restart, and locking out the
    requester of a live message would be the worse failure.
    """
    owner = payload.get("requested_by")
    if owner is None or user_id == owner:
        return True
    # Only now is the database worth touching — the requester is the common case.
    return await is_admin_user(user_id)


async def schall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Swap a /schall message between the not-obtained and obtained lists."""
    query = update.callback_query
    user = query.from_user
    token, _, view = query.data[len(_SCHALL_PREFIX) :].partition(":")
    show_have = view == _SCHALL_HAVE
    payload = context.bot_data.get("schall", {}).get(token)

    logger.info(
        "callback",
        command="schall",
        user_id=user.id,
        user=unidecode(html.escape(user.first_name)),
        view=view,
        expired=payload is None,
    )

    if payload is None:
        await query.answer(t.SCHALL_EXPIRED, show_alert=True)
        return

    if not await _may_toggle(user.id, payload):
        # Answer without editing: the message keeps whichever view its owner chose.
        logger.info("schall_toggle_denied", user_id=user.id, owner=payload.get("requested_by"))
        await query.answer(
            t.SCHALL_NOT_YOURS.format(name=payload.get("requested_by_name") or t.SCHALL_REQUESTER_FALLBACK),
            show_alert=True,
        )
        return

    msg, keyboard = _render_schall(payload, token, show_have)
    try:
        await query.edit_message_text(
            msg, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    except BadRequest:
        # Two people tapped the same button at once, so the message already shows
        # this view. Nothing to update — just acknowledge the tap.
        pass
    await query.answer()
