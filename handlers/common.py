"""Helpers shared across more than one command family."""

import html

from telegram import MessageEntity

import db
import settings


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
