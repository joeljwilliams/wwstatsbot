"""The supporter badge: /setemoji, and the name it follows around.

Three things carry this file.

**It is superuser-only, and the assertion is that the write is never reached** — a refusal
printed after a row was written would still pass a weaker test. Anybody who can hand out
badges can put arbitrary characters on somebody else's name in every group this bot is in.

**A premium emoji is text plus an entity.** The plain emoji sits in the message like any
other character and the entity beside it carries the id of the animated one, so a command
read by its text alone silently stores the fallback and loses what somebody paid for.

**A badge Telegram refuses would not fail at /setemoji — it would fail in every message
naming that player.** Only bots that bought a username on Fragment may send custom emoji, so
the confirmation doubles as the test: what is stored is whichever form Telegram agreed to
send, and the row is not written until it has.
"""

import pytest
from conftest import SUPERUSER_ID, FakeContext, FakeEntity, FakeUpdate, FakeUser, message
from telegram.error import BadRequest

import badges
import db
from handlers import admin, gamesession, search

PREMIUM_ID = "5368324170671202286"


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """The cache is the read path, so tests write to it directly and record the calls."""
    written = []

    async def set_badge(user_id, emoji, custom_emoji_id, name, set_by):
        written.append((user_id, emoji, custom_emoji_id, name, set_by))
        db._BADGES[user_id] = (emoji, custom_emoji_id)

    async def clear_badge(user_id):
        written.append(("cleared", user_id))
        return db._BADGES.pop(user_id, None) is not None

    monkeypatch.setattr(db, "_BADGES", {})
    monkeypatch.setattr(db, "set_badge", set_badge)
    monkeypatch.setattr(db, "clear_badge", clear_badge)
    return written


def command(text, args, from_user=None, entities=(), reply_to=None, reply_errors=()):
    """The command, and the context PTB would build for it.

    `reply_errors` is conftest's own hook: one error per reply_text call, in order — which
    is the shape of a badge Telegram refuses on the first attempt and accepts once the
    custom emoji has been dropped.
    """
    return message(
        text,
        from_user=from_user or FakeUser(SUPERUSER_ID, "Root"),
        entities=list(entities),
        reply_to_message=reply_to,
        reply_errors=list(reply_errors),
    ), FakeContext(args=args)


async def run(text, args, **kwargs):
    msg, ctx = command(text, args, **kwargs)
    await admin.set_emoji_cmd(FakeUpdate(message=msg), ctx)
    return msg


# --- Setting one -------------------------------------------------------------


async def test_the_superuser_sets_a_plain_emoji(no_database):
    msg = await run("/setemoji 7 \N{SPARKLES}", ["7", "\N{SPARKLES}"])

    assert no_database == [(7, "\N{SPARKLES}", None, None, SUPERUSER_ID)]
    assert "\N{SPARKLES}" in msg.last_reply


async def test_a_reply_names_the_player_instead(no_database):
    theirs = message("hi", from_user=FakeUser(99, "Zoe"))
    msg = await run("/setemoji \N{SPARKLES}", ["\N{SPARKLES}"], reply_to=theirs)

    assert no_database == [(99, "\N{SPARKLES}", None, "Zoe", SUPERUSER_ID)]
    assert "Zoe" in msg.last_reply


async def test_naming_nobody_explains_itself(no_database):
    msg = await run("/setemoji", [])

    assert "Usage" in msg.last_reply
    assert no_database == []


async def test_a_badge_longer_than_a_badge_is_refused(no_database):
    long_one = "\N{SPARKLES}" * 25
    msg = await run("/setemoji 7 " + long_one, ["7", long_one])

    assert "one emoji" in msg.last_reply
    assert no_database == []


# --- Premium emoji, and the bots that may not send them ----------------------


def premium(text):
    """The entity Telegram attaches beside a premium emoji, offset in UTF-16 units."""
    head = text[: text.index("\N{SPARKLES}")]
    return FakeEntity(
        "custom_emoji",
        offset=len(head.encode("utf-16-le")) // 2,
        length=len("\N{SPARKLES}".encode("utf-16-le")) // 2,
        custom_emoji_id=PREMIUM_ID,
    )


async def test_a_premium_emoji_keeps_its_sticker_id(no_database):
    text = "/setemoji 7 \N{SPARKLES}"
    msg = await run(text, ["7", "\N{SPARKLES}"], entities=[premium(text)])

    assert no_database == [(7, "\N{SPARKLES}", PREMIUM_ID, None, SUPERUSER_ID)]
    assert 'emoji-id="{}"'.format(PREMIUM_ID) in msg.last_reply


async def test_a_premium_emoji_telegram_refuses_is_downgraded(no_database):
    """Stored unusable it would break every message naming that player, not this one."""
    text = "/setemoji 7 \N{SPARKLES}"
    msg, ctx = command(
        text,
        ["7", "\N{SPARKLES}"],
        entities=[premium(text)],
        reply_errors=[BadRequest("Bad Request: unsupported custom emoji")],
    )

    await admin.set_emoji_cmd(FakeUpdate(message=msg), ctx)

    assert no_database == [(7, "\N{SPARKLES}", None, None, SUPERUSER_ID)], "the plain one is kept"
    assert "may not send premium emoji" in msg.last_reply


async def test_an_ordinary_refusal_is_not_swallowed(no_database):
    """Only the custom emoji is worth retrying without. Anything else is a real failure and
    belongs to the error handler — a badge stored after a failed send would be invisible."""
    msg, ctx = command(
        "/setemoji 7 \N{SPARKLES}", ["7", "\N{SPARKLES}"], reply_errors=[BadRequest("Bad Request: chat not found")]
    )

    with pytest.raises(BadRequest):
        await admin.set_emoji_cmd(FakeUpdate(message=msg), ctx)

    assert no_database == []


# --- Taking one away ---------------------------------------------------------


async def test_no_emoji_takes_the_badge_away(no_database):
    db._BADGES[7] = ("\N{SPARKLES}", None)

    msg = await run("/setemoji 7", ["7"])

    assert no_database == [("cleared", 7)]
    assert "no badge any more" in msg.last_reply


async def test_clearing_a_badge_nobody_had_says_so(no_database):
    msg = await run("/setemoji 7", ["7"])

    assert "no badge to take away" in msg.last_reply


# --- What it renders as ------------------------------------------------------


def test_a_name_with_no_badge_is_returned_untouched():
    """Which is every name in the bot, for everybody who is not a contributor — so the
    decorated call has to be byte-identical to the bare one it replaced."""
    assert badges.decorate(7, "Ren") == "Ren"


def test_a_plain_badge_hangs_off_the_end_of_the_name(no_database):
    db._BADGES[7] = ("\N{SPARKLES}", None)
    assert badges.decorate(7, "Ren") == "Ren \N{SPARKLES}"


def test_a_premium_badge_carries_the_fallback_inside_the_tag(no_database):
    db._BADGES[7] = ("\N{SPARKLES}", PREMIUM_ID)
    assert badges.decorate(7, "Ren") == 'Ren <tg-emoji emoji-id="{}">\N{SPARKLES}</tg-emoji>'.format(PREMIUM_ID)


def test_an_emoji_that_is_markup_is_escaped(no_database):
    """It arrives from a database this module does not own and goes straight into HTML."""
    db._BADGES[7] = ("<b>", None)
    assert badges.decorate(7, "Ren") == "Ren &lt;b&gt;"


# --- It follows the name around ----------------------------------------------
#
# There is no single place a name becomes a mention — each of the nine call sites
# interpolates it into a different template — so these go through two real renderers rather
# than the helper, and badges.py lists the rest.


def test_the_standin_puts_it_on_every_name_it_mentions(no_database):
    """One helper behind the roster, the achievements list, /dead, /love and the lynch
    order, so this is the widest call site in the bot."""
    db._BADGES[1] = ("\N{SPARKLES}", None)

    assert gamesession._mention(1, "Ren") == "<a href='tg://user?id=1'>Ren \N{SPARKLES}</a>"
    assert gamesession._mention(2, "omu") == "<a href='tg://user?id=2'>omu</a>"


def test_a_schall_row_carries_it(no_database):
    """A real rendered message, whole-string: the badge lands inside the mention, after the
    name, and nobody else's row changes."""
    db._BADGES[3] = ("\N{SPARKLES}", None)
    payload = {"name": "X", "desc": "d", "missing": [(1, "Alice")], "have": [(3, "Carol")], "unresolved": []}

    msg, _ = search._render_schall(payload, "TOK", show_have=True)

    assert msg == (
        "Achievement: <b>X</b>\n<i>d</i>\n\n"
        "Checked 2 players for it:\n\n"
        "\N{WHITE HEAVY CHECK MARK} <b>Obtained (1)</b>\n"
        "<a href='tg://user?id=3'>Carol \N{SPARKLES}</a>\n"
    )
