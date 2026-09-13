"""Edited messages reach no handler.

Every handler in this bot begins by reading `update.message`. PTB decides what to dispatch
with `update.effective_message`, which an **edit** populates while leaving `update.message`
as None — so editing a message into a command, or fixing a typo in a forwarded doused list,
dispatched normally and then crashed on the first attribute the handler read. That was live
in production: an `AttributeError: 'NoneType' object has no attribute 'text'` out of
`doused_forward`, caught by the error handler and reported to the log group.

The fix is one gate ahead of every handler group rather than a filter on twenty-eight
registrations. Two things below are what make it safe, and the second is the one that bites:

* it stops what it should — an edited message, and an edited channel post;
* it leaves **callback queries alone**. A callback query has no `update.message` either, and
  its `effective_message` is the message the button sits on, so the tempting
  "update.message is None" reading would silently swallow every button this bot has.
"""

from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop, TypeHandler

import main


def update(**kwargs):
    """An update with only the fields the gate looks at."""
    fields = {"edited_message": None, "edited_channel_post": None, "message": None, "callback_query": None}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


async def gate(upd):
    """Run the gate, reporting whether it stopped the update."""
    try:
        await main._drop_edited_messages(upd, None)
    except ApplicationHandlerStop:
        return True
    return False


# --- What it stops ----------------------------------------------------------


async def test_an_edited_message_is_stopped():
    assert await gate(update(edited_message=SimpleNamespace(text="/dead"))) is True


async def test_an_edited_channel_post_is_stopped():
    assert await gate(update(edited_channel_post=SimpleNamespace(text="/dead"))) is True


# --- What it must not touch -------------------------------------------------


async def test_an_ordinary_message_passes():
    assert await gate(update(message=SimpleNamespace(text="/dead"))) is False


async def test_a_callback_query_passes():
    """The trap. A callback query has no `update.message`, so a gate written as
    "update.message is None" would eat every button in the bot — the /schall toggle, the
    /allinfo pages and the stand-in session's Stop."""
    query = SimpleNamespace(data="standin:stop", message=SimpleNamespace(text="roster"))
    assert await gate(update(callback_query=query)) is False


async def test_an_update_carrying_nothing_passes():
    """An inline query, a poll answer, anything else: not this gate's business."""
    assert await gate(update()) is False


# --- Where it sits ----------------------------------------------------------


def test_the_gate_runs_before_every_other_handler():
    """A gate in the same group as the handlers it protects would protect only whichever
    one PTB happened to try first."""
    app = main.build_application()

    groups = [
        group
        for group, handlers in app.handlers.items()
        for handler in handlers
        if getattr(handler, "callback", None) is main._drop_edited_messages
    ]
    others = [
        group
        for group, handlers in app.handlers.items()
        for handler in handlers
        if getattr(handler, "callback", None) is not main._drop_edited_messages
    ]

    assert len(groups) == 1, "the edited-message gate is not registered"
    assert groups[0] < min(others)


def test_the_gate_sees_every_update_type():
    """A TypeHandler on Update rather than a MessageHandler: a filter narrow enough to
    match only what crashes is a filter that has to be kept in step with the table."""
    app = main.build_application()
    gate_handlers = [
        handler
        for handlers in app.handlers.values()
        for handler in handlers
        if getattr(handler, "callback", None) is main._drop_edited_messages
    ]

    assert isinstance(gate_handlers[0], TypeHandler)


@pytest.mark.parametrize("field", ["edited_message", "edited_channel_post"])
async def test_the_production_crash_shape_never_reaches_a_handler(field):
    """The exact update that crashed doused_forward: a forwarded doused list, edited.

    It matched `filters.FORWARDED & (filters.TEXT | filters.CAPTION)` through
    effective_message, then the handler read update.message.text and there was no
    update.message.
    """
    forwarded = SimpleNamespace(text="You have already doused the house of: A and B", caption=None)

    assert await gate(update(**{field: forwarded})) is True
