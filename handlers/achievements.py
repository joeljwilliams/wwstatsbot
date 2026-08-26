"""Achievement lookup: /achievements, /info, /getachv and the /info card pager.

A bare /info replying to a bot means "info for everything that message lists" — the game
bot's Possible Achievements post — so display_achv_info reroutes to all_info_cmd. That
pair is kept in one module deliberately (see handlers/__init__.py).

The pager keeps its state in bot_data under a short token because callback_data is capped
at 64 bytes; the store is bounded, so an expired token is ordinary traffic rather than an
edge case.
"""

import html
import random
import re
import secrets

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from unidecode import unidecode

import api
import builders
import db
import templates as t
import wwstats
from handlers.common import mention_map

logger = structlog.get_logger(__name__)


async def display_achv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    name = html.escape(update.message.from_user.first_name)

    logger.info("command", command="achievements", user_id=user_id, user=unidecode(name))

    msgs = await wwstats.check(user_id, api.client)

    try:
        for msg in msgs:
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.MARKDOWN)
        if update.message.chat.type != "private":
            await update.message.reply_text(t.ACHV_SENT_TO_PM)
    except Exception:
        url = "telegram.me/{}".format(context.bot.username)
        keyboard = [[InlineKeyboardButton(t.START_ME_BUTTON, url=url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(t.ACHV_NEEDS_PM, reply_markup=reply_markup)


async def display_achv_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    replied = update.message.reply_to_message

    # A bare /info replying to a bot means "info for everything that message
    # lists" — the game bot's Possible Achievements post. Given arguments, or
    # replying to a human, it stays the single-achievement lookup below.
    if not args and replied is not None and replied.from_user is not None and replied.from_user.is_bot:
        await all_info_cmd(update, context)
        return

    user_id = update.message.from_user.id
    name = html.escape(update.message.from_user.first_name)

    search = ""
    if len(args) > 0:
        search = " ".join(args)
    elif replied and replied.text:
        search = replied.text

    logger.info("command", command="info", user_id=user_id, user=unidecode(name), args=args)

    if len(search) == 0:
        msg = t.INFO_USAGE
    elif len(search) < 3:
        msg = t.QUERY_TOO_SHORT
    else:
        found = await builders.build_info_results(search)
        if not found:
            msg = t.NO_MATCHES
        else:
            # Results are rank-ordered (name hits first), so the top match is the
            # best answer — show it rather than making the user pick from a list.
            msg = builders.format_single_achv(found[0])

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def _achv_from_reply(replied):
    """Find the achievement whose /info card was replied to, by its title line
    (the first non-empty line of the card's plain text). None if no match."""
    title = next((line.strip() for line in replied.text.splitlines() if line.strip()), "")
    return next((a for a in db.get_achievements() if a["name"] == title), None)


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

# A row may carry a status marker between the dash and the name — the stand-in session's
# own post uses one to separate "the game can produce this" from "…if you get lucky" and
# "…if your role changes". Without stripping it the name never matches exactly, and the
# fuzzy fallback then answers with a *different* achievement, which is worse than not
# answering: the reader has no way to tell they were given the wrong card.
#
# Safe because no achievement name begins with punctuation — test_achv_parsing pins that
# against the real list, so this can only ever remove a marker, never part of a name.
#
# The trailing alternation matters: _ACHV_ROW has already stripped the line's trailing
# whitespace, so a row that is *only* a marker arrives as a bare glyph with no space after
# it. Requiring the space would leave it standing as a one-character "name", which then
# fuzzy-matches onto whatever it happens to be closest to.
_ROW_MARKER = re.compile(r"^[^\w\s]+(?:\s+|$)")


def _extract_possible_achievements(text):
    """Extract unique achievement names from a Possible Achievements message.

    Rows are indented dash bullets, e.g. " - Strongest Alpha". Indented rows win;
    a message with none falls back to unindented ones, so text that lost its
    leading spaces on the way in (a copy-paste, a client that trims) still works —
    the dash-then-space requirement keeps dash-prefixed player names out either
    way. A leading status marker (" - ❓ Wuffie-Cult") is stripped. Returns names in
    first-seen order, de-duplicated case-insensitively.
    """
    indented, flat = [], []
    for line in (text or "").splitlines():
        row = _ACHV_ROW.match(line)
        if row is None:
            continue
        name = _ROW_MARKER.sub("", row.group("name"))
        if not name:
            continue
        (indented if row.group("indent") else flat).append(name)

    seen = set()
    names = []
    for candidate in indented or flat:
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(candidate)
    return names


# A Possible Achievements post has two shapes in it, and /roll needs both. Rows nested
# under a player name are that player's:
#
#   Mango
#    - Did you guard yourself?
#
# and, at the bottom, achievements no role gates, named once with everyone who can still
# get them:
#
#   In for the Long Haul (3):
#   ieb 🪼, Infinite, D_Evil_SK
#
_GROUP_HEADER = re.compile(r"^(?P<name>\S.*?)\s*\((?P<count>\d+)\):$")

# An unindented line ending in a colon is a section heading, not somebody's name —
# "Possible Achievements:", "Dead Players:", "Anyone:". Player names do not end in colons,
# and treating one as a player would put a heading into a roll.
_SECTION_HEADING = re.compile(r"^\S.*:$")


def _extract_by_player(text):
    """Read a Possible Achievements post into (per_player, groups).

    `per_player` is an ordered list of (player, [achievement, ...]) and `groups` maps an
    achievement to the players listed under it at the bottom of the post. Both are needed
    because the same question — "who can still get this?" — is answered by whichever shape
    the achievement happens to be printed in.

    Players with no rows are dropped, which is what removes the headings and any stray
    line that survived the shapes above.
    """
    per_player = []
    groups = {}
    current = None
    pending_group = None

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        row = _ACHV_ROW.match(line)
        if row is not None and row.group("indent"):
            name = _ROW_MARKER.sub("", row.group("name"))
            if name and current is not None:
                current[1].append(name)
            continue

        group = _GROUP_HEADER.match(stripped)
        if group is not None:
            pending_group = group.group("name")
            current = None
            continue

        if pending_group is not None:
            # The line after a group heading is its comma-separated list of players.
            groups[pending_group] = [part.strip() for part in stripped.split(",") if part.strip()]
            pending_group = None
            continue

        if _SECTION_HEADING.match(stripped):
            current = None
            continue

        current = (stripped, [])
        per_player.append(current)

    return [(player, rows) for player, rows in per_player if rows], groups


def _players_who_can_get(text, achievement):
    """Everyone in the post who can still earn `achievement`, in the order listed."""
    per_player, groups = _extract_by_player(text)
    key = achievement.casefold()

    found = [player for player, rows in per_player if any(row.casefold() == key for row in rows)]
    for name, players in groups.items():
        if name.casefold() == key:
            found += [p for p in players if p not in found]
    return found


def _listed_names(text):
    """Every achievement the post names, in order, without duplicates."""
    per_player, groups = _extract_by_player(text)
    listed = []
    for _, rows in per_player:
        listed += rows
    listed += list(groups)

    seen, names = set(), []
    for name in listed:
        if name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


async def _listed_achievement(text, query):
    """The achievement in the post that `query` names: (name or None, ambiguous).

    Matched against what the post lists rather than the whole catalogue, because the answer
    has to be a name somebody can be rolled *for*. Exact first, then a unique substring.

    A query matching several listed achievements is refused rather than resolved to the
    first: picking one would decide a game on a coin toss nobody saw.

    A query matching none of them falls through to the same search /info and /sch use, so
    the ways people already refer to achievements keep working here — "dygy" is how the
    group says "Did you guard yourself?", and it only resolves through the initialism
    index. Whatever that search returns still has to be listed in the post; the search
    decides *which* achievement is meant, never who can get it.
    """
    names = _listed_names(text)
    key = query.casefold().strip()

    exact = [name for name in names if name.casefold() == key]
    if exact:
        return exact[0], False

    partial = [name for name in names if key in name.casefold()]
    if len(partial) == 1:
        return partial[0], False
    if len(partial) > 1:
        return None, True

    listed = {name.casefold(): name for name in names}
    for found in await builders.build_info_results(query):
        match = listed.get(found["name"].casefold())
        if match is not None:
            return match, False
    return None, False


def _best_achievement_match(name):
    """Find an achievement by exact case-insensitive name, then fuzzy fallback."""
    key = name.casefold()
    exact = next((a for a in db.get_achievements() if a["name"].casefold() == key), None)
    return exact


async def _resolve_achievement_cards(names):
    """Resolve achievement names to info cards. Returns (cards, not_found_names)."""
    cards = []
    not_found = []
    for name in names:
        match = _best_achievement_match(name)
        if match is None:
            fuzzy = await builders.build_info_results(name)
            if fuzzy:
                match = fuzzy[0]
        if match is None:
            not_found.append(name)
            continue
        cards.append(builders.format_single_achv(match))
    return cards, not_found


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
_ALLINFO_PM = "pm"  # allinfo:pm:<token>       — open the pager in the tapper's PM
_ALLINFO_PAGE = "p"  # allinfo:p:<token>:<idx>  — show card <idx>
_ALLINFO_ALL = "all"  # allinfo:all:<token>      — send every card as its own message
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

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t.ALLINFO_PREV, callback_data=page(index - 1)),
                InlineKeyboardButton(t.ALLINFO_NEXT, callback_data=page(index + 1)),
            ],
            [
                InlineKeyboardButton(
                    t.ALLINFO_SEND_ALL.format(count=total),
                    callback_data="{}{}:{}".format(_ALLINFO_PREFIX, _ALLINFO_ALL, token),
                )
            ],
        ]
    )
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
                chat_id=query.from_user.id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception:
        await query.answer(t.ALLINFO_NO_PM, show_alert=True)
        return False
    return True


def _as_mention(name, mentions):
    """A player's name, tappable when the post it came from mentioned them.

    The names in a roll are read back out of somebody else's message, so an id only exists
    if that message carried one. When it did, saying who won should be as tappable as
    every other name this bot prints; when it did not, the plain name is all there is and
    inventing a link would point at nobody.
    """
    user_id = mentions.get(name)
    escaped = html.escape(name)
    return t.ROLL_MENTION.format(user_id=user_id, name=escaped) if user_id else escaped


def _pick(candidates):
    """Choose a winner. Wrapped so a test can make the roll deterministic."""
    return random.choice(candidates)


async def roll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/roll <achievement>` in reply to a Possible Achievements post.

    Several achievements can only be had once per game — one player guards the wolf, one
    gets the tanner lynched — so when the post lists the same one under several people,
    somebody has to decide who chases it. This does that, out loud: the candidates are
    named before the winner, so the roll is visibly between *those* people rather than an
    assertion nobody can check.

    The candidates come from the post itself rather than from the achievement catalogue.
    Both shapes count: rows nested under a player, and the group sections at the bottom
    that name everyone who can still get a roleless one.
    """
    message = update.message
    user = message.from_user
    replied = message.reply_to_message
    query = " ".join(context.args)

    logger.info("command", command="roll", user_id=user.id, user=unidecode(user.first_name), args=context.args)

    if replied is None or not query:
        await message.reply_text(t.ROLL_USAGE, parse_mode=ParseMode.HTML)
        return

    source = replied.text or replied.caption or ""
    listed, ambiguous = await _listed_achievement(source, query)
    if ambiguous:
        await message.reply_text(t.ROLL_AMBIGUOUS.format(name=html.escape(query)), parse_mode=ParseMode.HTML)
        return
    if listed is None:
        per_player, groups = _extract_by_player(source)
        template = t.ROLL_NO_LIST if not per_player and not groups else t.ROLL_NOT_LISTED
        await message.reply_text(template.format(name=html.escape(query)), parse_mode=ParseMode.HTML)
        return

    candidates = _players_who_can_get(source, listed)
    if not candidates:
        await message.reply_text(t.ROLL_NOT_LISTED.format(name=html.escape(listed)), parse_mode=ParseMode.HTML)
        return

    mentions = mention_map(replied)

    if len(candidates) == 1:
        await message.reply_text(
            t.ROLL_ONLY_ONE.format(winner=_as_mention(candidates[0], mentions), name=html.escape(listed)),
            parse_mode=ParseMode.HTML,
        )
        return

    winner = _pick(candidates)
    # Logged with the field it was drawn from, so a disputed roll can be checked after the
    # fact rather than argued about.
    logger.info("roll", achievement=listed, candidates=len(candidates), winner=unidecode(winner))
    await message.reply_text(
        t.ROLL_RESULT.format(
            name=html.escape(listed),
            players=", ".join(_as_mention(name, mentions) for name in candidates),
            winner=_as_mention(winner, mentions),
        ),
        parse_mode=ParseMode.HTML,
    )


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
    if update.message.chat.type == "private":
        if not_found:
            await update.message.reply_text(_allinfo_unmatched(not_found), parse_mode=ParseMode.HTML)
        msg, keyboard = _render_allinfo_page(cards, 0, token)
        await update.message.reply_text(
            msg, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        return

    # In a group, post one message with a button instead: everyone who wants the
    # cards taps it and gets their own pager in PM, rather than one person's
    # paging being visible to — and shared with — the whole chat.
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t.ALLINFO_PM_BUTTON, callback_data="{}{}:{}".format(_ALLINFO_PREFIX, _ALLINFO_PM, token)
                )
            ]
        ]
    )
    prompt = t.ALLINFO_PROMPT.format(count=len(cards), plural="" if len(cards) == 1 else "s")
    if not_found:
        prompt += "\n\n" + _allinfo_unmatched(not_found)
    await update.message.reply_text(
        prompt, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def all_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Serve the /info card pager: open it in a PM, page it, or send every card."""
    query = update.callback_query
    user = query.from_user
    action, _, rest = query.data[len(_ALLINFO_PREFIX) :].partition(":")
    if action not in _ALLINFO_ACTIONS:
        # A button posted before the pager existed carried a bare token, and its
        # message may still be sitting in a group. Treat it as the PM hand-off.
        action, rest = _ALLINFO_PM, query.data[len(_ALLINFO_PREFIX) :]
    token, _, raw_index = rest.partition(":")
    names = context.bot_data.get("allinfo", {}).get(token)

    logger.info(
        "callback",
        command="allinfo",
        user_id=user.id,
        user=unidecode(html.escape(user.first_name)),
        action=action,
        count=len(names) if names else 0,
        expired=names is None,
    )

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
                msg, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except BadRequest:
            # Two taps raced and the message already shows this card. Nothing to
            # update — just acknowledge the tap.
            pass
        await query.answer()
        return

    if action == _ALLINFO_ALL:
        if await _deliver_to_pm(context, query, [(card, None) for card in cards]):
            await query.answer(t.ALLINFO_SENT_ALL.format(count=len(cards), plural="" if len(cards) == 1 else "s"))
        return

    if await _deliver_to_pm(context, query, [_render_allinfo_page(cards, 0, token)]):
        await query.answer(t.ALLINFO_SENT_PAGER)
