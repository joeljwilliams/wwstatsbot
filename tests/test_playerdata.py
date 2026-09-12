"""The record of every stats lookup: what it stores, what it announces, what it falls back to.

Three behaviours meet in `playerdata._read`, and each one has a way of going quietly wrong
that only a test catches:

* **Recording must never break a lookup.** A write that failed, or no database at all, has
  to leave the command answering exactly as it did before. The whole suite runs with
  `db._pool` unset, so every other test file is also asserting this — but only by accident,
  which is why it is pinned here on purpose.
* **The first lookup of a player is a baseline, not news.** Without that, the first time
  anybody ran /stats on a veteran the log group would receive their entire collection as
  "new".
* **A fallback must say it is one.** Numbers read out of the record look identical to live
  ones, and this bot's rule about that is already written on /schall's remembered list.
"""

import httpx
import pytest
from conftest import (
    ACHIEVEMENTS_JSON,
    STATS_JSON,
    FakeBot,
    FakeContext,
    FakeEntity,
    FakeUpdate,
    FakeUser,
    bot_message,
    message,
)

import api
import builders
import db
import playerdata
import settings
import templates as t
from handlers import search

LOG_GROUP = -1009999


class FakeStore:
    """An in-memory stand-in for the two snapshot functions in db.py.

    Keyed like the real table, (user_id, kind), and it returns the *previous* payload from a
    save exactly as `save_player_snapshot` does — that return value is the whole basis of
    new-achievement detection, so a fake that got it wrong would make every announcement
    test agree with a broken implementation.
    """

    def __init__(self):
        self.rows = {}
        self.age = 0.0
        self.write_error = None

    def install(self, monkeypatch):
        monkeypatch.setattr(db, "has_pool", lambda: True)
        monkeypatch.setattr(db, "save_player_snapshot", self.save)
        monkeypatch.setattr(db, "load_player_snapshot", self.load)
        return self

    def seed(self, user_id, kind, payload, name=""):
        self.rows[(user_id, kind)] = (payload, name)

    async def save(self, user_id, kind, payload, name=None):
        if self.write_error is not None:
            raise self.write_error
        key = (user_id, kind)
        previous = self.rows.get(key)
        # COALESCE(NULLIF(...)): a caller with no name must not blank a recorded one.
        kept = name or (previous[1] if previous else "")
        self.rows[key] = (payload, kept)
        return None if previous is None else previous[0]

    async def load(self, user_id, kind):
        row = self.rows.get((user_id, kind))
        return None if row is None else (row[0], self.age)


@pytest.fixture
def store(monkeypatch):
    return FakeStore().install(monkeypatch)


@pytest.fixture
def log_group(monkeypatch):
    """A bot installed as the announcer, with a log group to announce into."""
    bot = FakeBot()
    monkeypatch.setattr(settings, "LOG_GROUP_ID", LOG_GROUP)
    playerdata.set_announcer(bot)
    yield bot
    playerdata.set_announcer(None)


def announcements(bot):
    return [sent for sent in bot.sent if sent["chat_id"] == LOG_GROUP]


# --- Recording --------------------------------------------------------------


async def test_a_lookup_is_recorded(stats_api, store):
    reading = await playerdata.get_achievements(7)

    assert reading.data == ACHIEVEMENTS_JSON
    assert reading.age is None, "a live answer carries no age"
    assert store.rows[(7, playerdata.ACHIEVEMENTS)][0] == ACHIEVEMENTS_JSON


async def test_every_endpoint_gets_its_own_row(stats_api, store):
    await playerdata.get_stats(7)
    await playerdata.get_kills(7)
    await playerdata.get_killed_by(7)
    await playerdata.get_deaths(7)
    await playerdata.get_achievements(7)

    assert {kind for _, kind in store.rows} == set(playerdata.KINDS)


async def test_a_name_is_only_recorded_when_the_caller_has_a_raw_one(stats_api, store):
    """Callers holding an escaped name pass none, and must not blank what is stored."""
    await playerdata.get_stats(7, "Al & Sons")
    assert store.rows[(7, playerdata.STATS)][1] == "Al & Sons"

    await playerdata.get_stats(7)
    assert store.rows[(7, playerdata.STATS)][1] == "Al & Sons"


async def test_a_player_with_no_games_is_not_recorded(store, monkeypatch):
    """The API answers null for one. There is nothing to remember, and a stored null would
    be indistinguishable from never having looked."""

    async def no_games(user_id):
        return None

    monkeypatch.setattr(api, "get_stats", no_games)
    assert (await playerdata.get_stats(7)).data is None
    assert store.rows == {}


async def test_a_write_failure_still_answers(stats_api, store):
    store.write_error = RuntimeError("postgres is having a moment")
    assert (await playerdata.get_stats(7)).data["gamesPlayed"] == 100


async def test_no_database_still_answers(stats_api, monkeypatch):
    """How the whole suite runs, and how the bot behaves if the pool ever went away."""
    monkeypatch.setattr(db, "has_pool", lambda: False)
    assert (await playerdata.get_kills(7)).data


async def test_an_empty_achievement_list_does_not_erase_a_full_one(stats_api, store, log_group):
    """Achievements are never revoked, so an empty list where we hold a full one is the API
    answering badly — and recording it would report the whole collection as new on recovery."""
    store.seed(7, playerdata.ACHIEVEMENTS, ACHIEVEMENTS_JSON)
    stats_api.set_achievements(7, [])

    assert (await playerdata.get_achievements(7)).data == []
    assert store.rows[(7, playerdata.ACHIEVEMENTS)][0] == ACHIEVEMENTS_JSON
    assert announcements(log_group) == []


async def test_an_empty_list_is_recorded_for_a_player_who_has_none(stats_api, store):
    """The guard above must not stop a genuinely empty collection being baselined."""
    stats_api.set_achievements(7, [])
    await playerdata.get_achievements(7)
    assert store.rows[(7, playerdata.ACHIEVEMENTS)][0] == []


# --- Announcing -------------------------------------------------------------


async def test_the_first_lookup_of_a_player_announces_nothing(stats_api, store, log_group):
    """A diff against no record is not news — it is everything they ever earned."""
    await playerdata.get_achievements(7)
    assert announcements(log_group) == []


async def test_a_new_achievement_is_announced(stats_api, store, log_group):
    store.seed(7, playerdata.ACHIEVEMENTS, [{"name": "Welcome to Hell"}], name="Alice")
    stats_api.set_achievements(7, ["Welcome to Hell", "Busy Night"])

    await playerdata.get_achievements(7, "Alice")

    posted = announcements(log_group)
    assert len(posted) == 1
    assert "Busy Night" in posted[0]["text"]
    assert "Welcome to Hell" not in posted[0]["text"], "only what is new"
    assert "Alice" in posted[0]["text"]


async def test_nothing_new_is_not_announced(stats_api, store, log_group):
    store.seed(7, playerdata.ACHIEVEMENTS, ACHIEVEMENTS_JSON)
    await playerdata.get_achievements(7)
    assert announcements(log_group) == []


async def test_a_second_lookup_does_not_re_announce(stats_api, store, log_group):
    """The record is updated by the announcing lookup, so the next one has nothing to say."""
    store.seed(7, playerdata.ACHIEVEMENTS, [{"name": "Welcome to Hell"}])
    stats_api.set_achievements(7, ["Welcome to Hell", "Busy Night"])

    await playerdata.get_achievements(7)
    await playerdata.get_achievements(7)

    assert len(announcements(log_group)) == 1


async def test_an_unnamed_player_is_announced_by_id(stats_api, store, log_group):
    """Most lookups carry no name (theirs is already escaped); the id still identifies them."""
    store.seed(7, playerdata.ACHIEVEMENTS, [])
    stats_api.set_achievements(7, ["Busy Night"])

    await playerdata.get_achievements(7)

    assert "7" in announcements(log_group)[0]["text"]


async def test_a_long_run_of_new_achievements_is_capped_and_counted(stats_api, store, log_group):
    store.seed(7, playerdata.ACHIEVEMENTS, [])
    earned = ["Achievement {}".format(i) for i in range(playerdata._ANNOUNCE_MAX + 3)]
    stats_api.set_achievements(7, earned)

    await playerdata.get_achievements(7)

    text = announcements(log_group)[0]["text"]
    assert text.count("•") == playerdata._ANNOUNCE_MAX
    assert "and 3 more" in text


async def test_an_achievement_name_is_escaped_in_the_announcement(stats_api, store, log_group):
    store.seed(7, playerdata.ACHIEVEMENTS, [])
    stats_api.set_achievements(7, ["Al & Sons"])

    await playerdata.get_achievements(7)

    assert "Al &amp; Sons" in announcements(log_group)[0]["text"]


async def test_nothing_is_announced_without_a_log_group(stats_api, store, monkeypatch):
    bot = FakeBot()
    monkeypatch.setattr(settings, "LOG_GROUP_ID", None)
    playerdata.set_announcer(bot)
    store.seed(7, playerdata.ACHIEVEMENTS, [])
    stats_api.set_achievements(7, ["Busy Night"])
    try:
        await playerdata.get_achievements(7)
    finally:
        playerdata.set_announcer(None)
    assert bot.sent == []


async def test_a_refused_announcement_does_not_sink_the_lookup(stats_api, store, monkeypatch):
    """The log group is where problems are reported, so failing to reach it can only be logged."""
    monkeypatch.setattr(settings, "LOG_GROUP_ID", LOG_GROUP)
    playerdata.set_announcer(FakeBot(send_error=RuntimeError("chat not found")))
    store.seed(7, playerdata.ACHIEVEMENTS, [])
    stats_api.set_achievements(7, ["Busy Night"])
    try:
        reading = await playerdata.get_achievements(7)
    finally:
        playerdata.set_announcer(None)
    assert [a["name"] for a in reading.data] == ["Busy Night"]


async def test_only_achievements_are_announced(stats_api, store, log_group):
    store.seed(7, playerdata.STATS, {"gamesPlayed": 1})
    await playerdata.get_stats(7)
    assert announcements(log_group) == []


# --- Falling back -----------------------------------------------------------


async def test_an_unreachable_api_is_answered_from_the_record(stats_api, store):
    store.seed(7, playerdata.STATS, {"gamesPlayed": 42})
    store.age = 900
    stats_api.fail_pids.add("7")

    reading = await playerdata.get_stats(7)

    assert reading.data == {"gamesPlayed": 42}
    assert reading.age == 900


async def test_an_unreachable_api_with_no_record_still_raises(stats_api, store):
    """Callers that already handle a failed lookup must keep getting the failure: an empty
    payload would read as a statement about the player rather than about the site."""
    stats_api.fail_pids.add("7")
    with pytest.raises(httpx.ConnectError):
        await playerdata.get_stats(7)


async def test_a_fallback_records_nothing(stats_api, store):
    """Nothing new was learned, so the record's age must keep meaning what it says."""
    store.seed(7, playerdata.ACHIEVEMENTS, ACHIEVEMENTS_JSON)
    stats_api.fail_pids.add("7")

    await playerdata.get_achievements(7)

    assert store.rows[(7, playerdata.ACHIEVEMENTS)][0] == ACHIEVEMENTS_JSON


async def test_a_fallback_announces_nothing(stats_api, store, log_group):
    store.seed(7, playerdata.ACHIEVEMENTS, [])
    stats_api.fail_pids.add("7")
    await playerdata.get_achievements(7)
    assert announcements(log_group) == []


async def test_an_unreadable_record_reports_the_original_failure(stats_api, store, monkeypatch):
    """The fallback's own fallback: the caller is told the API failed, not the database."""

    async def broken(user_id, kind):
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(db, "load_player_snapshot", broken)
    stats_api.fail_pids.add("7")
    with pytest.raises(httpx.ConnectError):
        await playerdata.get_stats(7)


# --- Saying so --------------------------------------------------------------


def test_a_live_answer_gets_no_notice():
    assert playerdata.stale_notice(None) == ""
    assert playerdata.stale_notice(None, None) == ""


def test_a_message_reports_the_oldest_part_of_itself():
    """/stats and /deaths each read two endpoints, and either may have fallen back."""
    assert "2h ago" in playerdata.stale_notice(60, 7200, None)


def test_ages_read_in_the_largest_unit_that_fits():
    assert playerdata.age_label(0) == t.AGE_MOMENTS
    assert playerdata.age_label(59) == t.AGE_MOMENTS
    assert playerdata.age_label(60) == "1m ago"
    assert playerdata.age_label(3599) == "59m ago"
    assert playerdata.age_label(3600) == "1h ago"
    assert playerdata.age_label(86399) == "23h ago"
    assert playerdata.age_label(86400) == "1d ago"
    assert playerdata.age_label(-5) == t.AGE_MOMENTS, "a clock skew must not render '-1d ago'"


async def test_a_stats_card_built_from_the_record_says_so(stats_api, store):
    """The numbers look identical either way, which is the entire reason for the footer."""
    store.seed(7, playerdata.STATS, STATS_JSON)
    store.seed(7, playerdata.ACHIEVEMENTS, ACHIEVEMENTS_JSON)
    store.age = 7200
    stats_api.fail_pids.add("7")

    msg = await builders.build_stats_msg(7, "Alice")

    assert "100" in msg, "the record still answers the question"
    assert "2h ago" in msg
    assert "unreachable" in msg


async def test_a_live_stats_card_is_byte_identical_to_before(stats_api, store):
    """The footer is the only difference, so a working lookup must render exactly as it did."""
    msg = await builders.build_stats_msg(7, "Alice")
    assert "unreachable" not in msg
    assert "\N{CLOCK FACE ONE OCLOCK}" not in msg


# --- The one door -----------------------------------------------------------


def test_nothing_outside_playerdata_calls_a_fetcher_directly():
    """Recording, announcing and the fallback are all consequences of going through
    playerdata. A handler that called api.get_stats() itself would silently opt out of all
    three while looking perfectly correct — so the rule is checked rather than remembered.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    sources = [p for p in sorted(repo.glob("*.py")) if p.name not in {"playerdata.py", "api.py"}]
    sources += sorted(repo.glob("handlers/*.py"))

    offenders = [
        "{}:{}".format(path.relative_to(repo), i)
        for path in sources
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if "api.get_" in line and not line.lstrip().startswith("#")
    ]
    assert not offenders, "these bypass playerdata and its record: {}".format(offenders)


def test_every_kind_names_a_real_fetcher():
    """The kinds are stored strings and the fetchers are resolved by name at call time, so
    a rename on either side would fail in production rather than at import."""
    for kind in playerdata.KINDS:
        assert callable(getattr(api, playerdata._FETCHERS[kind]))


# --- Through the commands ---------------------------------------------------


async def test_a_schall_list_says_when_a_player_came_from_the_record(achievements, no_fts, stats_api, store):
    """A /schall answer is assembled from one lookup per player, and any subset of them can
    have fallen back while the rest were live. The tick beside a name looks the same either
    way, so the message as a whole has to carry the notice."""
    store.seed(2, playerdata.ACHIEVEMENTS, [{"name": "Busy Night"}])
    store.age = 3600
    stats_api.fail_pids.add("2")

    roster = bot_message(
        "Alice Bob",
        entities=[
            FakeEntity("text_mention", offset=0, length=5, user=FakeUser(1, "Alice")),
            FakeEntity("text_mention", offset=6, length=3, user=FakeUser(2, "Bob")),
        ],
    )
    msg = message("/sch busy", reply_to_message=roster)
    await search.display_search_all(FakeUpdate(message=msg), FakeContext(args=["busy"]))

    assert "1h ago" in msg.last_reply
    assert "unreachable" in msg.last_reply


async def test_a_fully_live_schall_list_carries_no_notice(achievements, no_fts, stats_api, store):
    roster = bot_message("Alice", entities=[FakeEntity("text_mention", offset=0, length=5, user=FakeUser(1, "Alice"))])
    msg = message("/sch busy", reply_to_message=roster)
    await search.display_search_all(FakeUpdate(message=msg), FakeContext(args=["busy"]))

    assert "unreachable" not in msg.last_reply


async def test_the_schall_roster_teaches_the_record_who_a_player_is(achievements, no_fts, stats_api, store):
    """One of the three call sites holding an unescaped name (see playerdata._read)."""
    roster = bot_message(
        "Al & Sons", entities=[FakeEntity("text_mention", offset=0, length=9, user=FakeUser(1, "Al & Sons"))]
    )
    await search.display_search_all(
        FakeUpdate(message=message("/sch busy", reply_to_message=roster)), FakeContext(args=["busy"])
    )

    assert store.rows[(1, playerdata.ACHIEVEMENTS)][1] == "Al & Sons", "stored raw, escaped at render"
