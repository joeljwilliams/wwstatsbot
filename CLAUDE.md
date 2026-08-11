# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`@wwstatsbot` — a Telegram bot that reads player stats and achievements from the
Werewolf-for-Telegram public stats API (`tgwerewolf.com`) and renders them in chat.
Long-lived fork of an older bot, rewritten for python-telegram-bot v22 (async).
Python 3.12 in the container; runs as a **long-polling** process (no webhook).

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
`REDIS_URL`, `HEALTH_PORT`, `LOG_LEVEL`, `LOG_FORMAT`, `GITHUB_REPO`.

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
  daemon thread; structlog-over-stdlib setup; release version plus git/Railway commit
  resolution for `/version`.

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

**Search has two layers.** `build_info_results()` tries Postgres FTS
(`search_achievements`) and falls back to a substring scan over the cache. The `search_tsv`
generated column and the query must use the *same* `'english'` config — a mismatch silently
breaks stemmed initialisms (see the comments in `db.py`). Callers treat `found[0]` as "the
answer", so the ORDER BY in `_SEARCH_SQL` is load-bearing.

**Callback state is token-keyed in `bot_data`.** `callback_data` is capped at 64 bytes, so
`/info` and `/sch` stash payloads in `context.bot_data[...]` under a `secrets.token_urlsafe(8)`
token and put only the token in the button. Both stores are bounded at 200 with
insertion-order eviction, so an expired token is a normal case every callback must handle
(`ALLINFO_EXPIRED` / `SCHALL_EXPIRED`). With `REDIS_URL` set these survive restarts — which
means payloads must stay **JSON-serializable** and tuples come back as lists.

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
