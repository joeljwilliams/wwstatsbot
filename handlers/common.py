"""Helpers shared across more than one command family."""

import html


def resolve_target(update):
    """Resolve (user_id, name) from a message: reply target if present, else sender."""
    if update.message.reply_to_message is not None:
        user = update.message.reply_to_message.from_user
    else:
        user = update.message.from_user
    return user.id, html.escape(user.first_name)
