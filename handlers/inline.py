"""Inline mode (@wwstatsbot ... from any chat).

An empty query returns the querying user's four stat cards; typed text becomes an
achievement search identical to /info. Answers are is_personal because the cards are built
from the querying user's own stats — a shared cache would leak them between users.
"""

import asyncio
import html

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import builders


def _article(result_id, title, html_text, description=None):
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            html_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        ),
    )


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    user = update.inline_query.from_user
    name = html.escape(user.first_name)

    if not query:
        # Empty query: 4 stat cards for the querying user, fetched in parallel.
        stats_msg, kills_msg, killedby_msg, deaths_msg = await asyncio.gather(
            builders.build_stats_msg(user.id, name),
            builders.build_kills_msg(user.id, name),
            builders.build_killed_by_msg(user.id, name),
            builders.build_deaths_msg(user.id, name),
        )
        results = [
            _article("stats", "My Stats", stats_msg),
            _article("kills", "My Kills", kills_msg),
            _article("killedby", "My Killed By", killedby_msg),
            _article("deaths", "My Deaths", deaths_msg),
        ]
    else:
        # Typed text: achievement search, same behaviour as /info.
        matches = await builders.build_info_results(query)
        if not matches:
            results = [_article("none", "No matching achievements", "No matching achievements found.")]
        else:
            results = [
                _article(m["name"], m["name"], builders.format_single_achv(m), description=m["desc"])
                for m in matches[:50]
            ]

    await update.inline_query.answer(results, cache_time=30, is_personal=True)
