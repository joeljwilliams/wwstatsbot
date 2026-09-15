"""Privileged commands, in two tiers.

* **Superuser** (an env-var id comparison): /addadmin, /deladmin, /admins, /db, /setemoji.
* **Admin** (superuser or a row in the admins table): /setnote, /clearnote.

db.run_sql executes whatever SQL it is handed, so /db is safe *only* because of its
superuser gate. tests/test_permissions.py asserts the privileged functions are never
reached unauthorised, not merely that a refusal is printed.
"""

import html

import structlog
from telegram import MessageEntity, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from wwstatsbot.data import db, notes
from wwstatsbot.handlers.achievements import _achv_from_reply
from wwstatsbot.handlers.common import is_admin_user, is_superuser, utf16_piece, utf16_units
from wwstatsbot.render import badges, builders
from wwstatsbot.render import templates as t

logger = structlog.get_logger(__name__)


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
        await update.message.reply_text(t.ADMIN_ONLY_ADD)
        return
    target = _resolve_admin_target(update, context)
    if target is None:
        await update.message.reply_text(t.ADMIN_ADD_USAGE)
        return
    user_id, username, first_name = target
    await db.add_admin(user_id, username, first_name, update.message.from_user.id)
    label = html.escape(first_name) if first_name else str(user_id)
    await update.message.reply_text(
        t.ADMIN_ADDED.format(user_id=user_id, name=label),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def del_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superuser(update.message.from_user.id):
        await update.message.reply_text(t.ADMIN_ONLY_REMOVE)
        return
    target = _resolve_admin_target(update, context)
    if target is None:
        await update.message.reply_text(t.ADMIN_DEL_USAGE)
        return
    removed = await db.remove_admin(target[0])
    await update.message.reply_text(t.ADMIN_REMOVED.format(user_id=target[0]) if removed else t.ADMIN_NOT_AN_ADMIN)


async def list_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superuser(update.message.from_user.id):
        await update.message.reply_text(t.ADMIN_ONLY_LIST)
        return
    rows = await db.list_admins()
    if not rows:
        await update.message.reply_text(t.ADMIN_LIST_EMPTY)
        return
    lines = [t.ADMIN_LIST_HEADER]
    for r in rows:
        name = html.escape(r["first_name"]) if r["first_name"] else t.ADMIN_LIST_UNKNOWN_NAME
        uname = " @{}".format(html.escape(r["username"])) if r["username"] else ""
        lines.append(t.ADMIN_LIST_ROW.format(user_id=r["user_id"], name=name, username=uname))
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# A badge is one emoji, and the cap is on what a *name* can carry rather than on what
# Telegram would accept: these are printed beside sixteen names in one roster message, and
# something longer than a couple of glyphs stops being a badge and starts being a rename.
# Generous enough for a ZWJ sequence — a family or a flag is a dozen code points.
_BADGE_MAX = 24


def _premium_badge(message):
    """The first premium emoji in the command, as (fallback character, custom emoji id).

    A premium emoji arrives as *text plus an entity*: the plain emoji sits in the message
    like any other character, and the entity beside it carries the id of the animated one.
    Reading the text alone would silently store the fallback and lose the thing somebody
    actually paid for.

    Offsets are UTF-16 units, as everywhere else Telegram counts characters — an emoji is
    two of them, which is exactly what makes slicing the str wrong.
    """
    for entity in message.entities or ():
        if entity.type == MessageEntity.CUSTOM_EMOJI:
            fallback = utf16_piece(utf16_units(message.text or ""), entity.offset, entity.length)
            return fallback, entity.custom_emoji_id
    return None


def _badge_target(update, context):
    """(user_id, name, emoji) for /setemoji. None when it named nobody.

    The target is resolved the way /addadmin resolves one — a reply, or an id as the first
    argument — so the two privileged commands that act on a person agree about how a person
    is named. What is left of the text is the badge, and nothing left means "take it away".
    """
    target = _resolve_admin_target(update, context)
    if target is None:
        return None
    user_id, _, first_name = target
    # A reply spends no argument on the id, so every argument is the badge; an id typed as
    # the first argument spends one.
    words = context.args if update.message.reply_to_message is not None else context.args[1:]
    return user_id, first_name, " ".join(words).strip()


async def set_emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/setemoji <user id> <emoji>` — give a contributor a badge, or take it away.

    Superuser only, and deliberately not advertised in PUBLIC_COMMANDS.

    The confirmation is also the **test**. A premium emoji can only be sent by a bot that
    bought a username on Fragment; Telegram refuses it outright otherwise, and a badge it
    refuses would not fail here — it would fail later, in every roster, stats card and
    announcement naming that player, with nothing to say why. So the badge is rendered into
    a message *before* the row is written, and what gets stored is whichever form Telegram
    agreed to send.
    """
    message = update.message
    user = message.from_user
    if not is_superuser(user.id):
        await message.reply_text(t.EMOJI_ONLY_SUPERUSER)
        return

    logger.info("command", command="setemoji", user_id=user.id, args=context.args)

    target = _badge_target(update, context)
    if target is None:
        await message.reply_text(t.EMOJI_USAGE, parse_mode=ParseMode.HTML)
        return
    target_id, target_name, typed = target
    label = html.escape(target_name) if target_name else str(target_id)

    if not typed:
        cleared = await db.clear_badge(target_id)
        template = t.EMOJI_CLEARED if cleared else t.EMOJI_NOT_SET
        await message.reply_text(
            template.format(user_id=target_id, name=label), parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        return

    premium = _premium_badge(message)
    emoji, custom_id = premium if premium else (typed, None)
    if len(emoji) > _BADGE_MAX:
        await message.reply_text(t.EMOJI_TOO_LONG.format(count=len(emoji), limit=_BADGE_MAX))
        return

    reply = t.EMOJI_SET.format(user_id=target_id, name=label, badge=badges.markup(emoji, custom_id))
    try:
        await message.reply_text(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest as err:
        if custom_id is None:
            # Nothing to do with the badge, and not ours to swallow.
            raise
        logger.warning("badge_custom_emoji_refused", user_id=target_id, error=str(err))
        custom_id = None
        await message.reply_text(
            t.EMOJI_SET.format(user_id=target_id, name=label, badge=badges.markup(emoji, None)) + t.EMOJI_NOT_PREMIUM,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    # Written only once Telegram has agreed to render it.
    await db.set_badge(target_id, emoji, custom_id, target_name, user.id)
    logger.info("badge_set", user_id=target_id, premium=custom_id is not None, set_by=user.id)


async def set_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_user(update.message.from_user.id):
        await update.message.reply_text(t.ADMIN_ONLY_NOTES)
        return
    replied = update.message.reply_to_message
    if replied is None or not replied.text:
        await update.message.reply_text(
            t.NOTE_SET_USAGE,
            parse_mode=ParseMode.HTML,
        )
        return
    # Take the text after the command verbatim so line breaks in the note are
    # preserved (context.args tokenises on whitespace and would flatten them).
    parts = update.message.text.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    field, text = notes.split_note_field(arg)
    if not text:
        await update.message.reply_text(
            t.NOTE_SET_NEEDS_TEXT,
            parse_mode=ParseMode.HTML,
        )
        return
    match = _achv_from_reply(replied)
    if match is None:
        await update.message.reply_text(t.NOTE_UNIDENTIFIED)
        return
    # Merge into the existing fields so the other field is preserved.
    fields = notes.parse_notes(match.get("notes", ""))
    fields[field] = text
    await db.update_notes(match["name"], notes.serialize_notes(fields))
    updated = next((a for a in db.get_achievements() if a["name"] == match["name"]), match)
    await update.message.reply_text(
        t.NOTE_UPDATED + builders.format_single_achv(updated),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def clear_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_user(update.message.from_user.id):
        await update.message.reply_text(t.ADMIN_ONLY_NOTES)
        return
    replied = update.message.reply_to_message
    if replied is None or not replied.text:
        await update.message.reply_text(
            t.NOTE_CLEAR_USAGE,
            parse_mode=ParseMode.HTML,
        )
        return
    match = _achv_from_reply(replied)
    if match is None:
        await update.message.reply_text(t.NOTE_UNIDENTIFIED)
        return
    which = context.args[0].lower() if context.args else ""
    if which == "all":
        targets = ["memo", "prob"]
    elif notes.is_prob_keyword(which):
        targets = ["prob"]
    else:
        targets = ["memo"]
    fields = notes.parse_notes(match.get("notes", ""))
    for key in targets:
        fields[key] = ""
    await db.update_notes(match["name"], notes.serialize_notes(fields))
    updated = next((a for a in db.get_achievements() if a["name"] == match["name"]), match)
    await update.message.reply_text(
        t.NOTE_UPDATED + builders.format_single_achv(updated),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# Telegram caps messages at 4096 chars; keep the SQL result well under that.
_DB_MAX_ROWS = 50
_DB_MAX_CHARS = 3500


def _format_sql_result(columns, rows, status):
    """Render a run_sql result as an HTML <pre> block for Telegram."""
    if not columns:
        # Non-SELECT (UPDATE/INSERT/DDL/...): just the command tag.
        return t.DB_RESULT.format(body=html.escape(status or t.DB_STATUS_OK), footer="")
    shown = rows[:_DB_MAX_ROWS]
    lines = [" | ".join(columns)]
    # "NULL" is Postgres' own spelling of the value, not prose — it stays literal so a
    # translated console cannot misrepresent what the database returned.
    lines += [" | ".join("NULL" if v is None else str(v) for v in r) for r in shown]
    body = "\n".join(lines)
    if len(body) > _DB_MAX_CHARS:
        body = body[:_DB_MAX_CHARS] + t.DB_TRUNCATED
    footer = t.DB_ROW_COUNT.format(count=len(rows), plural="" if len(rows) == 1 else "s")
    if len(rows) > len(shown):
        footer += t.DB_ROWS_SHOWN.format(count=len(shown))
    return t.DB_RESULT.format(body=html.escape(body), footer=footer)


async def db_console_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superuser(update.message.from_user.id):
        await update.message.reply_text(t.ADMIN_ONLY_SQL)
        return
    # Take everything after the command verbatim (preserves newlines/whitespace),
    # rather than context.args which collapses whitespace.
    parts = update.message.text.split(None, 1)
    sql = parts[1].strip() if len(parts) > 1 else ""
    if not sql:
        await update.message.reply_text(t.DB_USAGE, parse_mode=ParseMode.HTML)
        return
    logger.info("command", command="db", user_id=update.message.from_user.id, sql=sql)
    try:
        columns, rows, status = await db.run_sql(sql)
    except Exception as e:
        await update.message.reply_text(t.DB_ERROR.format(error=html.escape(str(e))), parse_mode=ParseMode.HTML)
        return
    # A statement that wasn't a plain SELECT may have changed the achievements
    # table; refresh the in-memory cache so the bot stays consistent.
    if not (status or "").upper().startswith("SELECT"):
        await db.load_cache()
    await update.message.reply_text(
        _format_sql_result(columns, rows, status), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
