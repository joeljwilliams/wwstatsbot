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
uv run python -m wwstatsbot       # env vars override config.py values

LOG_FORMAT=console LOG_LEVEL=DEBUG uv run python -m wwstatsbot   # human-readable logs (auto on a TTY)

# Translations (Babel is a dev-only tool; the runtime uses stdlib gettext). The catalogs
# live inside the package, beside the i18n.py that resolves them.
uv run pybabel extract -F babel.cfg -o wwstatsbot/locales/messages.pot .   # after editing templates.py
uv run pybabel update -i wwstatsbot/locales/messages.pot -d wwstatsbot/locales
uv run pybabel compile -d wwstatsbot/locales                     # .po -> .mo (not committed)

# Test / lint
uv run pytest                     # 1619 tests; the 74 Postgres ones skip by default
uv run pytest tests/test_notes.py::test_roundtrip_is_stable   # a single test
uv run ruff check . && uv run ruff format --check .

# Railway infrastructure (see .railway/README.md). `uv run` matters: the CLI shells out
# to whatever python is on PATH, which is not the venv holding railway-sdk.
railway status                    # which environment is linked — check before every apply
uv run railway config plan        # read-only diff against the linked environment
uv run railway config apply       # plan again, then apply after confirming

# Data-layer tests need a real Postgres. CI uses postgres:18 (matching Railway) because
# what's pinned is server-side text-search behaviour, which is version-sensitive.
docker run -d --rm --name pgtest -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest

# Container (this is the Railway deploy path — .railway/railway.py selects this Dockerfile)
docker build -t wwstatsbot . && docker run -e BOT_TOKEN=... -e DATABASE_URL=... wwstatsbot

# Health probes (HEALTH_PORT, default 8080)
curl localhost:8080/healthz   # liveness — 200 while the process lives
curl localhost:8080/readyz    # readiness — 503 until DB init + set_my_commands finish

# Webhook mode. Needs the URL to be reachable from Telegram, so locally that means a
# tunnel; the path is served on HEALTH_PORT next to the probes.
WEBHOOK_URL=https://bot.example.com uv run python -m wwstatsbot
```

A running instance can be inspected live: `/version` reports the release version, branch
and short commit, and
`/db <sql>` (superuser only) is a raw SQL console against production Postgres.

## Testing

`tests/conftest.py` stubs `BOT_TOKEN`/`DATABASE_URL` **at module scope, above the first
`wwstatsbot` import** — pytest loads conftest first, and env beats `config.py`, which is
what stops the suite picking up a developer's real token. Handlers are driven with hand-rolled
`SimpleNamespace`-style fakes (`FakeMessage`, `FakeContext`, …) that record
`reply_text`/`answer` calls; the stats API is an `httpx.MockTransport`, so nothing
touches the network.

**`tests/test_render_golden.py` is the load-bearing file.** It asserts whole-string
equality on every rendered message. It was written to guard the split of `main.py` into
`handlers/` — almost pure code motion over HTML built by concatenation with manual
`html.escape()`, where a golden failure meant the move had changed user-visible output —
and it goes on earning its place now that the split is done, because the same is true of
every refactor since. Only edit an expectation when the change to what users see is
intentional — and keep that edit in its own commit, never mixed with a refactor, since the
golden diff is the review artifact showing precisely which bytes users will see
differently.

Other things the suite is deliberately guarding, all of which a refactor could silently
break: `test_templates.py` cross-checks every `t.NAME` reference against `templates.py`
in both directions — `rglob`-ing the whole package, so a new sub-package is covered the
day it appears rather than silently unscanned (drift here fails at runtime, in a handler, in production);
`test_routing.py` pins the self-overloading commands; `test_permissions.py` asserts
gated functions are *never reached* unauthorised, not merely that a refusal is printed;
`test_db.py` pins the FTS stemming contract.

The stand-in manager has a suite per concern, and which file a behaviour belongs in is not
obvious from its name: `test_standin_session.py` (the commands, and the **silence** around
them — most assertions are that nothing was said), `test_standin_list.py` (the Possible
Achievements post and the publish debounce), `test_standin_transforms.py` (deaths, /ad and
the role changes a death sets off), `test_standin_auto.py` (everything read off the game
bot's own messages under `/gm auto`), `test_standin_flood.py` (RetryAfter, and not sending
an edit that would change nothing), `test_standin_replies.py`, `test_standin_full_list.py`,
`test_standin_pin.py` and `test_lynch_order.py`.

`REQUIRE_POSTGRES=1` turns a missing database from a skip into a failure — CI sets it so
a broken service container can't leave the data layer silently unexercised.

Coverage is reported, never gated.

## Configuration

Every setting lives in **`runtime/settings.py`**, read from the environment with a
`config.py` fallback for development — **env wins over `config.py`**. Required: `BOT_TOKEN`,
`DATABASE_URL`; `settings.require()` fails fast from `main()` rather than at import, so the
module stays safe to import in a test process that has neither. Optional: `SUPERUSER_ID`,
`LOG_GROUP_ID`, `REDIS_URL`, `HEALTH_PORT`, `LOG_LEVEL`, `LOG_FORMAT`, `GITHUB_REPO`,
`WEBHOOK_URL`, `WEBHOOK_PATH`, `WEBHOOK_SECRET`.

Reach them **through the module** — `settings.SUPERUSER_ID`, never
`from settings import SUPERUSER_ID`. A `from` import copies the value at import time, which
defeats the monkeypatching every permission test depends on and lets two modules disagree
about the same setting.

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

Deployed on Railway from `.railway/railway.py` (Dockerfile builder, healthcheck
`/healthz`); `k8s-deployment.example.yaml` is a reference manifest. Redis/Postgres are
wired in through env vars, not through committed manifests — see *Infrastructure* below.

### Infrastructure

`.railway/railway.py` is Railway [Infrastructure as Code][iac] and describes the whole
project — the bot, Postgres, Redis and both volumes, across **both** environments,
switched on `ctx.is_environment("production")`. It replaced `railway.json`, which was
Config as Code: per-service, deprecated, and read for the last time on 2026-12-01. Plan
and apply are manual (`uv run railway config plan` / `apply`); a PR touching `.railway/`
also gets a plan for both environments in its job summary, from a `RAILWAY_TOKEN` held as
a GitHub *environment* secret per environment. `.railway/README.md` is the full
working guide — the three things most likely to bite are:

- **The Dockerfile builder lives there now, and only there.** Railway's own setting for
  this service is `RAILPACK`; the image was only ever built from the `Dockerfile` because
  `railway.json` overrode it at deploy time. `build.builder` in `railway.py` is what
  replaces the override — delete it and production quietly builds with Railpack, which
  knows nothing about `/opt/venv`, the non-root user, or `handlers/`.
- **Omission is deletion, for resources and for fields.** A service the file does not name
  is planned for destruction; a field it does not set is planned to null. The Railway
  dashboard has correspondingly stopped being a place to change things — an edit made
  there survives only until the next apply.
- **A field set to Railway's own default normalises back to null**, so it never converges
  and every later `plan` re-proposes it. `sleepApplication: False` and the `ON_FAILURE`/10
  restart policy `railway.json` spelled out are both this, and both are now simply omitted.
  A clean `plan` straight after an `apply` is what catches it.
- **Serverless works only while the bot stays quiet.** `sleepApplication` is on for the
  bot in both environments and for the data layer in development only. Railway sleeps a
  container after ~5 minutes with no *outbound* traffic, so anything sent on a timer keeps
  it awake for ever — see *An idle bot must say nothing* below.

[iac]: https://docs.railway.com/infrastructure-as-code

## Architecture

One package, `wwstatsbot/`, grouped by concern — no ORM, no framework beyond PTB, one file
per concern inside each group. The repo root holds project furniture only (`pyproject.toml`,
`Dockerfile`, the gitignored dev-only `config.py`); the application is not importable from
there except through the package.

```
wwstatsbot/
  main.py version.py i18n.py locales/   the wiring, the release metadata, the catalogs
  data/      api.py db.py playerdata.py notes.py achvlist.py rulelist.py
  game/      session.py roles.py feasibility.py
  render/    templates.py builders.py badges.py wwstats.py
  runtime/   settings.py health.py webhook.py logging_config.py redis_persistence.py
  handlers/  one module per command family
```

It is run as `python -m wwstatsbot`; `__main__.py` is four lines over `main.main()`, and
`main.py` keeps its name because everything — this file, the test suite, the registration
table — refers to it by that name.

**Every `__init__.py` is docstring-only, and must stay that way.** The release job in CI
reads the version with `python3 -c 'import wwstatsbot.version'` on a runner that has
installed nothing, so an import of `telegram` or `asyncpg` from any package `__init__`
fails at exactly the moment a release is being cut.

**Imports are absolute and bind the module**, never a name out of it:
`from wwstatsbot.data import db`, and `from wwstatsbot.render import templates as t`. This
is the rule `settings.py` has always had, generalised — see *Configuration* for why a
`from` import of a *value* both defeats monkeypatching and lets two modules disagree.

- **`main.py`** (~290 lines) — the wiring, and now almost nothing else: `PUBLIC_COMMANDS`,
  the handler table, `_post_init`/`_post_shutdown`, and the polling-or-webhook lifecycle. It
  once held the whole bot. What is left is the one place to look up *which* handler answers
  a command word, and the one place a new one is registered — including the two ordering
  decisions that matter, `_drop_edited_messages` in group `-2` and `game_bot_message` in
  group `-1`.
- **`runtime/settings.py`** — every setting, resolved once (see *Configuration*).
- **`handlers/`** — one module per command family, and where new user-facing behaviour
  lands: `stats.py` (/stats, /kills, /killedby, /deaths), `search.py` (/search, /sch,
  /schall), `achievements.py` (/achievements, /info, /getachv, /roll and the card pager),
  `gamesession.py` (the stand-in game manager — the largest by far, see below), `admin.py`
  (the privileged commands), `welcome.py`, `inline.py`, `misc.py` (/start, /about,
  /version), `errors.py` (the global error handler) and `common.py` (helpers shared by more
  than one family, including the permission predicates and the remembered player list).
- **`render/builders.py`** — the stat messages themselves, apart from the handlers because a
  slash command and an inline card render the *same* bytes. Escaping happens here, once;
  callers pass raw values.
- **`data/db.py`** — asyncpg pool + raw SQL. Owns the schema (`achievements`,
  `achievement_rules`, `admins`, `player_alts`, `player_badges`, `player_snapshots`),
  idempotent seeding, full-text search, and the **in-memory caches** that are the read path
  for achievements, rules, alts and badges.
- **`render/badges.py`** — the supporter badge: one emoji a contributor's name carries
  wherever this bot prints it. Rendering only; the cache and the table are `db.py`'s.
- **`data/playerdata.py`** — the only caller of `api.py`'s fetchers. Records every lookup,
  notices new achievements by diffing against the previous one, and answers from the record
  when the stats site is down.
- **`data/api.py`** — the tgwerewolf.com client: read-only, unauthenticated, keyed by
  Telegram user id. Nothing outside `playerdata.py` may call its fetchers, and a test
  asserts it.
- **`game/session.py`** — the stand-in game session as pure state: the roster, what everyone
  revealed, who is alive. Touches no Telegram and renders nothing, which is what makes the
  rules of a game testable without a bot. Lives in `chat_data`, so every value must survive
  a JSON round-trip.
- **`game/roles.py`** — the game's role registry: teams, tags, emoji, and every spelling a
  player might type. The bot's own vocabulary for roles, which arrive from the stats API as
  free text.
- **`game/feasibility.py`** + **`data/rulelist.py`** — which achievements a revealed
  composition can still produce, and for whom. `rulelist.py` is the seed source for
  `achievement_rules` exactly as `achvlist.py` is for `achievements`: a fresh database is
  populated from it, and a running bot reads the table. Both seed lists live in `data/`
  beside the module that seeds from them, so the data layer never points at the game layer.
- **`render/templates.py`** — every user-visible string, as `str.format` templates grouped by
  parse mode. Handler code must not contain new prose; add a template. `N_()` marks each one
  for extraction, and `i18n.py` resolves the catalog at render time (stdlib `gettext`;
  Babel is a dev-only tool). `i18n.py` and `locales/` sit together at the package root
  because `LOCALE_DIR` is resolved from `i18n.py`'s own `__file__` — moving one without the
  other silently loses every catalog.
- **`render/wwstats.py`** — the `/achievements` Markdown report (attained / missing /
  not-via-playing / inactive), chunked 30 items per message. Takes the attained list; it
  does not fetch.
- **`data/achvlist.py`** — the original hardcoded `ACHV` list, now only a **seed source** for
  the database. Editing it will not change a deployed bot's data (seeding is
  `ON CONFLICT DO NOTHING`); edit rows via `/setnote` or `/db` instead.
- **`data/notes.py`** — the one encoder for the two sub-fields an achievement's notes column
  holds (memo, probability), delimited by marker emoji.
- **`runtime/redis_persistence.py`** — durable `DictPersistence` subclass for PTB (whole
  state blob under one Redis key).
- **`runtime/health.py`**, **`runtime/logging_config.py`**, **`version.py`** — stdlib health
  server on a daemon thread (and, in webhook mode, the update POST route);
  structlog-over-stdlib setup; release version plus git/Railway commit resolution for
  `/version`. `version.py` is at the package root rather than in `runtime/` because CI
  imports it with nothing installed, so the shorter the chain of `__init__` files it drags
  in, the fewer places can break a release.
- **`runtime/webhook.py`** — webhook intake: authenticate the request, parse an `Update`, put
  it on PTB's queue. Deliberately knows nothing about HTTP serving, and `health.py`
  deliberately knows nothing about Telegram — they meet at a callable returning a status
  code.

**`handlers/gamesession.py` is the biggest module in the repo** (~2750 lines) and most of
*Things that will bite you* below is about it. It is the stand-in achievement manager: it
runs a game's roster when the real manager is offline, keeps two live messages up to date,
reads the game bot's own posts under `/gm auto`, and owns the lynch order. Nothing in it
speaks unless the chat has a session.

### Releasing

The version numbers carry the project's history: **2.x** is the async rewrite this fork
carries (1.x was the original bot), and the **minor** counts feature releases since that
rewrite. So `2.22.0` is the 22nd feature release of the rewrite, not a fresh start.

**A version bump touches exactly two places, in one commit:**

| File | What |
|---|---|
| `wwstatsbot/version.py` | `VERSION = "X.Y.Z"` — the single source of truth, and the only one that exists at runtime |
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

**An idle bot must say nothing, or it never sleeps.** Railway's Serverless mode watches
*outbound* traffic and stops a container after ~5 minutes without any; the bot is asked to
sleep in both environments. So anything this process sends on a timer — a heartbeat, a
metrics push, a keepalive, a poll — keeps it awake for the life of the deploy, and there is
no symptom: an idle bot that chatters looks exactly like an idle bot that does not.

Two things did, and neither was visible until the container was expected to sleep and
didn't. PTB's persistence loop calls `update_bot_data` every `update_interval` (60s)
whether or not anything changed, and `RedisPersistence` turned each one into a Redis write
— so `_save()` now compares the serialized blob against the last one it successfully wrote
and skips an identical write (`DictPersistence` already drops an unchanged update; this is
the same idea one layer down, where the network is). And `db.init_pool()` left
`max_inactive_connection_lifetime` at asyncpg's default of 300s, which *is* the sleep
threshold, so the pool fell quiet exactly when the window would otherwise have closed and
the five minutes never started counting; it is 60s now, with `min_size=0`.

Skipping the write turned out to be only half of it, and the other half is not in this
process's control. Redis ships `tcp-keepalive 300` and `timeout 0`, so it probes an idle
client every 300 seconds and never hangs up first — and the bot's TCP stack answers every
probe. Railway's threshold is those same five minutes, so the two ends kept each other
awake over a connection neither was using. **An idle socket is not a silent one**, which is
why `RedisPersistence` now drops its connection after a minute unused, the same way the
asyncpg pool does. Postgres was already sleeping while Redis was not, and that contrast is
what identified it.

`tests/test_idle_quiet.py` and the write-policy tests in `tests/test_persistence.py` are
what stop this regressing. Note also that sleeping only makes sense in **webhook** mode: a
long-polling bot calls `getUpdates` for ever, and a slept one would have nothing inbound to
wake it.

**`_last_saved` advances only on a write that landed.** A failed Redis write leaves it
alone deliberately, so the next attempt retries rather than treating the blip as success —
otherwise a change that never reached Redis would be "unchanged" ever after, and skipped
for the life of the process.

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

**Edits reach no handler, and that is a gate rather than a filter.** Every handler here
begins by reading `update.message`, but PTB decides what to dispatch with
`update.effective_message` — which an edit populates while leaving `update.message` as None.
So editing a message into a command dispatched normally and then crashed on the first
attribute the handler touched. It shipped that way and was found in production, as an
`AttributeError` out of `doused_forward`; that handler was merely the one hit, and all
twenty-eight command words had the same crash behind them. `_drop_edited_messages` in
`main.py` stops them in group `-2`, ahead of everything, because it is a precondition every
handler shares rather than any one handler's business.

It matches the **edit fields by name**, not `update.message is None`. Those look equivalent
and are not: a callback query has no `message` either, and its `effective_message` is the
message the button sits on — so the shorter reading silently swallows every button in the
bot. `tests/test_edited_messages.py` pins that, and it is the reason the test file exists.

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
is English, like `_ROSTER_COUNTS`, `_DEAD_ROW`, `_DOUSED_LINE`, `_DAY_BREAKS` and
`_NIGHT_FALLS` beside it; a group playing in another language keeps typing `/gs` — and gets
neither the nudge below nor the nightly lynch-order reset.

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

`_unpin_state` runs from `_finish`, which is the single place every ending funnels
through (and from `/gm off`) — `/gsend`, the Stop button, the idle expiry and the game
bot's own closing message. Two things it must keep doing:
unpin **by message id**, never the bare call — that removes the group's most recent pin,
which by the end of a game may be a rules post somebody else put there — and unpin only
what `pinned_message_id` records, which is the evidence *we* pinned it. Without that
record a session that could not pin would still try to unpin at the end and clear whatever
the group actually has.

**An ending is offered, then undoable.** Three things stand between a live game and a
session that vanished while nobody was looking, and each exists because the one before it
was not enough.

The idle timer warns after ten minutes of silence and ends the session after a **five
minute** grace, not two. The warning lands in a chat that has by definition said nothing
for ten minutes — nobody is watching it — and two minutes was short enough for a night
phase, an argument or a slow lynch to use up, so the first anybody knew was a roster
reading GAME ENDED. The warning also carries **two buttons**, Keep playing and End it,
because the only answer it previously offered was to remember to type a command inside the
window, and a table quiet enough to be warned is a table typing nothing. Neither button
arms the way the roster's Stop does: they arrive on a message that has just asked this
exact question, where Stop sits under sixteen thumbs for a whole game.

Every ending then keeps the session for **ten minutes** and the roster carries **Restart**
where it carried Stop. `_finish` is where that happens, because it is the single funnel
all four endings pass through — `/gsend`, the Stop button, the idle expiry, and the game
bot's own closing message — and the alternative to picking a game back up is every player
re-sending a `/role` the bot already had. The archive lives under its own `chat_data` key
(`session.ARCHIVE_KEY`), never under the live one: every command in the module gates on
`session.get()`, and a dict still readable there — however it was marked — is one missed
check away from a dead game accepting reveals.

Three details inside that are load-bearing. The window is checked by **age as well as by
its job**, because `chat_data` is persisted to Redis and PTB's JobQueue is not — a deploy
inside the window would otherwise leave an archive nothing was going to clear and a
Restart button that worked days later. Opening any session **clears the previous
archive**, inside `_open_session` so that `/gm auto` clears it too, since a new roster is
the clearest statement that the chat has moved on. And a restart **cancels the expiry
job**: left running it would fire inside the live game it just restored and edit the
roster back to GAME ENDED. The pin is deliberately *not* held across the window — a game
that may be over must not go on holding the chat's pin on the chance that it is not — so a
restart re-pins.

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

**Night falling clears a typed order**, under `/gm auto`. It is the one thing in the session
that is about a single *day* rather than about the game — "who do we point at today" — and
read again the next morning it names players who died overnight and a plan the village has
already carried out. `_NIGHT_FALLS` matches the line the game bot opens that message with,
which is the same after a lynch, after a tie and after a Pacifist talks the village out of
one; the rotating order it falls back to is computed on demand, so the next morning has an
order with nobody retyping anything. It is announced **only when there was something to
clear**, which most games never have: a line saying nothing happened, every night, is the
noise the rest of this module exists to avoid — but a chat that did set one is owed the one
line, in the place where somebody would otherwise go looking for their order.

**Commands overload themselves based on the reply target.** `/sch` routes to the
multi-player `display_search_all` when it replies to a bot message that mentions players;
a bare `/info` replying to a bot routes to `all_info_cmd`. `/schall` and `/allinfo` still
work but are deliberately absent from `PUBLIC_COMMANDS` — don't re-advertise them.

**`/schall` has a second mode, and `/sch` deliberately does not.** `/schall <achv>` with
*no* reply re-checks this chat's remembered player list for 60 minutes. `/sch` with no reply
still means "check my own achievements": it is the advertised command, so silently turning
it into a group query would surprise anyone asking about themselves. The list is per-chat
(one group's roster can never surface in another), expires after an hour because a game
roster changes every round, and the reply
always carries a 🕐 with the list's age — a remembered result must never pass for a fresh one.

**Two things fill that list, and it lives in `handlers/common.py` because of it.** A
reply-based run records the `text_mention` user ids it saw; a stand-in session records its
**whole table**, so a group whose games this bot runs never primes it at all — the first
`/schall` of a round needs no reply, because the session opened itself from the game bot's
player list already (see `/gm auto`). `handlers.common.remember_players`/`recall_players`
are the only encoders, and they take `now` from the caller so each command family keeps its
own `_now` wrapper and no two modules have to agree about the time.

The table rather than the game bot's latest list, and dead players included, for two
reasons that are easy to get wrong. Achievements do not die with the player, and a list
that shrank every round would look exactly like a mention having gone missing — the failure
`/schall` already reports dropped alts to avoid. And the game bot *stops linking a player
once they are out*, so its later lists carry ids for only the living, where the session
keeps everybody. The record is refreshed on every list followed rather than only at the
start, so the age reads "when this line-up was last confirmed": during a live game that is
seconds, however long ago the game began. The `chat_data` key is still `schall_players`,
because renaming it would orphan every list already in Redis.

**The /schall toggle belongs to whoever asked.** Only the requester (recorded as
`requested_by` in the payload) and admins may flip the view; anyone else gets an alert and the
message is left alone. Before that, whoever tapped last decided what everyone saw. The
requester check comes first so the common tap costs no `admins` lookup, and payloads stored
before the field existed stay open to everyone — with `REDIS_URL` set they survive a restart,
and locking the requester out of a live message would be the worse failure.

**The Possible Achievements post is a view, and `list_contents()` is what it is a view
of.** A twenty-four player game has around nine thousand characters of content and four
thousand to put it in, so the post is trimmed almost every game — which made `/info` and
`/roll` wrong, because both read the text of the message they reply to. A roll drew from
the three rows that survived with nothing on screen to say the other six existed.
`gamesession.reply_contents()` answers from the session when the replied-to message is
this chat's own list (matched on `list_message_id`), in exactly the shapes
`_extract_by_player` returns, and `handlers/achievements.py::_post_contents` prefers it.
Anything else — the real manager's post, a forward — is still parsed. The two readings are
compared against each other on an untrimmed post in `tests/test_standin_replies.py`, which
is the only thing stopping them drifting.

Two rules inside the trimming itself. Rows are capped before the certain-only pass,
down to **one row each**, because that pass is not a smaller list but a different one: a
player whose achievements are all uncertain vanishes from a post that is about everybody,
and dropping players is the one outcome nobody can work around. And the **group sections
are capped by the same number** — they name every living player and were once the only
uncapped thing in the renderer, a quarter of a big post and the one part that could not
give any of it back. Their heading still counts everyone eligible, because that answers a
different question from the names under it.

Lengths are measured with `_visible_len`, never `len`: every name in the post is a
`tg://` mention, so a full game is ~8000 raw characters of which under 3200 are ever
displayed, and Telegram's 4096 is against what a client shows.

**A live message is not edited into what it already says.** Every write schedules a
publish and a publish re-renders *both* live messages, so a `/love` between players
already in love, a re-sent roster that moved nothing, a second `/dead` for somebody
already dead each spent two API calls asking Telegram to replace a message with itself.
`_edit_live_message` fingerprints the rendering (text *and* keyboard — the list's button
appears and disappears with the trimming) and skips an identical one; a sixteen-player
game with a round of no-op re-declares went from 63 edits to 31. It is the same rule
`RedisPersistence._save` follows one layer down, including the half that matters: **the
fingerprint advances only on an edit that landed**, so a failed one is retried rather than
remembered as done. The exception is "message is not modified" — that is Telegram
confirming the message already looks like this, which is worth recording.

**A first reveal is answered with silence; what is news is not.** A thirty-five player
game opens with thirty-five people typing `/role` inside a minute, and reading each one
back buries the reveals underneath their own confirmations — in the one stretch of chat
they are needed in. So `/role` records a player's own first reveal and says nothing: the
roster message is already about to say the same thing to the whole table. Only an
unrecognised role is refused, because nothing was recorded and there is no roster row to
read instead.

Three things are still said out loud, and `tests/test_standin_session.py` is what keeps
them said: a role that **changed** (the Thief steals, the Cursed turns), a role set **for
somebody else** (a claim the table has to see, not something its subject quietly accepts),
and a claim the **Beholder has already settled** — `session.set_roles` collapses a `sf`
pair to one role when the Beholder has answered, so going quiet there would leave a player
believing they are the Seer. Re-typing an identical role counts as a change deliberately:
somebody who saw no answer and typed it again is asking whether it landed, and with the
first reveal silent that is the only way left to ask.

Those still arrive in bursts — a night that turns two players at once, three roles recorded
after a wave of deaths — so the first is answered at once, quoted on the message that made
the claim, and anything inside the next `_ROLE_BURST_SECONDS` is buffered in the session and
read back in one notice. The window is the publish debounce's, so the notice and the live
list it describes land together. Three details are load-bearing: the buffer is keyed by
**player**, so somebody correcting themselves twice inside one window is named once; each
row is rendered from the session **when the notice fires**, never from what was typed; and a
bot built with no job queue answers everything, because nothing would flush the buffer and
every confirmation after the first would vanish. Flood control puts the whole burst back and
retries past the window, for the same reason.

**What the list cannot answer for is marked, then named once.** Silence on a first reveal
(above) costs something: nobody is told their `/role` landed, and a player who never sent
one is invisible until somebody counts the roster. So the roster row carries a ❗ rather
than only the italic "not revealed" it had — that row is the one thing in the message
somebody has to act on — and the **first death** in the session names the gaps out loud,
with mentions.

**Two gaps, one message.** A player with no role at all, and a **Wild Child or Doppelgänger
with no role model** — the quieter one, because the role is right there on the list while
the transform their whole game turns on can never fire, leaving them listed as a Wild Child
to the end and offered the wrong achievements the entire way. The roster has nothing to say
about that one at all. One send rather than two: the point is to stop the chat scrolling,
not to add to it.

**The moment is the end of the first night, and the game bot is what announces it.**
`_DAY_BREAKS` matches a line that is nothing but `Day 3` in what the game bot posts; the
flavour above it is what a reader notices and is the wrong thing to match, since it says
whether anybody died, differs again for a murderer or a harlot, and is translated in every
variant. The day number is structural and identical in all of them. Any day rather than
strictly Day 1, because the nudge fires once per session anyway — so it reads as "the first
night that ended while we were watching", which is Day 1 in an ordinary game and still the
right moment in a session that opened halfway through one.

A **death was the first attempt at that moment and is wrong**: a night can end with nobody
killed, and the first death that does happen may be a day-one lynch, hours later or never.
Reading the day announcement means the nudge only exists under **`/gm auto`** — it is the
only mode that reads the game bot at all — so a session run by hand never sends one, and the
roster's ❗ is all it has.

Three rules inside it: the **dead are left out** of both lists, since the game bot's death
rows name their role already and the one person a nudge cannot help is the one who is out;
it is said **once**, because a second telling is nagging and the ❗ stays on the roster for
anyone who looks; and the flag is set **whether or not anybody was missing**, so a table
with no gaps spends the moment there rather than banking it for a later morning.

**An unchanged lynch order is not sent twice in five seconds.** `/lo` is thirty-five lines
in a thirty-five player game, and several people ask for it within seconds of each other;
the third copy has scrolled the game itself out of the chat. The rendering is fingerprinted
and a repeat inside `_LYNCH_REPEAT_SECONDS` is dropped silently — whoever asked is looking
at the answer. Fingerprinted rather than timed alone because a death or a `/slo` between
the two asks makes the second a *different* answer, which is worth the room; and recorded
**before** the send rather than after, because the duplicates this is about arrive while
that send is still in flight.

**Flood control postpones the publish; it does not lose it.** A `RetryAfter` used to leave
the loop entirely — both live messages stayed at their last successful edit until somebody
happened to reveal a role, and the exception reached the error handler as if the bot had
crashed. `_postpone_publish` now *replaces* whatever publish was pending with one a second
past the window Telegram named (a pending one five seconds out would land inside the same
window), and the publish stops after the first refusal rather than spending a second call
on the message that would be refused next. `_retry_seconds` reads `retry_after` as either
a number or a `timedelta`, because PTB warns it changes type in a future major version and
flood handling is the worst place to meet a new `TypeError`.

This is the one part of the bot that goes near Telegram's per-chat limit: twelve publishes
a minute at two calls each, plus a reply to every command, against a documented soft limit
of about twenty messages a minute to one group. PTB's `AIORateLimiter` is the preventive
answer and is deliberately **not** enabled — its default group rate is 20/60s, which would
queue replies behind edits during exactly the busy game this is about, trading an
unobserved failure for a certain delay. Revisit it if `standin_*_failed` ever shows
`retry_after` in production; that log line exists to make the question answerable.

**The full list is paged in PM, and deliberately does not page the post.** The post is one
shared message that a table of sixteen is watching and that re-renders every few seconds,
so a page number on it would belong to whoever pressed a button last — the same failure
the `/schall` toggle above had. The button appears only when a rendering left something
out, and opens the whole list as a private pager for whoever tapped. Its Prev/Next land in
that private chat, where `context.chat_data` is the conversation's and the game's is
somewhere else entirely, which is why the group's chat id rides in the callback data and
the handler reaches for `context.application.chat_data`. Pages are rendered from the
session on every tap rather than from a copy, so a page turned after a death shows the
table as it is now — and a session that has ended is the one thing a page cannot turn to.

**Every stats lookup goes through `playerdata.py`, and nothing else may call `api.get_*`.**
Three behaviours hang off that single door, and a handler fetching for itself would opt out
of all three while looking perfectly correct — so
`test_nothing_outside_playerdata_calls_a_fetcher_directly` checks the rule rather than
trusting it. Each lookup is written to `player_snapshots` (one row per player per endpoint,
JSONB), each achievements lookup is diffed against the row it replaced, and a failed fetch
is answered from that row instead of raising.

The fetchers therefore return a **`Reading(data, age)`**, never a bare payload. `age` is
None for a live answer and seconds for one out of the record, and callers must carry it to
the end of the message: `playerdata.stale_notice(*ages)` renders "" when everything was
live, so a working lookup is byte-identical to before, and a footer naming the age when it
was not. A message built from two endpoints (`/stats`, `/deaths`) reports the *older* of
them. This is the same rule `/schall`'s 🕐 already enforces on its remembered player list —
a record must never pass for a live answer — and it is the reason the return shape changed
everywhere rather than the age being dropped at the fetch.

**Only the profile endpoint knows a player's own name, and it raises for strangers.** The
five stat endpoints carry the names of *other* players — who you killed, who killed you —
and never the subject's, so `/stats <id>` had nothing to title a card with but the digits
typed and a log announcement had nothing to call a player by. `api.get_player()` reads
`/Stats/Player/{id}?json=true` (id in the **path**, not a `pid` parameter) for
`{id, telegramId, name, username, language}`. For an id the game has never seen the site
dereferences a null and serves an **HTML error page**, so `json()` raises rather than
returning the empty string the other endpoints answer with — and a mistyped number is the
ordinary case. `playerdata.player_profile()` therefore never raises: it answers an empty
`Profile`, and both callers fall back to the id. Recorded as its own `player` kind, so a
name learned once survives the site being down.

**The username is the only link either of those two places can use.** `tg://user?id=`
resolves only in a client that has already met that user — a log group reads about players
its members have not met, and a `/stats <id>` card is by definition about somebody the asker
could not have mentioned. So both link `https://t.me/<username>` when there is one
(`STATS_NAME_BY_USERNAME`, `NO_GAMES_BY_USERNAME`, `LOG_ACHIEVEMENT_HEADER_LINKED`) and fall
back to the id mention or to plain text when there is not — a player who never set a
username is ordinary, not a degraded case. The username is **validated against Telegram's
own rule** before it is used, because it goes straight into an `href` and arrives from a
database this bot does not own and that has never revalidated it; anything not matching is
treated as no username at all.

**The first lookup of a player is a baseline, not news.** `db.save_player_snapshot` returns
the payload it replaced, and `None` there means "never looked" — announcing a diff against
nothing would post a veteran's entire collection to the log group the first time anybody ran
`/stats` on them. The read and the write are one transaction with the row `FOR UPDATE`,
because two lookups of the same player overlap routinely (a `/schall` in one group while
`/stats` runs in another) and both would otherwise diff against the same previous row and
announce the same achievement twice.

**An empty achievements list is refused when a full one is recorded.** Achievements are
never revoked, so `[]` over a stored collection is the API answering badly — a 200 whose
body decoded, which is exactly the failure the try/except cannot see. Recording it would
erase the record *and* then report the whole collection as new the moment the site
recovered. An empty list for a player who has no row is recorded normally, which is what
makes a genuinely-first achievement announceable. A `None` payload (the API's answer for
somebody who has never played) is not recorded at all: a stored JSON null and "we have never
looked" would read the same to anything that reads the row back.

**The log announcement names a player by asking the site, not by what the caller held.**
Most lookups reach a fetcher with no name at all (see below), which left the great majority
of announcements reading as a bare user id. `_who` spends one extra request on
`player_profile()` — and only on an announcement, which happens when somebody actually earns
something rather than on every lookup — falling back to the caller's name and then the id.
It returns the template *and* its fields rather than a finished line, because the header also
carries `{count}`/`{plural}` and `str.format` cannot fill some fields and leave others.
It cannot recurse: the profile lookup is kind `player`, and only kind `achievements` ever
announces.

**A name is only recorded by the three callers that hold an unescaped one** — `/schall`'s
roster, the join announcement, and the session's batch. Everything reached through
`builders.py` has already been `html.escape()`-ed by its caller, so it passes no name rather
than storing markup that the announcement would escape a second time. `save_player_snapshot`
never blanks a stored name with an empty one, so a row learns a name once and keeps it; the
announcement falls back to the bare user id when none was ever learned.

**The fetchers are resolved by name with `getattr(api, ...)` at call time.** Holding the
function objects in `_FETCHERS` binds them at import, which is the same failure api.py's own
docstring records for `api.client`: the test suite's patches would be invisible, and the
suite would pass while patching something nothing calls.

**The announcement needs a bot, and gets one from `main._post_init`.** Detection happens
inside a lookup, under builders that have no `context` and no business sending anything, so
`playerdata.set_announcer(application.bot)` hands one over at startup. Nothing is posted
unless `LOG_GROUP_ID` is also set, and a refused send is logged and swallowed — the log
group is where problems are reported, so failing to reach it can only be logged.

**A badge is attached at nine call sites, and there is no choke point.** `/setemoji`
(superuser) gives a contributor an emoji their name then carries everywhere this bot prints
it — but a name becomes a mention in nine different templates, so each carries a `{badge}`
field filled with `badges.of(user_id)`: `_mention` in the stand-in (the roster, the
achievements list, /dead, /love, the lynch order), the four stat builders, the /search
header and every /schall row, the /roll names, the join announcement and the log group's
achievement announcement. `badges.py` lists them, because a call site missed is one badge
absent from one message — nothing fails, and nobody reports it.

It reads through `db.badge()`, a dict lookup against a cache loaded at startup, for the same
reason `is_alt_account` does: a sixteen-player roster asks sixteen times per edit and edits
twice a phase. Every write reloads the cache. The emoji is escaped on the way out, because
it comes from a table this bot does not revalidate and goes straight into HTML, and an empty
badge renders every message byte-identically to the one before badges existed — which is
every message about everybody who has not been given one.

**The badge sits outside the `<a>`, and that is not cosmetic.** Telegram entities of these
kinds **cannot contain one another**: a custom emoji inside a `text_link` is not rendered as
one, it is silently dropped to the plain glyph the tag wraps. Rendered inside the mention, a
premium butterfly reached a live group as a star-struck face and a premium penguin as a
winking one — the fallback character each sticker happens to carry, the same in the roster
and the stats card, with no error anywhere and nothing to say why. Only the `/setemoji`
confirmation looked right, because that is the one message where the badge was never inside
a link. So every mention template ends its `<a>` at the **name** and puts `{badge}` after
the closing tag; on the stats card that also moved `the <role>` out of the link text, which
is the visible half of the fix and why the goldens changed with it.

**A premium emoji is two columns, and the confirmation is the test.** Telegram sends a
custom emoji as `<tg-emoji emoji-id="…">X</tg-emoji>` — an animated sticker addressed by id,
with `X` the plain emoji a client falls back to — and it arrives at `/setemoji` as *text
plus an entity*, so reading the command's text alone silently stores the fallback and loses
what somebody paid for. Only bots that bought a username on Fragment may send one at all,
and a badge Telegram refuses would not fail at `/setemoji`: it would fail in **every message
naming that player**, with nothing on screen to say why. So the badge is rendered into the
confirmation *before* the row is written, and what gets stored is whichever form Telegram
agreed to send — the plain one, with a note, when the custom one was refused.

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
(`/addadmin`, `/deladmin`, `/admins`, `/db`, `/setemoji`); `is_admin_user()` also consults the
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
- Imports inside the package are absolute and bind the *module*
  (`from wwstatsbot.data import db`), never a name out of it — see **Architecture**.
- Conventional commits — see **Workflow** below for the prefixes in use.
- Ruff config selects `E`/`F`/`W`/`B`/`I` but **deliberately not `UP`** — pyupgrade would
  rewrite this codebase's consistent `.format()` style into f-strings. `E501` is off
  (111 lines already exceed 100 chars; the longest is 348).
- History contains one whole-repo `ruff format` commit and the package move. Run
  `git config blame.ignoreRevsFile .git-blame-ignore-revs` once so `git blame` reads
  through both.
