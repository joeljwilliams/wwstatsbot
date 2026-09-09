# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`@wwstatsbot` — a Telegram bot that reads player stats and achievements from the
Werewolf-for-Telegram public stats API (`tgwerewolf.com`) and renders them in chat.
Long-lived fork of an older bot, rewritten for python-telegram-bot v22 (async).
Python 3.12 in the container; long-polling by default, with an optional webhook mode
(`WEBHOOK_URL`) served on the health port.

## Workflow

**Never commit directly to `devel` or `main`.** Both are protected by convention, and
`main` is what Railway deploys — a commit there is a production deploy.

Every change, however small, follows the same loop:

```
feature branch  ──PR──▶  devel  ──PR──▶  main  ──▶  auto-tag + release + deploy
```

1. **Branch off `devel`**, never off `main`. Prefix to match existing names: `feat/`,
   `fix/`, `bugfix/`, `refactor/`, `chore/`, `test/`.
2. **Open a PR into `devel`.** CI runs on every PR regardless of base, so stacking a
   branch on another feature branch is fine and gets full checks.
3. **Wait for the PR to be live-tested.** "Tested" here means the bot actually run against
   Telegram with a real token — not CI going green. Don't merge on CI alone, and don't
   report a change as verified when only CI has passed: the suite cannot catch anything at
   the Telegram API boundary (a `parse_mode` rejection, a malformed keyboard).
4. **`devel` → `main` is a release**, and deploys to production. See *Releasing*.

Use a **merge commit**, not a squash, when promoting `devel` → `main`: squashing erases the
`ruff format` SHA that `.git-blame-ignore-revs` references, and collapses history that is
deliberately kept separate so "output changed" and "code moved" are never ambiguous in one
diff.

### Commits

Conventional commits, one logical change each. Prefixes in use here:

| Prefix | For |
|---|---|
| `feat(scope):` | new user-facing behaviour |
| `fix(scope):` | bug fixes, including user-visible copy corrections |
| `refactor:` | code motion or simplification with no behaviour change |
| `test:` | tests only |
| `ci:` / `chore:` | pipeline, tooling, dependencies |
| `style:` | formatting only — reserved for whole-repo `ruff format` passes |

Two rules that matter more than they look:

- **Never mix a copy change with a refactor.** A golden-test diff is the review artifact
  showing exactly which bytes users will see differently; mixing makes it unreadable.
- **Bump the version and its mirror in the same commit** (see *Releasing*).

## Commands

Dependencies are managed with **uv** (`pyproject.toml` + committed `uv.lock`); there is
no `requirements.txt`. Everything is pinned exactly, and `.python-version` pins 3.12 to
match the Dockerfile.

```bash
# Local dev
uv sync                           # creates .venv from uv.lock
cp configEXAMPLE.py config.py     # then fill in BOT_TOKEN / DATABASE_URL (config.py is gitignored)
uv run python main.py             # env vars override config.py values

LOG_FORMAT=console LOG_LEVEL=DEBUG uv run python main.py   # human-readable logs (auto on a TTY)

# Translations (Babel is a dev-only tool; the runtime uses stdlib gettext)
uv run pybabel extract -F babel.cfg -o locales/messages.pot .   # after editing templates.py
uv run pybabel update -i locales/messages.pot -d locales        # merge into existing .po
uv run pybabel compile -d locales                               # .po -> .mo (not committed)

# Test / lint
uv run pytest                     # 175 tests; the 28 Postgres ones skip by default
uv run pytest tests/test_notes.py::test_roundtrip_is_stable   # a single test
uv run ruff check . && uv run ruff format --check .

# Data-layer tests need a real Postgres. CI uses postgres:18 (matching Railway) because
# what's pinned is server-side text-search behaviour, which is version-sensitive.
docker run -d --rm --name pgtest -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest

# Container (this is the Railway deploy path — railway.json builds this Dockerfile)
docker build -t wwstatsbot . && docker run -e BOT_TOKEN=... -e DATABASE_URL=... wwstatsbot

# Health probes (HEALTH_PORT, default 8080)
curl localhost:8080/healthz   # liveness — 200 while the process lives
curl localhost:8080/readyz    # readiness — 503 until DB init + set_my_commands finish

# Webhook mode. Needs the URL to be reachable from Telegram, so locally that means a
# tunnel; the path is served on HEALTH_PORT next to the probes.
WEBHOOK_URL=https://bot.example.com uv run python main.py
```

A running instance can be inspected live: `/version` reports the release version, branch
and short commit, and
`/db <sql>` (superuser only) is a raw SQL console against production Postgres.

## Testing

`tests/conftest.py` stubs `BOT_TOKEN`/`DATABASE_URL` **at module scope, above
`import main`** — pytest loads conftest first, and env beats `config.py`, which is what
stops the suite picking up a developer's real token. Handlers are driven with hand-rolled
`SimpleNamespace`-style fakes (`FakeMessage`, `FakeContext`, …) that record
`reply_text`/`answer` calls; the stats API is an `httpx.MockTransport`, so nothing
touches the network.

**`tests/test_render_golden.py` is the load-bearing file.** It asserts whole-string
equality on every rendered message. `main.py` is being split into modules, and that is
almost pure code motion over HTML built by concatenation with manual `html.escape()` — so
if a golden fails, the refactor changed user-visible output and *that* is the bug. Only
edit an expectation when the change to what users see is intentional — and keep that edit
in its own commit, never mixed with a refactor, since the golden diff is the review
artifact showing precisely which bytes users will see differently.

Other things the suite is deliberately guarding, all of which a refactor could silently
break: `test_templates.py` cross-checks every `t.NAME` reference against `templates.py`
in both directions (drift here fails at runtime, in a handler, in production);
`test_routing.py` pins the self-overloading commands; `test_permissions.py` asserts
gated functions are *never reached* unauthorised, not merely that a refusal is printed;
`test_db.py` pins the FTS stemming contract.

`REQUIRE_POSTGRES=1` turns a missing database from a skip into a failure — CI sets it so
a broken service container can't leave the data layer silently unexercised.

Coverage is reported, never gated.

## Configuration

Every setting is read as `os.environ.get("NAME", <config.py fallback>)` at the top of
`main.py` — **env wins over `config.py`**. Required: `BOT_TOKEN`, `DATABASE_URL` (the
process exits at import if either is missing). Optional: `SUPERUSER_ID`, `LOG_GROUP_ID`,
`REDIS_URL`, `HEALTH_PORT`, `LOG_LEVEL`, `LOG_FORMAT`, `GITHUB_REPO`, `WEBHOOK_URL`,
`WEBHOOK_PATH`, `WEBHOOK_SECRET`.

**Polling vs webhook.** `WEBHOOK_URL` is the switch and nothing else: unset, the bot polls
exactly as it always has. Set, `main.start()` drives the lifecycle by hand — `initialize()`,
`set_webhook()`, `start()` — instead of `run_polling`, and PTB's own webhook server is
*not* used. It serves only its update route, while a Railway service exposes one port and
must answer `/healthz` on it, so the health server takes the POST and hands the body to
`webhook.receiver` (see the docstrings in both). Consequences worth knowing:

- The receiver runs on the **health server's thread**, so the update queue is fed through
  `loop.call_soon_threadsafe`. `asyncio.Queue` is not thread-safe, and feeding it directly
  loses updates in a way that looks like Telegram never sent them.
- `WEBHOOK_SECRET` is **derived from `BOT_TOKEN`** when unset (a namespaced SHA-256), and
  compared with `hmac.compare_digest`. A webhook without a secret accepts forged updates
  from the whole internet, so it is never empty — but it must not be *random per boot*
  either: every rolling deploy briefly runs two containers, each would register its own
  with setWebhook, the last to start would win, and the other would 401 every update while
  looking perfectly healthy. That was observed on dev, and a digest is what makes all
  replicas agree with no configuration.
- The POST route answers 503 until `initialize()` has installed the receiver, because the
  health server is up before the bot is — Telegram redelivers a 503.
- Switching back to polling needs only the variable removed: PTB always calls
  `deleteWebhook` before `getUpdates`, so no stale registration can strand it.

Deployed on Railway (`railway.json`, Dockerfile builder, healthcheck `/healthz`);
`k8s-deployment.example.yaml` is a reference manifest. Redis/Postgres are wired in
through env vars, not through committed manifests.

## Architecture

Flat module layout, one concern per file — no packages, no ORM, no framework beyond PTB.

- **`main.py`** (~1250 lines) — everything Telegram: config resolution, stats-API
  fetchers, message builders, command/callback/inline handlers, `PUBLIC_COMMANDS`,
  and `main()` wiring handlers onto the `Application`. New user-facing behaviour lands
  here.
- **`db.py`** — asyncpg pool + raw SQL. Owns the schema (`achievements`, `admins`),
  idempotent seeding, full-text search, and an **in-memory achievement cache**.
- **`templates.py`** — every user-visible string, as `str.format` templates grouped by
  parse mode. Handler code must not contain new prose; add a template.
- **`wwstats.py`** — the `/achievements` Markdown report (attained / missing /
  not-via-playing / inactive), chunked 30 items per message.
- **`achvlist.py`** — the original hardcoded `ACHV` list, now only a **seed source** for
  the database. Editing it will not change a deployed bot's data (seeding is
  `ON CONFLICT DO NOTHING`); edit rows via `/setnote` or `/db` instead.
- **`redis_persistence.py`** — durable `DictPersistence` subclass for PTB (whole state
  blob under one Redis key).
- **`health.py`**, **`logging_config.py`**, **`version.py`** — stdlib health server on a
  daemon thread (and, in webhook mode, the update POST route); structlog-over-stdlib setup;
  release version plus git/Railway commit resolution for `/version`.
- **`webhook.py`** — webhook intake: authenticate the request, parse an `Update`, put it on
  PTB's queue. Deliberately knows nothing about HTTP serving, and `health.py` deliberately
  knows nothing about Telegram — they meet at a callable returning a status code.

### Releasing

The version numbers carry the project's history: **2.x** is the async rewrite this fork
carries (1.x was the original bot), and the **minor** counts feature releases since that
rewrite. So `2.22.0` is the 22nd feature release of the rewrite, not a fresh start.

**A version bump touches exactly two places, in one commit:**

| File | What |
|---|---|
| `version.py` | `VERSION = "X.Y.Z"` — the single source of truth, and the only one that exists at runtime |
| `pyproject.toml` | `version = "X.Y.Z"` — a mirror, for uv |

`test_pyproject_version_matches` fails if they drift, so a half-bump turns CI red rather
than shipping a version that lies. Nothing else needs editing: the template reads
`{version}` from `get_version_info()`, and the tag comes from `version.py` in CI.

Which number moves:

| Change | Bump |
|---|---|
| breaking change to commands or stored data | **major** |
| new command or capability | **minor** |
| bug fix, copy fix, internal refactor | **patch** |

A refactor with no behaviour change still warrants a patch bump if it is deployed, so the
running `/version` distinguishes builds. Changes that never reach the image need no bump at
all — docs, tests, and CI are all `.dockerignore`d, so they cannot alter what is running.

It is not derived from git tags on purpose: the container has no `.git` and Railway injects
commit metadata but not tags, so a tag-derived version would read `unknown` in production —
exactly where it matters. `importlib.metadata` is unavailable too (no build backend, and
`uv sync --no-install-project`).

**Tagging and releases are automatic.** The `release` job in `ci.yml` runs on a push to
`main`, gated on lint/test/docker passing, and creates the `v<VERSION>` tag plus a GitHub
release with generated notes. Consequences worth knowing:

- A merge to `main` that did **not** bump `VERSION` is not an error — the tag already exists,
  so the job logs a notice and skips. Most merges are like this.
- To cut a release, bump `VERSION` and `pyproject.toml` on `devel`, then merge `devel` → `main`.
- The tag is created *by* `gh release create`, so tag and release can never disagree about
  which commit they point at.

### Things that will bite you

**The achievement cache is the read path.** `db.get_achievements()` is *synchronous* and
returns a module-level list loaded at startup. Any write to the `achievements` table must
be followed by `await db.load_cache()` — `update_notes()` and `db_console_cmd` both do
this. Cached entries keep the legacy `ACHV` dict shape: `desc` (not `description`), and
`inactive`/`not_via_playing` keys **present only when true** (`a.get('inactive')`, never
`a['inactive']`).

**The search column is rebuilt, not patched.** `ensure_schema()` drops and recreates
`search_tsv` on every startup. `ADD COLUMN IF NOT EXISTS` silently skips an existing column,
so editing the generation expression changed nothing on a live database — and the test fixture
drops the tables, so the whole suite passed while production kept the old definition. That gap
shipped a broken initialism search (typing `SSS` found nothing for "Should've Said Something").
The column is GENERATED, so a rebuild loses no data, and it costs ~4ms on ~110 rows. If you
change the expression, `test_ensure_schema_rebuilds_a_stale_search_column` is what proves a
live database actually picks it up — it is the one db test that does not start from dropped
tables.

**Search splits at `_INITIALISM_ONLY_MAX_LEN`, and the two halves share nothing.** A query
of 2 characters or fewer is answered by `db.search_initialism()` alone and never reaches
Postgres; anything longer goes to FTS (`search_achievements`) with a substring-on-name
fallback over the cache. The `search_tsv` generated column and the query must use the *same*
`'english'` config — a mismatch silently breaks stemmed initialisms (see the comments in
`db.py`). Callers treat `found[0]` as "the answer", so the ORDER BY in `_SEARCH_SQL` is
load-bearing.

**The short-query cutover is a hard one, and the softer version was already tried.** There
used to be a three-letter floor (`QUERY_TOO_SHORT`) on `/info`, `/sch` and `/schall`, because
two letters can only be prefix-matched and `hp` as a prefix never reaches Helpful Paranoia.
Ranking initialism hits *first* while keeping the FTS results replaced that floor and was not
enough: `/sch sa` still listed nine achievements, one of them only because "silver" appears
in a description. Being right in position one does not help when `/sch` renders positions two
through nine as well. The price, pinned by
`test_a_short_query_that_is_nobody_initialism_finds_nothing`, is that a two-letter query
nobody registered as an initialism (`he`) now finds nothing until the third character — one
keystroke, and only inline mode ever had two-letter search at all.

**The initialism rule therefore exists twice.** `db.initialism()` is a Python mirror of the
initialism expression inside `search_tsv`. It is a mirror rather than a query for a reason
the index cannot fix: the `'english'` config drops stopwords, so `TO` (Tanner Overkill) is
not in `search_tsv` at all. `test_the_python_initialism_matches_the_indexed_one` compares the
two against the whole catalogue — that pairing is the only thing stopping them drifting.

**Callback state is token-keyed in `bot_data`.** `callback_data` is capped at 64 bytes, so
`/info` and `/sch` stash payloads in `context.bot_data[...]` under a `secrets.token_urlsafe(8)`
token and put only the token in the button. Both stores are bounded at 200 with
insertion-order eviction, so an expired token is a normal case every callback must handle
(`ALLINFO_EXPIRED` / `SCHALL_EXPIRED`). With `REDIS_URL` set these survive restarts — which
means payloads must stay **JSON-serializable** and tuples come back as lists.

**`/gm on` is what hands a chat's game management to this bot.** Off by default, per chat,
stored in `chat_data` so it survives restarts and outlives any single game. On, two things
change together: `_ours_to_answer` accepts a **bare** `/gs` and lynch-order command (not
just `/gs@wwstatsbot`), and `_pin_state` pins the roster for the length of a game. Off,
only addressed commands are answered and nothing is ever pinned.

Adminness was tried as the signal for this and is the wrong one: the pin *also* needs the
Telegram permission, so a group that promoted the bot only to let it pin would have been
opted into answering bare commands without asking. A switch says which bot runs the games;
a permission does not. The switch also costs no API call, where the admin check needed a
`getChatMember` on every bare command.

Two details in `/gm` itself. Turning it **on** requires the address — a bare `/gm` while
management is off is not ours to act on, which is the whole point — while `/gm off` works
bare once on, so it is typed like everything else it governs. And switching off mid-game
unpins the roster: the pin would otherwise outlive the permission, and it is the one thing
nobody can undo without going to find the message.

**`/gm auto` is a third state, and it needs three things nobody can check.** On, the game
bot's own messages drive the session: its player list opens the roster, every later one
follows it, and its closing message closes it — `/gs`, `/ad` and `/gsend` all still work
and a human still wins. Reaching that at all needs **all three** of Bot-to-Bot
Communication Mode on for this bot in @BotFather, this bot an **admin** in the group, and
its Group Privacy Mode off; Telegram says which one is missing by delivering nothing. So
`/gm auto` answers with whichever of three replies is true, and two of them are "it can't
work yet" — silence would leave a group with the switch on, nothing happening, and no way
to find out why. It is a state of its own rather than the meaning of `on` so that a group
already running games this way does not silently start having its rosters opened for it by
a deploy.

The game engine cooperates by accident of how it already works: `SendPlayerList` is called
from its lynch, day and night cycles and only when the list changed, so a full roster
arrives at the start of a game and after every death with nobody asking. The **end** is
matched on `Game Length: hh:mm:ss`, which the engine appends exactly once, at game end. The
win messages are the obvious alternative and are the wrong one — they are GIF captions, and
they differ in every one of the game's hundred-odd language variants. Everything read here
is English, like `_ROSTER_COUNTS`, `_DEAD_ROW` and `_DOUSED_LINE` before it; a group playing
in another language keeps typing `/gs`.

**Which bot is the game bot is learned, never guessed.** A group has several bots in it and
a roster-shaped message is not proof of anything, so the answer is whichever bot a human ran
`/gs` or `/ad` against — recorded in `chat_data` the moment they do, from a list we could
actually read. One `/gs` per chat, ever. A configured username was the alternative and is
worse: the official bot has many forks and regional instances, and a chat following the
wrong one would have its live game reset by a stranger.

**The closing message is checked before the roster, and that ordering is load-bearing.** It
carries a player-list header of its own — `Players Alive: 3 / 12` over every player, the
*dead ones mentioned too* — so read as a roster it would raise the dead in the last thing
anybody sees. `_read_roster`'s count guard refuses it as well; the ordering is what stops it
getting that far.

**`game_bot_message` is a shield as much as a feature, and it is why enabling any of this is
safe.** Every message every bot in the room posts now arrives, and this module answers
several of the real manager's command words *bare* once `/gm` is on — so a bot posting `/gs`
or `/gm off`, for its own reasons or by echoing somebody, would be issuing them to us. One
handler in group **-1** sees all bot traffic and stops the update, whatever happens to it.
The stop is unconditional and the failure log reads nothing off the update, because PTB
dispatches the next handler group when an error handler does not claim one: an exception in
there — including one raised while reporting an exception — would leak a bot's message into
exactly the handlers this exists to shield. Nothing else in this bot has ever seen a bot
speak, and nothing else should start.

Loop prevention is a documented requirement of bot-to-bot communication, not a nicety, since
two bots answering each other in a group has no natural end. Three things bound it: only the
learned bot is read, a message id is acted on once (which also makes an *edit* of a message
already followed a no-op), and opening a session — the one path that costs an API call per
player — has a sixty-second floor under it. Following a roster deliberately has none: those
arrive every phase, and the publish debounce already coalesces the edits they cause.

**The roster message is pinned for the length of a game, if the bot can.** `_pin_state`
attempts it at `/gs` and does not check the permission first: a `getChatMember` answer is
a snapshot that can be stale by the time it is used, and the API's refusal is the
authoritative answer anyway — so a group that has not made the bot an admin gets no pin
and no complaint. Pinned silently, because the notification pings every member and an
active group starts a game every few minutes.

`_unpin_state` runs from `_finish`, which is the single place all three endings funnel
through (and from `/gm off`) (`/gsend`, the Stop button, the idle expiry). Two things it must keep doing:
unpin **by message id**, never the bare call — that removes the group's most recent pin,
which by the end of a game may be a rules post somebody else put there — and unpin only
what `pinned_message_id` records, which is the evidence *we* pinned it. Without that
record a session that could not pin would still try to unpin at the end and clear whatever
the group actually has.

**The lynch order has two forms and only one is stored.** `/lo`, `/slo` and `/rslo`
(plus the spelt-out `lynchorder`/`setlynchorder`/`resetlynchorder`) answer **only when
addressed** — `/lo@wwstatsbot`, never a bare `/lo`, because these are short words another
bot in the room may own. Being addressed also changes what silence means: unlike the
incumbent's command words, a chat with no session is *told* so rather than ignored.

`/slo` names players three ways — `@handle`, a tapped mention, or a bare user id — all
resolved against the roster by the same `_pointed_at` every other command in the module
uses. A named order is stored as a **list of ids**, so names and aliveness resolve at
render time: it follows a rename and drops a player who dies after it was set. Anything
that resolves to nobody is stored as free text instead, *except* a mention that failed to
match — `_pointed_at` cuts every mention out of the text whether or not it resolved, so a
mistyped `@handle` would otherwise look exactly like a bare `/slo` and silently reset the
order. That case is questioned instead.

The rotating order is computed from the **living** roster on demand — the first name
repeated at the bottom, so everybody lynches the name below them and each player receives
exactly one vote — so it follows deaths with nobody re-typing it, and a dead player is
never left in for two players to be pointed at. A typed order is stored verbatim in the
session and wins until cleared; `/slo` with neither argument nor reply *is* the reset,
since "set it to nothing" and "go back to rotating" are the same instruction. It is
session-scoped on purpose (the rotating order is a fact about this roster, so an override
of it means nothing next game), capped at `_LYNCH_ORDER_MAX` where it is set rather than
where Telegram would refuse it, and read with `.get()` — sessions predating the field are
still in Redis. Output mimics the incumbent's exactly: "Lynchorder:" over one mention
per line and nothing else, "The lynchorder was set/reset by <name>" as a single line with
no order appended. There is deliberately **no marker** distinguishing a set order from the
rotating one, because the incumbent has none — decoration meant to be helpful still reads
as a different tool. The one addition is a note naming a dead player dropped at set time,
which is a wrong answer avoided rather than decoration.

**Commands overload themselves based on the reply target.** `/sch` routes to the
multi-player `display_search_all` when it replies to a bot message that mentions players;
a bare `/info` replying to a bot routes to `all_info_cmd`. `/schall` and `/allinfo` still
work but are deliberately absent from `PUBLIC_COMMANDS` — don't re-advertise them.

**`/schall` has a second mode, and `/sch` deliberately does not.** A reply-based run caches
the chat's `text_mention` user ids in `chat_data`, and `/schall <achv>` with *no* reply
re-checks them for 60 minutes. `/sch` with no reply still means "check my own achievements":
it is the advertised command, so silently turning it into a group query would surprise
anyone asking about themselves. The cache is per-chat (one group's roster can never surface
in another), expires after an hour because a game roster changes every round, and the reply
always carries a 🕐 with the list's age — a remembered result must never pass for a fresh one.

**The /schall toggle belongs to whoever asked.** Only the requester (recorded as
`requested_by` in the payload) and admins may flip the view; anyone else gets an alert and the
message is left alone. Before that, whoever tapped last decided what everyone saw. The
requester check comes first so the common tap costs no `admins` lookup, and payloads stored
before the field existed stay open to everyone — with `REDIS_URL` set they survive a restart,
and locking the requester out of a live message would be the worse failure.

**HTML escaping is manual and single-pass.** Most output is `ParseMode.HTML` built by
string concatenation, so every interpolated name/description needs `html.escape()`.
Stored state (e.g. `/schall` player names) is kept **unescaped** and escaped only at
render time, so a persistence round-trip can't double-escape. `/achievements` is the one
Markdown path.

**Notes are two sub-fields in one TEXT column**, delimited by leading marker emoji
(📝 memo, 🎲 prob). `parse_notes`/`serialize_notes` are the only encoders; always
round-trip through them so legacy plain notes and `/db`-edited rows normalise.

**Only user-id mentions are checkable.** `_mentioned_users` can use `TEXT_MENTION`
entities (they carry a `User`); plain `@username` mentions have no id, so the
id-keyed stats API can't be queried and they are reported as unresolved rather than
dropped. Player names may themselves start with `-`, which is why `_ACHV_ROW` requires
a dash *plus* whitespace and prefers indented rows.

**Permissions are two-tier.** `is_superuser()` is an env-var id comparison
(`/addadmin`, `/deladmin`, `/admins`, `/db`); `is_admin_user()` also consults the
`admins` table (`/setnote`, `/clearnote`). `db.run_sql` executes arbitrary SQL and is
safe *only* because of its superuser gate — never call it from a new handler without one.

## Conventions

- Logging is structured: `logger.info("snake_case_event", key=value)` — never a
  pre-formatted message. Command handlers log `logger.info("command", command=..., user_id=..., user=unidecode(name))`.
  Tracebacks go through `format_exc_info` deliberately (not `dict_tracebacks`, which
  would dump `BOT_TOKEN`/`DATABASE_URL` from frame locals).
- Comments in this codebase explain *why* — the failure that motivated the code, not what
  the line does. Match that when editing; several comments record real bugs and should
  survive refactors.
- No type annotations beyond PTB handler signatures. No f-strings in most of the older
  code (`.format()` throughout) — stay consistent with the surrounding file.
- Concurrent API fan-out uses `asyncio.gather(..., return_exceptions=True)` so one failed
  player lookup degrades to "couldn't check" rather than failing the command.
- Conventional commits — see **Workflow** below for the prefixes in use.
- Ruff config selects `E`/`F`/`W`/`B`/`I` but **deliberately not `UP`** — pyupgrade would
  rewrite this codebase's consistent `.format()` style into f-strings. `E501` is off
  (111 lines already exceed 100 chars; the longest is 348).
- History contains one whole-repo `ruff format` commit. Run
  `git config blame.ignoreRevsFile .git-blame-ignore-revs` once so `git blame` reads
  through it.
