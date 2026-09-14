"""The full list, paged privately: the button on the post and the pager it opens.

The post in the group is one shared message that a table of sixteen is watching and that
re-renders itself every few seconds as roles come in. Paging *it* would mean a page number
belonging to whoever pressed a button last — the failure this codebase already made once,
with the /schall toggle, and fixed by giving the view an owner. A game's post has no
owner, so the pager goes somewhere that does: whoever taps gets their own copy in PM, and
nobody else's view moves.

Three properties carry this file.

**The button appears only when there is more to see.** A pager offering exactly what is
already on the screen is a button that does nothing.

**A page is rendered from the session, not from a copy.** The pager is opened during a
live game and read over the next few minutes, so a page turned after a death shows the
table as it is now — and the session having ended is the one thing a page cannot turn to.

**Prev/Next taps land in a different chat from the game.** PTB hands a handler the
chat_data of the chat the *button* is in, which by then is a conversation with one person
in it, so the game's chat id has to travel in the callback data.
"""

import pytest
from conftest import FakeApplication, FakeCallbackQuery, FakeChat, FakeContext, FakeMessage, FakeUpdate, FakeUser
from test_standin_list import big_game, crowded_game, post_text, publish, visible
from test_standin_session import reveal, start_session

import db
import session
from handlers import gamesession
from rulelist import RULES

GROUP = FakeChat().id


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    """The real catalogue, without a database. db.get_rules() is the live read path."""
    monkeypatch.setattr(db, "get_rules", lambda: {rule["name"]: rule for rule in RULES})


def keyboard_of(session_data):
    return gamesession.render_list(session_data)[1]


def tap_button(user_id=1, name="Ren"):
    """The button in the group, which carries no page: it opens page one."""
    return FakeUpdate(
        callback_query=FakeCallbackQuery(
            data=gamesession.FULL_LIST_CALLBACK,
            from_user=FakeUser(user_id, name),
            message=FakeMessage(chat=FakeChat()),
        )
    )


def tap_page(index, chat_data, user_id=1, name="Ren"):
    """A Prev/Next tap, which happens on the copy sitting in the tapper's PM.

    Built with a *private* chat_data and the group's reachable only through the
    application, because that is the shape of the real update and the reason the chat id
    is in the button at all.
    """
    query = FakeCallbackQuery(
        data="standin:full:{}:{}".format(GROUP, index),
        from_user=FakeUser(user_id, name),
        message=FakeMessage(chat=FakeChat(chat_type="private", chat_id=user_id)),
    )
    context = FakeContext(chat_data={}, application=FakeApplication({GROUP: chat_data}))
    return FakeUpdate(callback_query=query), context, query


# --- The button -------------------------------------------------------------


async def test_a_post_with_nothing_left_out_has_no_button(context):
    """Four players and two reveals fits whole; there is nothing to page through."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")
    await reveal(context, 2, "harlot")

    assert "more</i>" not in post_text(session_data), "this game was supposed to fit"
    assert keyboard_of(session_data) is None


async def test_a_trimmed_post_offers_the_full_list(context):
    session_data = await big_game(context)

    keyboard = keyboard_of(session_data)

    assert keyboard is not None
    button = keyboard.inline_keyboard[0][0]
    assert button.callback_data == gamesession.FULL_LIST_CALLBACK
    assert "full list" in button.text


async def test_the_button_rides_on_the_published_message(context):
    """It is the post's own keyboard, so it has to survive the edit that publishes it."""
    session_data = await big_game(context)
    context.bot.sent.clear()
    context.bot.edits.clear()
    await publish(context)

    assert session_data["list_message_id"] is not None
    edit = context.bot.edits[-1]
    assert edit["reply_markup"] is not None


# --- Opening it -------------------------------------------------------------


async def test_tapping_sends_the_first_page_to_the_tappers_pm(context):
    session_data = await big_game(context)
    context.bot.sent.clear()

    update = tap_button(user_id=4, name="J J")
    await gamesession.full_list_callback(update, context)

    assert len(context.bot.sent) == 1
    sent = context.bot.sent[0]
    assert sent["chat_id"] == 4, "to the tapper, not the group"
    assert "Possible Achievements:" in sent["text"]
    assert "Page 1 of" in sent["text"]
    assert "PM" in update.callback_query.answers[-1]["text"]
    assert session_data is not None


async def test_the_full_list_holds_what_the_post_had_to_leave_out(context):
    session_data = await big_game(context)
    shown = visible(post_text(session_data))
    whole = "".join(visible(page) for page in gamesession.full_list_pages(session_data))

    assert "more</i>" in post_text(session_data), "this game was supposed to trim"
    for _uid, _name, entries in gamesession.list_contents(session_data)[0]:
        for entry in entries:
            assert entry["name"] in whole, entry["name"]
    assert len(whole) > len(shown)


async def test_every_page_fits_in_a_message(context):
    """The whole point is not needing a limit check at the send."""
    session_data = await crowded_game(context, count=60, namelen=128)

    pages = gamesession.full_list_pages(session_data)

    assert len(pages) > 1
    for page in pages:
        assert len(visible(page)) <= 4096, len(visible(page))


async def test_a_page_break_never_splits_a_player(context):
    """A heading on one page and its rows on the next reads as a player with nothing
    available, which is a different and wrong statement."""
    session_data = await crowded_game(context, count=40)
    pages = [visible(page) for page in gamesession.full_list_pages(session_data)]

    for _uid, name, entries in gamesession.list_contents(session_data)[0]:
        # Matched with the newline in front too: a player is also named, comma-separated,
        # in the group sections, and the last name on such a line ends one as well.
        home = [page for page in pages if "\n{}\n".format(name) in page]
        assert len(home) == 1, name
        for entry in entries:
            assert entry["name"] in home[0], (name, entry["name"])


async def test_a_pm_the_bot_cannot_open_is_explained(context):
    """Almost always somebody who has never started the bot privately."""
    session_data = await big_game(context)
    assert session_data is not None
    context.bot._send_error = RuntimeError("Forbidden: bot can't initiate conversation")

    update = tap_button()
    await gamesession.full_list_callback(update, context)

    answer = update.callback_query.answers[-1]
    assert answer["show_alert"] is True
    assert "Start a private chat" in answer["text"]


# --- Paging it --------------------------------------------------------------


async def test_next_edits_the_pm_copy_rather_than_sending_another(context):
    session_data = await crowded_game(context, count=40)
    assert len(gamesession.full_list_pages(session_data)) > 1

    update, pm_context, query = tap_page(1, context.chat_data)
    await gamesession.full_list_callback(update, pm_context)

    assert pm_context.bot.sent == [], "the pager is one message, edited"
    text, _kwargs = query.edits[-1]
    assert "Page 2 of" in text


async def test_paging_wraps_at_both_ends(context):
    """So a button never moves out from under a thumb at the end of the list.

    Asserted on the buttons rather than by tapping a negative page, because the wrap is
    done where the keyboard is built: what a button carries is always a real page number,
    which is also why the handler can treat anything else as page one.
    """
    session_data = await crowded_game(context, count=40)
    total = len(gamesession.full_list_pages(session_data))
    assert total > 1

    first = gamesession._full_list_page_keyboard(GROUP, 0, total).inline_keyboard[0]
    last = gamesession._full_list_page_keyboard(GROUP, total - 1, total).inline_keyboard[0]

    assert first[0].callback_data.endswith(":{}".format(total - 1)), "Prev from page one"
    assert last[1].callback_data.endswith(":0"), "Next from the last page"


async def test_a_page_is_rendered_from_the_session_as_it_is_now(context):
    """Not from a copy taken when the pager opened: a death between taps must show."""
    session_data = await crowded_game(context, count=40)
    _uid, name, _entries = gamesession.list_contents(session_data)[0][0]

    session.set_alive(session_data, 1, False)
    update, pm_context, query = tap_page(0, context.chat_data)
    await gamesession.full_list_callback(update, pm_context)

    pages = "".join(text for text, _ in query.edits)
    assert "{}\n".format(name) not in visible(pages), "the dead player is still listed"


async def test_paging_a_game_that_has_ended_says_so(context):
    """The price of rendering live, and the honest answer — the alternative is a page of
    a game that is over, presented as if it were still running."""
    await crowded_game(context, count=40)
    session.end(context.chat_data)

    update, pm_context, query = tap_page(1, context.chat_data)
    await gamesession.full_list_callback(update, pm_context)

    assert query.edits == []
    assert query.answers[-1]["show_alert"] is True
    assert "ended" in query.answers[-1]["text"]


async def test_a_tap_from_a_chat_the_bot_has_no_session_for_says_so(context):
    """A button that outlived its game, in a chat this process has never seen."""
    update, pm_context, query = tap_page(1, {})
    await gamesession.full_list_callback(update, pm_context)

    assert "ended" in query.answers[-1]["text"]


async def test_a_single_page_gets_no_keyboard(context):
    """There is nothing to page through, and two dead buttons say otherwise."""
    session_data = await start_session(context)
    await reveal(context, 1, "snow_wolf")

    assert len(gamesession.full_list_pages(session_data)) == 1
    assert gamesession._full_list_page_keyboard(GROUP, 0, 1) is None
