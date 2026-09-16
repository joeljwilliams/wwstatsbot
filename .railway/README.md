# Railway configuration

`railway.py` is the whole of this project's Railway configuration — both environments,
all three services, both volumes. It replaced `railway.json`, which was
[Config as Code](https://docs.railway.com/config-as-code): per-service, deprecated, and
read for the last time on **2026-12-01**.

## Working with it

```bash
uv sync                                 # the CLI imports railway_sdk from the dev group
railway link                            # pick the project and environment to target
railway status                          # ← confirm which environment before every apply
uv run railway config plan              # read-only preview of the diff
uv run railway config apply             # plan again, then apply after confirming
```

`plan` changes nothing and is safe to run at any time. `apply` shows the same plan and
waits for a yes.

**Run the CLI through `uv run`.** `railway config` shells out to whatever `python` is on
PATH, which is not the project venv — a bare `railway config plan` reports "The Railway
Python SDK is not installed" even though `uv sync` installed it.

**The linked environment is the target, and nothing in this file names it.** `railway.py`
renders whichever environment the CLI is pointed at, so `railway status` is the only thing
standing between a development apply and a production one. Switch with
`railway environment production` / `railway environment development`.

Pull requests that touch this directory get a plan for **both** environments in the job
summary (`.github/workflows/railway-config.yml`). Applying is manual — see the comment at
the top of that file for why.

That workflow reads `RAILWAY_TOKEN` as a GitHub **environment** secret, one per Railway
environment, which is why each matrix leg declares `environment:`. A job that does not
declare one reads `secrets.RAILWAY_TOKEN` as empty and skips while looking perfectly
healthy — a green check that planned nothing. A Railway project token is scoped to one
environment as well, so the token a leg finds is also what decides which environment it
plans; nothing in the workflow names one.

## Things that will bite you

**Omission is deletion, for resources and for fields alike.** A service this file does not
name is planned for destruction; a field it does not set is planned to null. That is why
both environments live in one file switched on `ctx` rather than split in two, and why
`source.branch` and the Redis `startCommand` are written out in full instead of left to
the dashboard. It also means the dashboard has stopped being a place to change things: an
edit made there survives only until the next apply, which will silently revert it.

**`postgres()` and `redis()` ignore most of what you pass them.** The Python helpers take
`region`, `image`, `output` and `defaultMountPath` and drop every other keyword without an
error — and assigning `db.deploy = {...}` afterwards sets an attribute the graph builder
never reads, so both spellings fail silently and plan clean. `with_fields()` is the only
way in, and it *replaces* the field rather than merging, which is why the
`multiRegionConfig` that `region=` would have produced is spelled out by hand next to
`sleepApplication`.

**Never write a field's value if it equals Railway's default.** Railway normalises such
a field back to null on apply, so it does not read back as what you set — it reads back as
nothing, and the next `plan` proposes the identical change again. Applying never
converges, and a plan that always carries a line nobody should act on is how people learn
to skim plans. Two were found this way: `sleepApplication: False` (off is the default —
`_sleep()` omits it instead) and `restartPolicyType: "ON_FAILURE"` with 10 retries, which
is word for word [Railway's default][restart] and was only in `railway.json` because
someone wrote it out. **A clean `plan` right after an `apply` is the check for this** — run
it against both environments before calling a change done.

[restart]: https://docs.railway.com/deployments/restart-policy

**`github()` defaults the branch to `main`.** Not to "leave it alone" — a source block
that says nothing about the branch points that environment at production's. Both branches
are therefore named explicitly.

**Development's branch is a working knob, and this file pins it.** The workflow in
`CLAUDE.md` points the development environment at whichever feature branch is being
live-tested, so it will legitimately differ from `devel` while that test runs. Applying to
development then plans `source.branch ("fix/..." → "devel")` and yanks the environment off
the branch under test. The plan says so in as many words — read it before confirming, and
re-point the branch afterwards.

**The Dockerfile builder lives here now, and only here.** Railway's own setting for this
service is `RAILPACK`; the image was only ever built from the `Dockerfile` because
`railway.json` overrode that at deploy time. `build.builder` in `railway.py` is what
replaces the override, and production builds wrong without it — Railpack knows nothing
about the uv-built venv at `/opt/venv`, the non-root user, or the `wwstatsbot/` package.

**Serverless only works because the bot is quiet, and staying quiet is a code
property.** `sleepApplication` is on for the bot in both environments, and for Postgres and
Redis in development only. Railway sleeps a container after ~5 minutes with no *outbound*
traffic, so anything the bot says on a timer keeps it awake for ever. Two things did until
2.36.4 — PTB's persistence loop writing `bot_data` to Redis every 60 seconds whether or not
it changed, and the asyncpg pool holding a connection open — and both are now pinned by
tests (`tests/test_persistence.py`, `tests/test_idle_quiet.py`). Adding a heartbeat, a
metrics push or a keepalive would silently undo this: the bot would look perfectly healthy
and simply never sleep.

Production's data layer is deliberately excluded from that. A slept database answers the
first connection with a 502, and `db.init_pool()` builds the pool once at startup with no
retry — so a cold Postgres in production is a crash loop rather than one slow reply.

**Secrets are never written here.** Every variable renders as `preserve()`, meaning "keep
whatever Railway already has". `railway config pull --include-variables` would inline the
real values into this file; don't. `railway config plan --show-values` prints them to the
terminal; treat that output as sensitive.

**Python authoring is beta.** TypeScript (`railway.ts`) is the generally-available surface;
`railway_sdk` mirrors it and its helper names may still move. The blast radius is this
file — plan and apply live in the CLI, and this module only builds the graph they compare
against — but a CLI upgrade is a reason to re-run `plan` and read it.
