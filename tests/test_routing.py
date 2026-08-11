"""Commands that reroute themselves based on what they reply to.

Two commands are overloaded, and the overload is invisible from the handler table:

* `/sch` (and `/search`) replying to a **bot** message that mentions players means
  "check all of them" rather than "check this message's author" — the author is the game
  bot, whose own stats are empty, so the single-player reading is never what was meant.
* a bare `/info` replying to a bot means "info for everything that message lists".

`/schall` and `/allinfo` still work when typed but are deliberately unadvertised. A
refactor that splits these handlers into different modules could easily drop a reroute
and leave a command that merely looks correct, so the dispatch is pinned here.

Also covered: expired callback tokens. Because the bot_data stores are bounded, an
expired token is ordinary traffic, not an edge case.
"""

from conftest import (
    FakeCallbackQuery,
    FakeContext,
    FakeEntity,
    FakeUpdate,
    FakeUser,
    bot_message,
    message,
)

import main
import templates as t


def player_mention(user_id=1, name="Alice", offset=0, length=5):
    return FakeEntity("text_mention", offset=offset, length=length, user=FakeUser(user_id, name))


# --- /sch -> multi-player path ---------------------------------------------------


async def test_search_reroutes_to_search_all_on_a_bot_player_reply(monkeypatch, achievements, no_fts, stats_api):
    called = {}

    async def spy(update, context):
        called["yes"] = True

    monkeypatch.setattr(main, "display_search_all", spy)

    replied = bot_message("Alice", entities=[player_mention()])
    update = FakeUpdate(message=message("/sch busy", reply_to_message=replied))
    await main.display_search(update, FakeContext(args=["busy"]))

    assert called.get("yes"), "expected /sch to hand off to display_search_all"


async def test_search_stays_single_player_when_replying_to_a_human(achievements, no_fts, stats_api):
    """A human reply means "check this person", the original behaviour."""
    replied = message("hi", from_user=FakeUser(5, "Bob"))
    msg = message("/sch busy", reply_to_message=replied)
    await main.display_search(FakeUpdate(message=msg), FakeContext(args=["busy"]))

    assert "for <a href='tg://user?id=5'>Bob</a>" in msg.last_reply


async def test_search_without_args_explains_the_syntax(achievements, stats_api):
    msg = message("/sch")
    await main.display_search(FakeUpdate(message=msg), FakeContext(args=[]))
    assert "Invalid parameter!" in msg.last_reply


async def test_search_rejects_queries_under_three_characters(achievements, stats_api):
    msg = message("/sch ab")
    await main.display_search(FakeUpdate(message=msg), FakeContext(args=["ab"]))
    assert "at least 3 letters" in msg.last_reply


async def test_search_marks_attained_and_unattained(achievements, no_fts, stats_api):
    """✅ for held achievements, ☑️ for not — mirroring the /schall marks."""
    msg = message("/sch night")
    await main.display_search(FakeUpdate(message=msg), FakeContext(args=["night"]))
    reply = msg.last_reply
    assert t.SEARCH_ATTAINED in reply  # Busy Night is in ACHIEVEMENTS_JSON
    assert "Busy Night" in reply


async def test_search_hides_inactive_achievements_the_user_lacks(achievements, no_fts, stats_api):
    """They can no longer be earned, so listing them as "not yet" would mislead."""
    msg = message("/sch Explorer")
    await main.display_search(FakeUpdate(message=msg), FakeContext(args=["Explorer"]))
    assert "No matching achievements found" in msg.last_reply


async def test_search_keeps_an_inactive_achievement_the_user_already_holds(achievements, no_fts, stats_api):
    """So a completed collection still shows everything in it."""
    stats_api.set_achievements(1, ["Explorer"])
    msg = message("/sch Explorer", from_user=FakeUser(1, "Alice"))
    await main.display_search(FakeUpdate(message=msg), FakeContext(args=["Explorer"]))
    assert "Explorer" in msg.last_reply


# --- /info -> all-cards path ------------------------------------------------------


async def test_bare_info_replying_to_a_bot_reroutes_to_all_info(monkeypatch):
    called = {}

    async def spy(update, context):
        called["yes"] = True

    monkeypatch.setattr(main, "all_info_cmd", spy)

    update = FakeUpdate(message=message("/info", reply_to_message=bot_message("- Busy Night")))
    await main.display_achv_info(update, FakeContext(args=[]))

    assert called.get("yes"), "expected bare /info on a bot reply to hand off"


async def test_info_with_args_stays_a_single_lookup_even_when_replying_to_a_bot(monkeypatch, achievements, no_fts):
    """Explicit arguments mean the user named what they want."""

    async def fail(update, context):
        raise AssertionError("should not have rerouted")

    monkeypatch.setattr(main, "all_info_cmd", fail)

    msg = message("/info busy", reply_to_message=bot_message("- Something"))
    await main.display_achv_info(FakeUpdate(message=msg), FakeContext(args=["busy"]))
    assert "<b>Busy Night</b>" in msg.last_reply


async def test_info_replying_to_a_human_uses_the_replied_text_as_the_query(achievements, no_fts):
    replied = message("Busy Night", from_user=FakeUser(5, "Bob"))
    msg = message("/info", reply_to_message=replied)
    await main.display_achv_info(FakeUpdate(message=msg), FakeContext(args=[]))
    assert "<b>Busy Night</b>" in msg.last_reply


async def test_info_returns_the_top_ranked_match_only(achievements, no_fts):
    msg = message("/info business")
    await main.display_achv_info(FakeUpdate(message=msg), FakeContext(args=["business"]))
    assert msg.last_reply.startswith("<b>Liquid Business</b>")


# --- /allinfo in a group vs a PM --------------------------------------------------


async def test_allinfo_in_a_group_posts_a_prompt_with_a_pm_button(achievements, no_fts):
    """One public message with a button, so several people can each pull their own copy."""
    replied = bot_message("Possible Achievements:\n\nRen\n - Busy Night")
    msg = message("/info", reply_to_message=replied)
    ctx = FakeContext()
    await main.all_info_cmd(FakeUpdate(message=msg), ctx)

    text, kwargs = msg.replies[-1]
    assert "Tap the button" in text
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data.startswith("allinfo:pm:")
    assert ctx.bot_data["allinfo"]


async def test_allinfo_in_a_pm_shows_the_pager_directly(achievements, no_fts):
    """Offering to PM someone already in their PM would just add a hop."""
    from conftest import FakeChat

    replied = bot_message("Possible Achievements:\n\nRen\n - Busy Night\n - Liquid Business")
    msg = message("/info", chat=FakeChat("private", 1), reply_to_message=replied)
    await main.all_info_cmd(FakeUpdate(message=msg), FakeContext())

    text, kwargs = msg.replies[-1]
    assert "<b>Busy Night</b>" in text
    assert "<i>1/2</i>" in text
    assert kwargs["reply_markup"] is not None


async def test_allinfo_needs_a_reply():
    msg = message("/info")
    await main.all_info_cmd(FakeUpdate(message=msg), FakeContext())
    assert msg.last_reply == t.ALLINFO_NEED_REPLY


async def test_allinfo_reports_a_message_with_no_achievement_rows():
    msg = message("/info", reply_to_message=bot_message("just prose, no bullets"))
    await main.all_info_cmd(FakeUpdate(message=msg), FakeContext())
    assert msg.last_reply == t.ALLINFO_NO_ACHIEVEMENTS


async def test_allinfo_reports_unmatched_names_alongside_the_prompt(achievements, no_fts):
    replied = bot_message("Ren\n - Busy Night\n - Totally Fake Name")
    msg = message("/info", reply_to_message=replied)
    await main.all_info_cmd(FakeUpdate(message=msg), FakeContext())
    assert "Could not match: Totally Fake Name" in msg.last_reply


# --- Callback tokens: expiry is normal traffic ------------------------------------


async def test_allinfo_callback_reports_an_expired_token():
    query = FakeCallbackQuery(data="allinfo:pm:gone-token")
    await main.all_info_callback(FakeUpdate(callback_query=query), FakeContext())
    assert query.answers == [{"text": t.ALLINFO_EXPIRED, "show_alert": True}]


async def test_schall_callback_reports_an_expired_token():
    query = FakeCallbackQuery(data="schall:gone-token:have")
    await main.schall_callback(FakeUpdate(callback_query=query), FakeContext())
    assert query.answers == [{"text": t.SCHALL_EXPIRED, "show_alert": True}]


async def test_schall_callback_toggles_the_view():
    payload = {"name": "X", "desc": "d", "missing": [(1, "Alice")], "have": [(2, "Bob")], "unresolved": []}
    ctx = FakeContext(bot_data={"schall": {"TOK": payload}})
    query = FakeCallbackQuery(data="schall:TOK:have")
    await main.schall_callback(FakeUpdate(callback_query=query), ctx)

    text, _ = query.edits[-1]
    assert "Obtained (1)" in text and "Bob" in text
    assert query.answers == [{"text": None, "show_alert": False}]


async def test_schall_callback_swallows_a_not_modified_race():
    """Two people tapping the same button: the message already shows this view."""
    from telegram.error import BadRequest

    payload = {"name": "X", "desc": "d", "missing": [], "have": [], "unresolved": []}
    ctx = FakeContext(bot_data={"schall": {"TOK": payload}})
    query = FakeCallbackQuery(data="schall:TOK:have")
    query.edit_error = BadRequest("Message is not modified")

    await main.schall_callback(FakeUpdate(callback_query=query), ctx)  # must not raise
    assert query.answers == [{"text": None, "show_alert": False}]


async def test_allinfo_callback_pages(achievements, no_fts):
    ctx = FakeContext(bot_data={"allinfo": {"TOK": ["Busy Night", "Liquid Business"]}})
    query = FakeCallbackQuery(data="allinfo:p:TOK:1")
    await main.all_info_callback(FakeUpdate(callback_query=query), ctx)

    text, _ = query.edits[-1]
    assert "<b>Liquid Business</b>" in text
    assert "<i>2/2</i>" in text


async def test_allinfo_callback_treats_a_legacy_bare_token_as_the_pm_handoff(achievements, no_fts):
    """Buttons posted before the pager existed carried just a token, and their
    messages may still be sitting in a group."""
    ctx = FakeContext(bot_data={"allinfo": {"TOK": ["Busy Night"]}})
    query = FakeCallbackQuery(data="allinfo:TOK")
    await main.all_info_callback(FakeUpdate(callback_query=query), ctx)

    assert ctx.bot.sent, "expected the cards to be delivered to the tapper's PM"
    assert "<b>Busy Night</b>" in ctx.bot.sent[0]["text"]


async def test_allinfo_callback_sends_every_card_on_send_all(achievements, no_fts):
    ctx = FakeContext(bot_data={"allinfo": {"TOK": ["Busy Night", "Liquid Business"]}})
    query = FakeCallbackQuery(data="allinfo:all:TOK")
    await main.all_info_callback(FakeUpdate(callback_query=query), ctx)

    assert len(ctx.bot.sent) == 2
    assert query.answers[-1]["text"] == t.ALLINFO_SENT_ALL.format(count=2, plural="s")


async def test_allinfo_callback_explains_when_it_cannot_pm_the_user(achievements, no_fts):
    """Almost always means the user has never started the bot in a private chat."""
    from conftest import FakeBot

    ctx = FakeContext(
        bot=FakeBot(send_error=RuntimeError("Forbidden: bot can't initiate")),
        bot_data={"allinfo": {"TOK": ["Busy Night"]}},
    )
    query = FakeCallbackQuery(data="allinfo:pm:TOK")
    await main.all_info_callback(FakeUpdate(callback_query=query), ctx)

    assert query.answers == [{"text": t.ALLINFO_NO_PM, "show_alert": True}]


# --- /schall proper --------------------------------------------------------------


async def test_schall_buckets_mentioned_players(achievements, no_fts, stats_api):
    stats_api.set_achievements(1, ["Busy Night"])  # Alice has it
    stats_api.set_achievements(2, [])  # Bob does not

    replied = bot_message(
        "Alice Bob",
        entities=[
            player_mention(1, "Alice", 0, 5),
            player_mention(2, "Bob", 6, 3),
        ],
    )
    msg = message("/sch busy", reply_to_message=replied)
    ctx = FakeContext(args=["busy"])
    await main.display_search_all(FakeUpdate(message=msg), ctx)

    text = msg.last_reply
    assert "Not obtained (1)" in text and "Bob" in text
    assert "Show who has it (1)" in msg.replies[-1][1]["reply_markup"].inline_keyboard[0][0].text
    assert ctx.bot_data["schall"], "the payload must be stored for the toggle"


async def test_schall_reports_players_whose_lookup_failed(achievements, no_fts, stats_api):
    """One failed API call must not sink the whole command."""
    stats_api.set_achievements(1, [])
    stats_api.fail_pids.add("2")

    replied = bot_message(
        "Alice Bob",
        entities=[
            player_mention(1, "Alice", 0, 5),
            player_mention(2, "Bob", 6, 3),
        ],
    )
    msg = message("/sch busy", reply_to_message=replied)
    await main.display_search_all(FakeUpdate(message=msg), FakeContext(args=["busy"]))

    text = msg.last_reply
    assert "Couldn't check: Bob" in text
    assert "Not obtained (1)" in text  # Alice still got answered


async def test_schall_needs_a_reply():
    msg = message("/sch busy")
    await main.display_search_all(FakeUpdate(message=msg), FakeContext(args=["busy"]))
    assert msg.last_reply == t.SCHALL_NEED_REPLY


async def test_schall_without_a_query_shows_usage():
    msg = message("/sch", reply_to_message=bot_message("x"))
    await main.display_search_all(FakeUpdate(message=msg), FakeContext(args=[]))
    assert msg.last_reply == t.SCHALL_USAGE


async def test_schall_explains_when_only_usernames_were_mentioned(achievements, no_fts):
    replied = bot_message("@dave", entities=[FakeEntity("mention", 0, 5)])
    msg = message("/sch busy", reply_to_message=replied)
    await main.display_search_all(FakeUpdate(message=msg), FakeContext(args=["busy"]))
    assert "can't check plain @username mentions" in msg.last_reply
