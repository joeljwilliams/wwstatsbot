"""Helpers shared across more than one command family."""

import html

import structlog
from telegram import MessageEntity

import db
import settings

logger = structlog.get_logger(__name__)


def resolve_target(update):
    """Resolve (user_id, name) from a message: reply target if present, else sender."""
    if update.message.reply_to_message is not None:
        user = update.message.reply_to_message.from_user
    else:
        user = update.message.from_user
    return user.id, html.escape(user.first_name)


# Permission tiers. These live here rather than in handlers/admin.py because they are not
# an admin *command* — two families ask the question now: admin.py gates /setnote and /db,
# and search.py gates the /schall toggle button.


def is_superuser(user_id):
    return settings.SUPERUSER_ID is not None and user_id == settings.SUPERUSER_ID


async def is_admin_user(user_id):
    return is_superuser(user_id) or await db.is_admin(user_id)


async def is_chat_admin(context, chat_id, user_id):
    """Whether this user administrates the group. False if Telegram will not say.

    A group's own admins are the right authority for anything scoped to that group: they
    are usually the person who notices the session is still running after the game ended,
    or who decides whether the group wants joins announced at all. Neither question is
    answered by the bot-wide admin table, which is why this asks Telegram.
    """
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except Exception as exc:
        # An unreachable API must not hand a group's controls to someone with no claim
        # to them, so failure is "no" rather than an exception taking the command down.
        logger.warning("chat_admin_lookup_failed", chat_id=chat_id, user_id=user_id, error=str(exc))
        return False
    return getattr(member, "status", None) in ("administrator", "creator")


# --- This chat's player list -----------------------------------------------
#
# Who is playing in this chat, remembered between commands. It lives here rather than in
# handlers/search.py because two command families write it now: /schall remembers whoever a
# reply mentioned, and the stand-in session remembers the game bot's roster (see
# handlers/gamesession.py). /schall reads it either way, which is what lets
# `/schall <achievement>` work with no reply at all.
#
# In chat_data, so it is per-chat — one group's line-up can never leak into another's
# results — and persisted by RedisPersistence when REDIS_URL is set. Stored
# JSON-serializable for that reason, which is also why recall normalises pairs back to
# tuples: JSON turns them into lists.
#
# It expires, because a game group's line-up changes every round and silently checking last
# night's players would be worse than refusing. The age is always reported, so even inside
# the window a remembered result is never mistaken for a fresh one.
#
# The key still reads "schall_players": renaming it would orphan every list already in
# Redis, and the only thing that buys is a tidier string.
PLAYERS_KEY = "schall_players"
PLAYERS_TTL = 60 * 60


def describe_age(seconds):
    """Compact age for the remembered-list notice: "just now", "12m ago"."""
    minutes = int(seconds // 60)
    return "just now" if minutes < 1 else "{}m ago".format(minutes)


def remember_players(chat_data, users, unresolved, now):
    """Record this chat's player list. `now` comes from the caller's own clock."""
    chat_data[PLAYERS_KEY] = {
        "users": [[uid, name] for uid, name in users],
        "unresolved": list(unresolved),
        "at": now,
    }


def recall_players(chat_data, now):
    """This chat's remembered players as (users, unresolved, age_seconds).

    Returns None when nothing is remembered and the string "stale" when what is remembered
    is older than PLAYERS_TTL. The caller distinguishes the two because "reply to a list"
    and "your list expired" are different things to be told.
    """
    stored = chat_data.get(PLAYERS_KEY)
    if not stored:
        return None
    age = now - stored["at"]
    if age > PLAYERS_TTL:
        # Dropped rather than left to be re-checked on every future call.
        chat_data.pop(PLAYERS_KEY, None)
        return "stale"
    users = [(uid, name) for uid, name in stored["users"]]
    return users, list(stored["unresolved"]), age


def utf16_units(text):
    """`text` as UTF-16 code units — what Telegram entity offsets actually index.

    Offsets are counted in UTF-16 units, not characters, so every character outside the
    BMP costs two. Player names here are made of them ("J J 🎭", "𝑬𝒔𝒓𝒂"), so slicing by
    character reads one place further left for each one that came before.
    """
    return text.encode("utf-16-le")


def utf16_piece(units, offset, length):
    """The span an entity covers, from `utf16_units` output."""
    return units[offset * 2 : (offset + length) * 2].decode("utf-16-le", "ignore")


def mention_map(message):
    """The text of each mention in a message -> the user id behind it.

    A text_mention carries both: the User object, and the span of text it was written
    over. That makes a message that mentions people properly a lookup table from display
    name to id — which is what lets a name read back out of a message be rendered as a
    tappable mention rather than as flat text.
    """
    text = message.text if message.text is not None else (message.caption or "")
    units = utf16_units(text)
    found = {}
    entities = list(message.entities or ()) + list(message.caption_entities or ())
    for ent in entities:
        if ent.type == MessageEntity.TEXT_MENTION and ent.user is not None and not ent.user.is_bot:
            found[utf16_piece(units, ent.offset, ent.length).strip()] = ent.user.id
    return found


def mentioned_usernames(message):
    """@username (lowercased, no @) -> user_id, from a message's text_mention entities.

    A plain `@handle` in a message carries no user id, which is why they are normally
    uncheckable. A *text_mention* does: it holds the whole User object, username included.
    So a message that mentions people properly — the game bot's player list does — teaches
    us the handle-to-id mapping for everybody in it, and a later plain @handle can be
    resolved against what we learned.

    Only users who have set a username appear; the rest have nothing to key on.
    """
    found = {}
    for ent in list(message.entities or ()) + list(message.caption_entities or ()):
        if ent.type == MessageEntity.TEXT_MENTION and ent.user is not None:
            username = getattr(ent.user, "username", None)
            if username and not ent.user.is_bot:
                found[username.lower()] = ent.user.id
    return found


def mentioned_users(message):
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
            unresolved.append(body[ent.offset : ent.offset + ent.length])
    return users, unresolved
