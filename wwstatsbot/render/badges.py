"""The supporter badge: one emoji a contributor's name carries wherever this bot prints it.

Set by the superuser with `/setemoji` and stored against a **user id** (`db.player_badges`),
because it is a fact about the person rather than about a game or a chat — the same badge
follows them into every roster, stats card, search result and achievement announcement.

Rendering lives here rather than in `db.py` because there are two shapes of it and the
difference is not a storage question. An ordinary emoji is a character. A **premium** one is
an animated sticker addressed by id, sent as `<tg-emoji emoji-id="…">X</tg-emoji>` where `X`
is the plain emoji a client falls back to when it cannot show the sticker — so what goes in
the message is markup, assembled from two stored columns.

Like everything else this bot sends, the HTML is built by concatenation with manual
escaping: `decorate()` takes an **already-escaped** name and returns an escaped string, so a
caller that was correct before stays correct with a badge in it.

Attached wherever this bot renders a person's name as a link, which is nine places and no
single choke point — each one fills a `{badge}` field of its own, and every one of those
sits **outside** the `<a>`:

* `handlers/gamesession.py::_mention` — the whole stand-in manager goes through this one:
  the roster, the Possible Achievements list, /dead, /love, the lynch order;
* `builders.py` — the /stats card and the /kills, /killedby and /deaths headers;
* `handlers/search.py` — the /search header and every /schall row;
* `handlers/achievements.py` — the player /roll names;
* `handlers/welcome.py` — the join announcement;
* `playerdata.py` — the new-achievement announcement in the log group.

A name rendered without it is not a bug that shows: it is one badge missing from one
message, which is why the list is here rather than left to be rediscovered.
"""

import html
import re

from wwstatsbot.data import db
from wwstatsbot.render import templates as t

# <tg-emoji emoji-id="...">fallback</tg-emoji> -> fallback. The tag always carries an
# ordinary glyph for clients that cannot render the custom one, so unwrapping it is a
# complete fallback rather than a degraded one — which is what makes it a safe retry for a
# send Telegram refused (see handlers/welcome.py::_post, the other caller).
_CUSTOM_EMOJI = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.DOTALL)


def strip_custom(text):
    """`text` with every custom emoji replaced by the plain glyph inside it."""
    return _CUSTOM_EMOJI.sub(r"\1", text)


def markup(emoji, custom_emoji_id=None):
    """The badge itself, ready to concatenate — with its leading space.

    The space belongs here rather than in the template because every caller wants it and
    exactly one of them would eventually forget it.
    """
    escaped = html.escape(emoji or "")
    if not escaped:
        return ""
    if custom_emoji_id:
        return t.BADGE_CUSTOM.format(emoji=escaped, custom_id=html.escape(str(custom_emoji_id)))
    return t.BADGE.format(emoji=escaped)


def of(user_id):
    """This player's badge as markup, or "" — the whole read path, and it touches no I/O.

    `db.badge()` is a dict lookup against a cache loaded at startup, which matters because
    this is called once per name per render: a sixteen-player roster edits twice a phase and
    asks sixteen times each.

    What comes back goes in a template's `{badge}` field, which every mention template puts
    **outside** its `<a>` tag — see the note above those templates. An empty string renders
    the message byte-identically to the one before badges existed, which is every message
    about everybody who has not been given one.
    """
    stored = db.badge(user_id)
    return markup(*stored) if stored else ""
