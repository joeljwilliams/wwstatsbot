"""The stats command family, and `display_stats`' three-way target resolution.

`/kills`, `/killedby` and `/deaths` are thin wrappers over builders.py — worth covering
only to confirm each is wired to its own builder and honours a reply.

`display_stats` is the interesting one, and the last piece of real untested logic left in
the handlers. It resolves *who* the stats are for in three ways, in priority order:

  1. a reply     -> the replied-to user
  2. a numeric argument -> that raw id, in "by_id" mode
  3. otherwise   -> the sender

"by_id" matters beyond naming: Telegram cannot render a `tg://user?id=` link for an
arbitrary id the bot has never seen, so that path deliberately emits plain text instead of
a mention. Getting the flag wrong produces a message with a dead link in it, which is why
the templates are split rather than conditional.
"""

from conftest import FakeContext, FakeUpdate, FakeUser, message

from handlers import stats


async def run(msg, args=None):
    await stats.display_stats(FakeUpdate(message=msg), FakeContext(args=args or []))
    return msg.last_reply


# --- display_stats target resolution ---------------------------------------------


async def test_no_args_uses_the_sender(stats_api):
    reply = await run(message("/stats", from_user=FakeUser(7, "Alice")))
    assert "<a href='tg://user?id=7'>Alice the Villager</a>" in reply


async def test_a_reply_targets_the_replied_to_user(stats_api):
    replied = message("hi", from_user=FakeUser(99, "Bob"))
    reply = await run(message("/stats", from_user=FakeUser(7, "Alice"), reply_to_message=replied))
    assert "<a href='tg://user?id=99'>Bob the Villager</a>" in reply


async def test_a_numeric_argument_looks_up_that_id_without_a_link(stats_api):
    """Telegram can't render a mention for an id it has never seen, so this path is plain."""
    reply = await run(message("/stats 4242"), args=["4242"])
    assert reply.startswith("4242 the Villager")
    assert "tg://user" not in reply


async def test_the_numeric_argument_is_the_id_actually_queried(stats_api):
    await run(message("/stats 4242"), args=["4242"])
    assert all(r.url.params.get("pid") == "4242" for r in stats_api.requests)


async def test_a_non_numeric_argument_falls_back_to_the_sender(stats_api):
    """Someone typing `/stats Alice` gets their own stats, not an error."""
    reply = await run(message("/stats Alice", from_user=FakeUser(7, "Alice")), args=["Alice"])
    assert "<a href='tg://user?id=7'>Alice the Villager</a>" in reply


async def test_a_reply_wins_over_a_numeric_argument(stats_api):
    """The reply branch is checked first, so the argument is ignored entirely."""
    replied = message("hi", from_user=FakeUser(99, "Bob"))
    reply = await run(
        message("/stats 4242", from_user=FakeUser(7, "Alice"), reply_to_message=replied),
        args=["4242"],
    )
    assert "id=99" in reply
    assert "4242" not in reply


async def test_only_the_first_argument_is_considered(stats_api):
    reply = await run(message("/stats 4242 junk"), args=["4242", "junk"])
    assert reply.startswith("4242 the Villager")


async def test_the_senders_name_is_escaped_exactly_once(stats_api):
    """A display name containing & must not break the HTML or double-escape."""
    reply = await run(message("/stats", from_user=FakeUser(7, "Al & Sons")))
    assert "Al &amp; Sons the Villager" in reply
    assert "&amp;amp;" not in reply


async def test_a_replied_to_users_name_is_escaped(stats_api):
    replied = message("hi", from_user=FakeUser(99, "Bob & Co"))
    reply = await run(message("/stats", reply_to_message=replied))
    assert "Bob &amp; Co" in reply


async def test_a_player_with_no_games(stats_api):
    stats_api.routes["/Stats/PlayerStats/"] = {}
    reply = await run(message("/stats", from_user=FakeUser(7, "Alice")))
    assert reply == "<a href='tg://user?id=7'>Alice</a> has not played any games."


async def test_a_player_with_no_games_by_id_has_no_link(stats_api):
    stats_api.routes["/Stats/PlayerStats/"] = {}
    reply = await run(message("/stats 4242"), args=["4242"])
    assert reply == "4242 has not played any games."


# --- The thin wrappers -----------------------------------------------------------


async def test_kills_uses_the_kills_builder(stats_api):
    msg = message("/kills", from_user=FakeUser(7, "Alice"))
    await stats.display_kills(FakeUpdate(message=msg), FakeContext())
    assert msg.last_reply.startswith("Players <a href='tg://user?id=7'>Alice</a> most killed:")


async def test_killed_by_uses_the_killed_by_builder(stats_api):
    msg = message("/killedby", from_user=FakeUser(7, "Alice"))
    await stats.display_killed_by(FakeUpdate(message=msg), FakeContext())
    assert msg.last_reply.startswith("Players who killed <a href='tg://user?id=7'>Alice</a> most:")


async def test_deaths_uses_the_deaths_builder(stats_api):
    msg = message("/deaths", from_user=FakeUser(7, "Alice"))
    await stats.display_deaths(FakeUpdate(message=msg), FakeContext())
    assert msg.last_reply.startswith("Types of deaths that <a href='tg://user?id=7'>Alice</a> most had:")


async def test_the_wrappers_honour_a_reply(stats_api):
    """resolve_target is shared, so one reply case per wrapper is enough."""
    for handler in (stats.display_kills, stats.display_killed_by, stats.display_deaths):
        replied = message("hi", from_user=FakeUser(99, "Bob"))
        msg = message("/cmd", from_user=FakeUser(7, "Alice"), reply_to_message=replied)
        await handler(FakeUpdate(message=msg), FakeContext())
        assert "id=99" in msg.last_reply, handler.__name__


async def test_every_stats_reply_is_html_without_a_link_preview(stats_api):
    """Every message here contains tg://user links; a preview would attach junk."""
    msg = message("/stats", from_user=FakeUser(7, "Alice"))
    await stats.display_stats(FakeUpdate(message=msg), FakeContext())
    _, kwargs = msg.replies[-1]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["disable_web_page_preview"] is True
