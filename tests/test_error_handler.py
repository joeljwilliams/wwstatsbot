"""The global error handler.

Every unhandled exception in every handler arrives here, so its two decisions matter
more than their few lines suggest:

* a **swallow-list** of errors that are normal operational noise for a polling bot
  (long-poll timeouts, "not modified" from two people tapping one button, an expired
  inline query id) — logging these would bury real failures;
* **reporting to LOG_GROUP_ID**, which is how anything at all becomes visible in
  production. If that send throws, the error handler must not itself raise: it is the
  last line of defence and an exception here is unhandleable.

Matching is on the lowercased `str(error)` — substring, not exception type — which is
worth pinning because it is easy to "tidy" into an isinstance check that behaves
differently.
"""

import pytest
from conftest import FakeBot, FakeContext, FakeUpdate

import main

LOG_GROUP = -1001234567890


@pytest.fixture
def log_group(monkeypatch):
    """Configure a log group, as production does."""
    monkeypatch.setattr(main, "LOG_GROUP_ID", LOG_GROUP)


@pytest.fixture
def no_log_group(monkeypatch):
    monkeypatch.setattr(main, "LOG_GROUP_ID", None)


async def handle(error, context=None):
    context = context or FakeContext()
    context.error = error
    await main.error_handler(FakeUpdate(), context)
    return context


# --- The swallow-list ------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # A long-poll that returned nothing: routine for a polling bot.
        "Timed out",
        "timed out",
        "httpx.ReadTimeout: The read operation timed out",
        # Two users tapped the same toggle, so the message already shows that view.
        "Message is not modified",
        "not modified",
        # The user dismissed the inline menu before the answer landed.
        "Query_id_invalid",
        "query_id_invalid",
    ],
)
async def test_operational_noise_is_not_reported(log_group, message):
    context = await handle(Exception(message))
    assert context.bot.sent == [], "{!r} should have been swallowed".format(message)


async def test_matching_is_case_insensitive(log_group):
    """str(error).lower() is compared, so the wording's casing must not matter."""
    context = await handle(Exception("TIMED OUT"))
    assert context.bot.sent == []


async def test_a_real_error_that_merely_mentions_a_swallowed_phrase_is_still_swallowed(log_group):
    """Documents the known trade-off: matching is substring-based, so an unrelated error
    whose text contains "timed out" is dropped too. Pinned so the behaviour is a choice
    rather than a surprise — tighten it here if that ever bites."""
    context = await handle(Exception("database connection timed out during /db"))
    assert context.bot.sent == []


# --- Reporting -------------------------------------------------------------------


async def test_an_unexpected_error_is_reported_to_the_log_group(log_group):
    context = await handle(RuntimeError("something genuinely broke"))
    assert len(context.bot.sent) == 1
    report = context.bot.sent[0]
    assert report["chat_id"] == LOG_GROUP
    assert report["text"] == "something genuinely broke"


async def test_nothing_is_reported_when_no_log_group_is_configured(no_log_group):
    """LOG_GROUP_ID is optional; without it the error is logged and that is all."""
    context = await handle(RuntimeError("boom"))
    assert context.bot.sent == []


async def test_a_failure_to_report_is_swallowed(log_group):
    """The error handler is the last line of defence — raising here is unhandleable
    (and, if the log group itself is misconfigured, would fire on every single error)."""
    context = FakeContext(bot=FakeBot(send_error=RuntimeError("bot was kicked from the log group")))
    await handle(RuntimeError("original failure"), context=context)  # must not raise


async def test_the_report_survives_an_error_message_containing_markup(log_group):
    """Reports are sent with parse_mode=MARKDOWN, so an error whose text contains stray
    markup can make Telegram reject the send. That rejection must stay swallowed."""
    context = FakeContext(bot=FakeBot(send_error=RuntimeError("Can't parse entities")))
    await handle(RuntimeError("unbalanced *asterisk in _message"), context=context)


async def test_a_non_exception_error_value_is_handled(log_group):
    """context.error is typed as object; PTB can hand over something unusual."""
    context = await handle("a bare string error")
    assert context.bot.sent[0]["text"] == "a bare string error"


async def test_none_error_does_not_crash(log_group):
    """Defensive: str(None) is "none", which is on no swallow-list, so it reports."""
    context = await handle(None)
    assert len(context.bot.sent) == 1
