"""Helpers shared across more than one command family."""

import html

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
