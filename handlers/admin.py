"""Privileged commands, in two tiers.

* **Superuser** (an env-var id comparison): /addadmin, /deladmin, /admins, /db.
* **Admin** (superuser or a row in the admins table): /setnote, /clearnote.

db.run_sql executes whatever SQL it is handed, so /db is safe *only* because of its
superuser gate. tests/test_permissions.py asserts the privileged functions are never
reached unauthorised, not merely that a refusal is printed.
"""

import html

import structlog
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import builders
import db
import notes
import templates as t
from handlers.achievements import _achv_from_reply
from handlers.common import is_admin_user, is_superuser

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
