"""Small standalone commands: /start, /about, /version."""

import structlog
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import templates as t
import version

logger = structlog.get_logger(__name__)


async def display_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = t.ABOUT.format(repo=version.GITHUB_REPO)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


async def display_version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = version.get_version_info()
    logger.info(
        "command",
        command="version",
        user_id=update.message.from_user.id,
        version=info["version"],
        commit=info["short_commit"],
        branch=info["branch"],
        source=info["source"],
    )
    tmpl = t.VERSION_INFO_LINKED if info["commit_url"] else t.VERSION_INFO_PLAIN
    await update.message.reply_text(tmpl.format(**info), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def startme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == "private":
        await update.message.reply_text(t.START_PRIVATE)
    else:
        return
