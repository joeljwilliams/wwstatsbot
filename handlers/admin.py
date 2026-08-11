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
import settings
from handlers.achievements import _achv_from_reply

logger = structlog.get_logger(__name__)


def is_superuser(user_id):
    return settings.SUPERUSER_ID is not None and user_id == settings.SUPERUSER_ID


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
        await update.message.reply_text("Usage: reply to a user with /addadmin, or /addadmin <user_id>.")
        return
    user_id, username, first_name = target
    await db.add_admin(user_id, username, first_name, update.message.from_user.id)
    label = html.escape(first_name) if first_name else str(user_id)
    await update.message.reply_text(
        "Added <a href='tg://user?id={}'>{}</a> as an admin.".format(user_id, label),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def del_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superuser(update.message.from_user.id):
        await update.message.reply_text("Only the superuser can remove admins.")
        return
    target = _resolve_admin_target(update, context)
    if target is None:
        await update.message.reply_text("Usage: reply to a user with /deladmin, or /deladmin <user_id>.")
        return
    removed = await db.remove_admin(target[0])
    await update.message.reply_text("Removed admin {}.".format(target[0]) if removed else "That user is not an admin.")


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
        name = html.escape(r["first_name"]) if r["first_name"] else "(unknown)"
        uname = " @{}".format(html.escape(r["username"])) if r["username"] else ""
        lines.append("<code>{}</code> {}{}".format(r["user_id"], name, uname))
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def set_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_user(update.message.from_user.id):
        await update.message.reply_text("Only admins can edit notes.")
        return
    replied = update.message.reply_to_message
    if replied is None or not replied.text:
        await update.message.reply_text(
            "Reply to an achievement /info card with <code>/setnote &lt;note&gt;</code> "
            "or <code>/setnote prob &lt;probability&gt;</code>.",
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
            "Please provide the text: <code>/setnote &lt;note&gt;</code> or "
            "<code>/setnote prob &lt;probability&gt;</code>. "
            "Use /clearnote to remove a field.",
            parse_mode=ParseMode.HTML,
        )
        return
    match = _achv_from_reply(replied)
    if match is None:
        await update.message.reply_text(
            "Could not identify the achievement from that message. Reply to a single /info card."
        )
        return
    # Merge into the existing fields so the other field is preserved.
    fields = notes.parse_notes(match.get("notes", ""))
    fields[field] = text
    await db.update_notes(match["name"], notes.serialize_notes(fields))
    updated = next((a for a in db.get_achievements() if a["name"] == match["name"]), match)
    await update.message.reply_text(
        "Note updated.\n\n" + builders.format_single_achv(updated),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def clear_note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_user(update.message.from_user.id):
        await update.message.reply_text("Only admins can edit notes.")
        return
    replied = update.message.reply_to_message
    if replied is None or not replied.text:
        await update.message.reply_text(
            "Reply to an achievement /info card with <code>/clearnote</code> "
            "(memo), <code>/clearnote prob</code>, or <code>/clearnote all</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    match = _achv_from_reply(replied)
    if match is None:
        await update.message.reply_text(
            "Could not identify the achievement from that message. Reply to a single /info card."
        )
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
        "Note updated.\n\n" + builders.format_single_achv(updated),
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
            "Usage: <code>/db &lt;sql&gt;</code>\nRuns a single SQL statement.", parse_mode=ParseMode.HTML
        )
        return
    logger.info("command", command="db", user_id=update.message.from_user.id, sql=sql)
    try:
        columns, rows, status = await db.run_sql(sql)
    except Exception as e:
        await update.message.reply_text(
            "<b>SQL error:</b>\n<pre>{}</pre>".format(html.escape(str(e))), parse_mode=ParseMode.HTML
        )
        return
    # A statement that wasn't a plain SELECT may have changed the achievements
    # table; refresh the in-memory cache so the bot stays consistent.
    if not (status or "").upper().startswith("SELECT"):
        await db.load_cache()
    await update.message.reply_text(
        _format_sql_result(columns, rows, status), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
