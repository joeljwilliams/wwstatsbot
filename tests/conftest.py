"""Shared test machinery.

Two things happen here before anything else can work:

1. **Config env is stubbed at module scope, before any application import.** pytest
   imports conftest before every test module, so this runs first — which matters because
   `settings.py` reads the environment at *its* import, and a test module importing
   `main` pulls `settings` in with it. It matters for more than CI: a developer's
   checkout has a real `config.py` holding a live bot token, and env wins over
   `config.py`, so this is what guarantees the suite can never pick up (or send anything
   with) the real credentials.

2. **The shared httpx client is replaced per test.** `api.client` is created at import;
   the `stats_api` fixture swaps in a `MockTransport`-backed client so no test touches
   tgwerewolf.com. It patches the attribute on the module rather than importing the
   object, because a stale patch target fails silently — the suite would go on passing
   while hitting the real API. `forbid_real_network` (autouse) is the backstop.

The Telegram fakes are deliberately hand-rolled `SimpleNamespace`-ish objects rather
than a PTB test harness: the handlers only ever touch a handful of attributes, and
recording `reply_text`/`answer`/`edit_message_text` calls is the whole assertion
surface. Constructing real `telegram.Update` objects would add a dependency and a lot
of required-field noise for no extra coverage.
"""

import json
import os
from types import SimpleNamespace

# --- 1. Stub config BEFORE importing the app ------------------------------------
# Must precede `import main` (and anything that imports it). Values are inert
# placeholders; nothing in the suite opens a socket to Telegram or Postgres.
os.environ["BOT_TOKEN"] = "12345:TEST-TOKEN-NOT-REAL"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["SUPERUSER_ID"] = "999"
# Keep LOG_GROUP_ID unset: error_handler's "report to the log group" branch is opt-in
# per test, so the default path stays quiet.
os.environ.pop("LOG_GROUP_ID", None)
os.environ.pop("REDIS_URL", None)

import httpx  # noqa: E402
import pytest  # noqa: E402

import api  # noqa: E402
import db  # noqa: E402

SUPERUSER_ID = 999


@pytest.fixture(autouse=True)
def clean_alt_cache():
    """Empty db's alt-account cache around every test.

    It is a module-level set, so without this a Postgres test that marks an alt leaks into
    every later test in the run — and the symptom is bizarre: a player silently missing
    from a rendered list because their *id* happened to match one marked several files ago.
    Diagnosed exactly once, from that symptom, which is reason enough to make it
    impossible rather than remember it.
    """
    db._ALTS = set()
    yield
    db._ALTS = set()


# --- Achievement fixtures --------------------------------------------------------

# Entries use the legacy ACHV cache shape that db.load_cache() produces: 'desc' (not
# 'description'), and 'inactive'/'not_via_playing' present ONLY when true. Tests that
# assert on the flags rely on that asymmetry, because handler code does too
# (a.get('inactive'), never a['inactive']).
ACHIEVEMENTS = [
    {
        "name": "Welcome to Hell",
        "desc": "Play a game",
        "type": "game-end",
        "notes": "",
    },
    {
        "name": "O HAI DER!",
        "desc": "Play a game with Para's secret account (not @para949)",
        "type": "game-end",
        "notes": "",
    },
    {
        "name": "Liquid Business",
        "desc": "Drink the potion & survive",
        "type": "instantaneous",
        "notes": "\N{MEMO} Needs the drunk role.\n\N{GAME DIE} ~5%",
    },
    {
        "name": "Busy Night",
        "desc": "Be visited by four different roles in one night",
        "type": "instantaneous",
        "notes": "",
    },
    {
        "name": "Explorer",
        "desc": "Play at least 2 games each in 10 different groups",
        "type": "game-end",
        "notes": "",
        "inactive": True,
    },
    {
        "name": "Here's Johnny!",
        "desc": "Get 50 kills as the serial killer",
        "type": "instantaneous",
        "notes": "",
        "not_via_playing": True,
    },
]


@pytest.fixture
def achievements(monkeypatch):
    """Point db.get_achievements() at the fixture list (no database involved)."""
    monkeypatch.setattr(db, "get_achievements", lambda: ACHIEVEMENTS)
    return ACHIEVEMENTS


@pytest.fixture
def no_fts(monkeypatch):
    """Force build_info_results onto its substring fallback.

    search_achievements() needs Postgres. Returning nothing from it exercises the
    same in-memory path the real bot uses when FTS misses, so search-shaped tests
    stay unit tests.
    """

    async def _empty(query):
        return []

    monkeypatch.setattr(db, "search_achievements", _empty)


# --- Stats API (httpx MockTransport) ---------------------------------------------

# Canned tgwerewolf.com payloads, shaped exactly like the real JSON. Kept small but
# with the fields every builder reads, including the nested won/lost/survived dicts
# whose 'percent' values are strings in the real API.
STATS_JSON = {
    "gamesPlayed": 100,
    "mostCommonRole": "Villager",
    "won": {"total": 60, "percent": "60"},
    "lost": {"total": 40, "percent": "40"},
    "survived": {"total": 50, "percent": "50"},
    "mostKilled": {"name": "Bob", "times": 7},
    "mostKilledBy": {"name": "Al & Sons", "times": 3},
}
KILLS_JSON = [{"name": "Bob", "times": 7}, {"name": "Al & Sons", "times": 3}]
KILLED_BY_JSON = [{"name": "Carol", "times": 5}]
DEATHS_JSON = [{"method": "Lynched", "percent": "40"}, {"method": "Eaten", "percent": "20"}]
ACHIEVEMENTS_JSON = [{"name": "Welcome to Hell"}, {"name": "Busy Night"}]

_ROUTES = {
    "/Stats/PlayerStats/": STATS_JSON,
    "/Stats/PlayerKills/": KILLS_JSON,
    "/Stats/PlayerKilledBy/": KILLED_BY_JSON,
    "/Stats/PlayerDeaths/": DEATHS_JSON,
    "/Stats/PlayerAchievements/": ACHIEVEMENTS_JSON,
    # wwstats.check() builds its own absolute URL against a different path shape.
    "/stats/PlayerAchievements/": ACHIEVEMENTS_JSON,
}


class StatsAPI:
    """A fake stats API: records requests and lets a test override any route."""

    def __init__(self):
        self.requests = []
        self.routes = dict(_ROUTES)
        # Per-user overrides keyed by (path, pid) for multi-player tests like /schall.
        self.by_pid = {}
        self.fail_pids = set()

    def set_achievements(self, pid, names):
        self.by_pid[("/Stats/PlayerAchievements/", str(pid))] = [{"name": n} for n in names]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        pid = request.url.params.get("pid")
        if pid in self.fail_pids:
            raise httpx.ConnectError("simulated network failure")
        path = request.url.path
        if (path, pid) in self.by_pid:
            return httpx.Response(200, json=self.by_pid[(path, pid)])
        if path in self.routes:
            return httpx.Response(200, json=self.routes[path])
        return httpx.Response(404, json={})


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    """Make the shared client unusable unless a test opts into the fake API.

    The `stats_api` fixture patches an attribute by name, and a patch whose target has
    drifted fails **silently** — the suite would keep passing while making real requests
    to tgwerewolf.com. That is exactly what would happen if the client moved module again
    and this fixture were not updated with it.

    So every test starts with a client that raises on any request. Tests that want the
    stats API take `stats_api`, which replaces it with the working mock; anything else
    that reaches the network fails loudly and names the URL it tried.
    """

    def explode(request):
        raise AssertionError(
            "real HTTP request attempted: {} {}\n"
            "Either this test needs the `stats_api` fixture, or `stats_api`'s patch "
            "target has drifted from where the shared client actually lives.".format(request.method, request.url)
        )

    monkeypatch.setattr(api, "client", httpx.AsyncClient(transport=httpx.MockTransport(explode)))


@pytest.fixture
def stats_api(monkeypatch):
    """Replace api.client with a MockTransport client. No network, no real sockets.

    Patches the attribute on the `api` module, which is where the client lives and where
    every fetcher resolves it from. `forbid_real_network` above is the backstop for this
    patch target going stale.
    """
    fake = StatsAPI()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), timeout=15)
    monkeypatch.setattr(api, "client", client)
    # No teardown: MockTransport holds no sockets, and monkeypatch restores api.client.
    return fake


# --- Telegram fakes ---------------------------------------------------------------


class FakeUser:
    def __init__(self, user_id=1, first_name="Alice", username=None, is_bot=False):
        self.id = user_id
        self.first_name = first_name
        self.username = username
        self.is_bot = is_bot


class FakeChat:
    def __init__(self, chat_type="group", chat_id=-100):
        self.type = chat_type
        self.id = chat_id


class FakeEntity:
    """Stands in for telegram.MessageEntity (compared by .type string)."""

    def __init__(self, entity_type, offset=0, length=0, user=None):
        self.type = entity_type
        self.offset = offset
        self.length = length
        self.user = user


class FakeMessage:
    """Records every reply_text call in .replies as (text, kwargs)."""

    def __init__(
        self,
        text="",
        from_user=None,
        chat=None,
        reply_to_message=None,
        entities=None,
        caption=None,
        caption_entities=None,
        message_id=1,
    ):
        self.message_id = message_id
        self.text = text
        self.caption = caption
        self.from_user = from_user or FakeUser()
        self.chat = chat or FakeChat()
        self.reply_to_message = reply_to_message
        self.entities = entities or []
        self.caption_entities = caption_entities or []
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return FakeMessage(text=text)

    @property
    def last_reply(self):
        assert self.replies, "expected a reply, got none"
        return self.replies[-1][0]


class FakeCallbackQuery:
    """Records .answer() and .edit_message_text() calls."""

    def __init__(self, data="", from_user=None):
        self.data = data
        self.from_user = from_user or FakeUser()
        self.answers = []
        self.edits = []
        self.edit_error = None

    async def answer(self, text=None, show_alert=False):
        self.answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_text(self, text, **kwargs):
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append((text, kwargs))


class FakeInlineQuery:
    """Records the results passed to answer(), plus the answer kwargs."""

    def __init__(self, query="", from_user=None):
        self.query = query
        self.from_user = from_user or FakeUser()
        self.answers = []

    async def answer(self, results, **kwargs):
        self.answers.append({"results": results, **kwargs})

    @property
    def results(self):
        assert self.answers, "expected the inline query to be answered"
        return self.answers[-1]["results"]


class FakeBot:
    def __init__(self, username="wwstatsbot", send_error=None, edit_error=None, chat_admins=()):
        self.username = username
        # Telegram chat administrators, by user id. The stand-in session asks so that an
        # admin who is not playing can still stop a game that outlived its round.
        self.chat_admins = set(chat_admins)
        self.sent = []
        self.edits = []
        self.markup_edits = []
        self.commands = None
        self._send_error = send_error
        self._edit_error = edit_error
        self._next_message_id = 100

    async def send_message(self, chat_id, text, **kwargs):
        if self._send_error is not None:
            raise self._send_error
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        # Returned so callers that keep a message id — the stand-in session edits one
        # roster message for the length of a game — get a stable, distinct one.
        self._next_message_id += 1
        return FakeMessage(text=text, message_id=self._next_message_id)

    async def edit_message_text(self, chat_id=None, message_id=None, text="", **kwargs):
        if self._edit_error is not None:
            raise self._edit_error
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kwargs})

    async def edit_message_reply_markup(self, chat_id=None, message_id=None, reply_markup=None):
        self.markup_edits.append({"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup})

    async def set_my_commands(self, commands):
        self.commands = commands

    async def get_chat_member(self, chat_id, user_id):
        status = "administrator" if user_id in self.chat_admins else "member"
        return SimpleNamespace(status=status, user=FakeUser(user_id))


class FakeJob:
    """One scheduled job, as PTB's JobQueue would hand it back."""

    def __init__(self, callback, when, chat_id=None, name=None):
        self.callback = callback
        self.when = when
        self.chat_id = chat_id
        self.name = name
        self.removed = False
        self.ran = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    """Records scheduled jobs and runs them on demand.

    Real enough for the property that matters: get_jobs_by_name is what the stand-in
    session's trailing debounce checks to decide whether a publish is already pending, so a
    fake that always returned nothing would make the coalescing test pass while the real
    thing edited once per reveal.
    """

    def __init__(self):
        self.jobs = []

    def run_once(self, callback, when, chat_id=None, name=None, **kwargs):
        job = FakeJob(callback, when, chat_id=chat_id, name=name)
        self.jobs.append(job)
        return job

    def get_jobs_by_name(self, name):
        return tuple(job for job in self.jobs if job.name == name and not job.removed and not job.ran)

    def pending(self, name=None):
        return [j for j in self.jobs if not j.removed and not j.ran and (name is None or j.name == name)]

    async def run_pending(self, context, elapsed=5):
        """Fire the jobs due within `elapsed` seconds, oldest first.

        The delay is honoured rather than ignored, because the stand-in session schedules
        two very different jobs against one chat: a five-second publish and a ten-minute
        idle timer. A fake that ran "everything pending" would fire the idle warning as
        part of every debounce test and quietly make them assert the wrong thing.
        """
        for job in list(self.pending()):
            if job.when > elapsed:
                continue
            job.ran = True
            context.job = job
            await job.callback(context)
        context.job = None


class FakeUpdate:
    def __init__(self, message=None, callback_query=None, inline_query=None):
        self.message = message
        self.callback_query = callback_query
        self.inline_query = inline_query


class FakeContext:
    """Stands in for ContextTypes.DEFAULT_TYPE.

    bot_data is a plain dict, exactly as PTB provides it, so the token-store eviction
    and JSON-serializability tests exercise the real code path.
    """

    def __init__(self, args=None, bot=None, bot_data=None, chat_data=None, error=None, job_queue=None):
        self.args = args if args is not None else []
        # The stand-in session schedules its message updates; a chat with no queue is
        # the shape of a bot built without the job-queue extra, which must still work.
        self.job_queue = FakeJobQueue() if job_queue is None else job_queue
        self.job = None
        self.bot = bot or FakeBot()
        self.bot_data = bot_data if bot_data is not None else {}
        # Per-chat store, as PTB provides it. /schall's remembered player list lives here,
        # so a fresh FakeContext means a chat with nothing remembered.
        self.chat_data = chat_data if chat_data is not None else {}
        self.error = error


@pytest.fixture
def context():
    return FakeContext()


def message(text="", **kwargs):
    """Shorthand for a user message."""
    return FakeMessage(text=text, **kwargs)


def bot_message(text="", entities=None, **kwargs):
    """A message posted by a bot — the shape /sch and /info reroute on."""
    return FakeMessage(
        text=text, from_user=FakeUser(user_id=42, first_name="WerewolfBot", is_bot=True), entities=entities, **kwargs
    )


def assert_json_roundtrips(value):
    """Assert a bot_data payload survives the Redis persistence round-trip.

    RedisPersistence stores state as JSON, so anything stashed in bot_data must be
    JSON-serializable — and tuples come back as lists, which callers must tolerate.
    Returns the round-tripped value so tests can assert on the degraded form.
    """
    return json.loads(json.dumps(value))
