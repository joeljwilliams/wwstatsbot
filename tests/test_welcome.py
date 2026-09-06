"""Join announcements: the /welcome switch, and what a join actually posts.

Three things shape this feature, and each is tested below:

* **Off until a group asks.** The handler sees every join in every group the bot is in, so
  a chat that has not opted in must produce no message *and* no API call — the second is
  what stops the bot doing a round of stats lookups every time anyone joins anywhere.
* **The group's own admins decide.** It is a setting about their room, not about the bot,
  so the bot-wide admin table is not the authority — Telegram is.
* **A mass add is bounded, and says so.** A group merge can add dozens of people at once;
  the message caps the lines and never lets the cap pass for "this is everyone".
"""

import pytest
from conftest import FakeBot, FakeChat, FakeContext, FakeUpdate, FakeUser, message

import db
from handlers import welcome


@pytest.fixture(autouse=True)
def no_bot_admins(monkeypatch):
    """The bot-wide admin table is a database away, and every permission path here
    consults it. Answering "no" by default keeps each test about the tier it is testing."""

    async def not_an_admin(user_id):
        return False

    monkeypatch.setattr(db, "is_admin", not_an_admin)


ADMIN = 5
MEMBER = 6


def group(chat_type="supergroup"):
    return FakeChat(chat_type=chat_type)


def welcome_ctx(enabled=None, chat_admins=(ADMIN,), args=()):
    chat_data = {} if enabled is None else {welcome._WELCOME_KEY: enabled}
    return FakeContext(args=list(args), bot=FakeBot(chat_admins=chat_admins), chat_data=chat_data)


async def toggle(args, user_id=ADMIN, ctx=None, chat=None):
    ctx = ctx or welcome_ctx(args=args)
    ctx.args = list(args)
    msg = message("/welcome " + " ".join(args), from_user=FakeUser(user_id, "Admin"), chat=chat or group())
    await welcome.welcome_cmd(FakeUpdate(message=msg), ctx)
    return msg, ctx


async def joins(ctx, users=((7, "Alice"),)):
    """A join service message, as Telegram delivers one."""
    msg = message("", chat=group(), new_chat_members=[FakeUser(uid, name) for uid, name in users])
    await welcome.greet_new_members(FakeUpdate(message=msg), ctx)
    return msg


# --- The switch --------------------------------------------------------------------


async def test_it_is_off_until_a_group_turns_it_on():
    assert welcome.is_enabled(FakeContext()) is False


async def test_an_admin_can_turn_it_on():
    msg, ctx = await toggle(["on"])
    assert ctx.chat_data[welcome._WELCOME_KEY] is True
    assert "<b>on</b>" in msg.last_reply


async def test_an_admin_can_turn_it_off_again():
    ctx = welcome_ctx(enabled=True)
    msg, ctx = await toggle(["off"], ctx=ctx)
    assert ctx.chat_data[welcome._WELCOME_KEY] is False
    assert "<b>off</b>" in msg.last_reply


async def test_an_ordinary_member_cannot_change_it():
    """Asserted on the stored setting, not merely on the refusal: a message that says no
    while the write already happened is the failure worth catching."""
    ctx = welcome_ctx()
    msg, ctx = await toggle(["on"], user_id=MEMBER, ctx=ctx)
    assert welcome._WELCOME_KEY not in ctx.chat_data
    assert "admins" in msg.last_reply


async def test_a_bot_admin_can_change_it(monkeypatch):
    """The escape hatch for a group whose own admins are unreachable."""
    monkeypatch.setattr(welcome, "is_admin_user", _true)
    ctx = welcome_ctx()
    msg, ctx = await toggle(["on"], user_id=MEMBER, ctx=ctx)
    assert ctx.chat_data[welcome._WELCOME_KEY] is True


async def _true(user_id):
    return True


async def test_no_argument_reports_the_current_state():
    ctx = welcome_ctx(enabled=True)
    msg, _ = await toggle([], ctx=ctx)
    assert "currently <b>on</b>" in msg.last_reply


async def test_an_unknown_argument_reports_the_state_rather_than_guessing():
    """ "/welcome yes" must not be read as "on" — a setting nobody meant to change is
    worse than a line of help."""
    ctx = welcome_ctx()
    msg, ctx = await toggle(["yes"], ctx=ctx)
    assert welcome._WELCOME_KEY not in ctx.chat_data
    assert "currently <b>off</b>" in msg.last_reply


async def test_it_is_refused_in_a_private_chat():
    ctx = welcome_ctx()
    msg, ctx = await toggle(["on"], ctx=ctx, chat=group(chat_type="private"))
    assert welcome._WELCOME_KEY not in ctx.chat_data
    assert "group feature" in msg.last_reply


async def test_the_setting_survives_the_persistence_roundtrip():
    """chat_data is stored as JSON by RedisPersistence; a restart must not lose a
    group's choice, least of all by turning announcements back on."""
    from conftest import assert_json_roundtrips

    _, ctx = await toggle(["on"])
    restored = assert_json_roundtrips(ctx.chat_data)
    assert welcome.is_enabled(FakeContext(chat_data=restored)) is True


# --- What a join posts --------------------------------------------------------------


async def test_a_join_says_nothing_when_the_chat_has_not_opted_in(stats_api):
    msg = await joins(welcome_ctx())
    assert msg.replies == []
    assert stats_api.requests == [], "an opted-out chat must not cost a stats lookup"


async def test_a_join_announces_games_and_achievements(stats_api):
    msg = await joins(welcome_ctx(enabled=True))
    stats_url = "https://www.tgwerewolf.com/Stats/Player/7?referer=wwstatsbot"
    assert msg.last_reply == (
        "<a href='{url}'>Alice the Villager 👱</a> has "
        "<b>100</b> games played and <b>2</b> achievements unlocked \N{EM DASH} "
        "<a href='{url}'>full stats</a>.\n".format(url=stats_url)
    )


async def test_large_game_counts_are_grouped(stats_api):
    stats_api.routes["/Stats/PlayerStats/"] = dict(stats_api.routes["/Stats/PlayerStats/"], gamesPlayed=20000)
    msg = await joins(welcome_ctx(enabled=True))
    assert "<b>20,000</b> games played" in msg.last_reply


async def test_a_player_with_no_games_is_still_greeted(stats_api):
    stats_api.routes["/Stats/PlayerStats/"] = {}
    msg = await joins(welcome_ctx(enabled=True))
    assert msg.last_reply == "<a href='tg://user?id=7'>Alice</a> has not played any games yet.\n"


async def test_a_joining_bot_is_not_greeted(stats_api):
    """Including this bot being added to the group — a bot has no player stats, so the
    only thing it could ever be greeted as is somebody who has never played."""
    msg = message("", chat=group(), new_chat_members=[FakeUser(42, "SomeBot", is_bot=True)])
    await welcome.greet_new_members(FakeUpdate(message=msg), welcome_ctx(enabled=True))
    assert msg.replies == []


async def test_a_name_is_escaped(stats_api):
    msg = await joins(welcome_ctx(enabled=True), users=((7, "Al & <b>Sons</b>"),))
    assert "Al &amp; &lt;b&gt;Sons&lt;/b&gt;" in msg.last_reply


async def test_several_joiners_share_one_message(stats_api):
    """One message, one line each — not one message per person."""
    msg = await joins(welcome_ctx(enabled=True), users=((7, "Alice"), (8, "Bob")))
    assert len(msg.replies) == 1
    assert msg.last_reply.count("full stats") == 2


async def test_a_mass_add_is_capped_and_says_how_many_were_left_out(stats_api):
    joined = tuple((i, "Player{}".format(i)) for i in range(1, 9))
    msg = await joins(welcome_ctx(enabled=True), users=joined)

    assert msg.last_reply.count("full stats") == welcome._MAX_ANNOUNCED
    assert "and 3 more joined" in msg.last_reply


async def test_one_failed_lookup_does_not_sink_the_greeting(stats_api):
    """A network failure for one player must not turn the whole join silent, and must
    never be reported as "has not played any games" — that is a claim about the player."""
    stats_api.fail_pids.add("7")
    msg = await joins(welcome_ctx(enabled=True), users=((7, "Alice"), (8, "Bob")))

    assert "Alice" not in msg.last_reply
    assert "Bob" in msg.last_reply


async def test_a_join_where_every_lookup_fails_says_nothing(stats_api):
    stats_api.fail_pids.add("7")
    msg = await joins(welcome_ctx(enabled=True))
    assert msg.replies == []
