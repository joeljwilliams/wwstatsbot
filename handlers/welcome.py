"""Announcing a joining player's record, and the /welcome switch that enables it.

Telegram delivers a service message when people join a group, and a stats bot has
something worth saying at that moment: how long this person has been playing and how much
of the achievement list they hold. That is a greeting the group can read, rather than one
more "X joined the chat".

It is **off until a group turns it on**. Shipping it on by default would have every group
the bot already sits in start being talked at by a deploy nobody in that group asked for,
which is how a bot gets removed. The switch is per chat and belongs to the group's own
admins — this is a decision about their room, not about the bot.
"""

import asyncio
import html

import structlog
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from unidecode import unidecode

import api
import builders
import templates as t
from handlers.common import is_admin_user, is_chat_admin

logger = structlog.get_logger(__name__)

# Per-chat, in chat_data, so one group's choice can never turn it on in another. Persisted
# by RedisPersistence when REDIS_URL is set: a restart must not silently switch a group's
# setting back, least of all back *on*.
_WELCOME_KEY = "welcome"

# A group merge can add dozens of people in one service message. Announcing each would
# bury the chat, so the message is bounded and says how many it left out.
_MAX_ANNOUNCED = 5

_GROUP_CHATS = ("group", "supergroup")


def is_enabled(context):
    """Whether this chat has asked for join announcements. Default off."""
    return bool(context.chat_data.get(_WELCOME_KEY))


async def welcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/welcome [on|off] — read or set this group's join announcements."""
    chat = update.message.chat
    user = update.message.from_user
    args = context.args
    logger.info("command", command="welcome", user_id=user.id, user=unidecode(user.first_name), args=args)

    if chat.type not in _GROUP_CHATS:
        await update.message.reply_text(t.WELCOME_GROUP_ONLY, parse_mode=ParseMode.HTML)
        return

    if not args:
        state = t.WELCOME_STATE_ON if is_enabled(context) else t.WELCOME_STATE_OFF
        await update.message.reply_text(t.WELCOME_USAGE.format(state=state), parse_mode=ParseMode.HTML)
        return

    wanted = args[0].lower()
    if wanted not in ("on", "off"):
        state = t.WELCOME_STATE_ON if is_enabled(context) else t.WELCOME_STATE_OFF
        await update.message.reply_text(t.WELCOME_USAGE.format(state=state), parse_mode=ParseMode.HTML)
        return

    # Asked only once a valid change is on the table, so reading the current state costs
    # nobody an API call. Bot admins are included because they are the ones who get told
    # when a group's own admins are unreachable.
    allowed = await is_chat_admin(context, chat.id, user.id) or await is_admin_user(user.id)
    if not allowed:
        await update.message.reply_text(t.WELCOME_ADMINS_ONLY, parse_mode=ParseMode.HTML)
        return

    context.chat_data[_WELCOME_KEY] = wanted == "on"
    logger.info("welcome_toggled", chat_id=chat.id, user_id=user.id, enabled=wanted == "on")
    await update.message.reply_text(t.WELCOME_ON if wanted == "on" else t.WELCOME_OFF, parse_mode=ParseMode.HTML)


async def _player_line(user):
    """One announcement line for a joining user, or None if we couldn't look them up.

    An unreachable stats API must not produce "has not played any games yet" — that is a
    statement about the player, and getting it wrong is worse than staying quiet.
    """
    stats = await api.get_stats(user.id)
    name = html.escape(user.first_name)
    if not stats:
        return t.WELCOME_NO_GAMES.format(user_id=user.id, name=name)
    achievements = await api.get_achievement_count(user.id)
    return t.WELCOME_PLAYER.format(
        name=name,
        role=builders.role_label(stats["mostCommonRole"]),
        # Grouped here rather than in the template: a format spec in a translatable
        # string breaks the "every template formats with its own fields" guard, and
        # 20,000 is read at a glance where 20000 has to be counted.
        games="{:,}".format(stats["gamesPlayed"]),
        achievements=achievements,
        url=api.player_url(user.id),
    )


async def greet_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Announce the players in a join service message, when the chat has asked for it."""
    if not is_enabled(context):
        # The common case by far, and it must cost nothing: this handler sees every join
        # in every group the bot is in.
        return

    # Bots have no player stats, so they could only ever be greeted as somebody who has
    # never played — including the moment this bot itself is added to a group.
    joined = [u for u in (update.message.new_chat_members or ()) if not u.is_bot]
    if not joined:
        return

    shown = joined[:_MAX_ANNOUNCED]
    lines = await asyncio.gather(*[_player_line(u) for u in shown], return_exceptions=True)

    msg = ""
    for user, line in zip(shown, lines, strict=True):
        if isinstance(line, Exception):
            # One failed lookup degrades to that player going ungreeted, not to the
            # whole join passing in silence.
            logger.warning("welcome_lookup_failed", user_id=user.id, error=str(line))
            continue
        msg += line
    if not msg:
        return
    if len(joined) > len(shown):
        msg += t.WELCOME_MORE.format(count=len(joined) - len(shown))
    # Once, at the bottom: it is aimed at the people who just arrived, not at any one of
    # their records. A player with no games needs it most, so it is not conditional on
    # having any.
    msg += t.WELCOME_HOUSE_RULES

    logger.info("welcome_announced", chat_id=update.message.chat.id, joined=len(joined), announced=len(shown))
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
