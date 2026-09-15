"""Railway Infrastructure as Code — the whole project, both environments.

Replaces the railway.json this repo carried until now. Config as Code was per-service,
is deprecated, and stops being read on 2026-12-01; this file is per-project, so the
databases and their volumes are described here too.

    uv sync                 once — the CLI evaluates this file with railway-sdk
    railway status          confirm which environment is linked
    railway config plan     read-only preview of the diff
    railway config apply    plan again, then apply after confirming

Omission is deletion, for resources and for fields alike: a service this file does not
name is planned for destruction, and a field it does not set is planned to null. Both
environments are therefore described here in full and switched on ``ctx``, never split
across files — a file that knew only about production would propose deleting
development's Redis the first time it was applied there. It also means the Railway
dashboard has stopped being a place to change things: an edit made there survives only
until the next apply.

Python authoring is in beta (TypeScript is the GA surface), so the helper names below may
move under us. What that costs is a rewrite of this file, not the deployment: plan and
apply live in the CLI, and this module only builds the graph they compare against.
"""

from railway_sdk import define_railway, github, postgres, preserve, project, redis, service, volume

REGION = "europe-west4-drams3a"
REPO = "joeljwilliams/wwstatsbot"
UPSTREAM = "https://github.com/joeljwilliams/wwstatsbot"

_VOLUME_ALERTS = {"usage": {"80": {}, "95": {}, "100": {}}}


def _sleep(enabled):
    """sleepApplication, but only when it is on.

    Railway normalises a field written with its own default value back to null, so
    `"sleepApplication": False` does not read back as False — it reads back as nothing,
    and the next `plan` proposes the same change again. Applying it never converges and
    every future plan carries a line nobody should act on, which is how people learn to
    skim plans. Off is expressed by saying nothing.
    """
    return {"sleepApplication": True} if enabled else {}


@define_railway
def main(ctx=None):
    prod = ctx.is_environment("production")

    # The two environments were created at different times and Railway suffixed the second
    # Redis and its volume. Renaming either to match would be a destroy-and-recreate of a
    # live store, so the names are carried as data instead of tidied up.
    redis_name = "Redis" if prod else "Redis-93UD"
    redis_volume_name = "redis-volume" if prod else "redis-volume-RI6D"

    # Serverless (formerly app-sleeping) stops a container after ~5 minutes with no
    # *outbound* traffic and wakes it on the next inbound request.
    #
    # The bot asks for it in both environments. The data layer asks for it in development
    # only, where an idle-hours cold start costs nothing. Production's Postgres and Redis
    # stay hot deliberately: a slept database answers the first connection with a 502, and
    # db.init_pool() builds the asyncpg pool once at startup with no retry — so a cold data
    # layer there is a crash loop rather than one slow reply.
    #
    # This was inert until 2.36.4, which is when the bot learned to stop talking: PTB's
    # persistence loop wrote bot_data to Redis every 60 seconds whether or not anything had
    # changed, and the asyncpg pool held a connection open besides, so the container never
    # saw five idle minutes. Both are fixed in wwstatsbot/runtime/redis_persistence.py and
    # wwstatsbot/data/db.py, and both are
    # pinned by tests — an idle bot that chatters looks exactly like one that does not.
    sleep_databases = not prod

    postgres_volume = volume(
        "postgres-volume",
        alerts=_VOLUME_ALERTS,
        allowOnlineResize=True,
        region=REGION,
        sizeMB=500,
    )

    redis_volume = volume(
        redis_volume_name,
        alerts=_VOLUME_ALERTS,
        allowOnlineResize=True,
        region=REGION,
        sizeMB=500,
    )

    # postgres()/redis() accept only region, image, output and defaultMountPath — every
    # other keyword is dropped on the floor without an error, and assigning `db.deploy =`
    # afterwards sets an attribute the graph builder never reads. with_fields() is the one
    # way to reach these, and it *replaces* the field, so the multiRegionConfig that
    # region= would have produced is written out here by hand.
    db = postgres("Postgres", region=REGION).with_fields(
        networking={"privateNetworkEndpoint": "postgres", "tcpProxies": {"5432": {}}},
        deploy={
            "multiRegionConfig": {REGION: {"numReplicas": 1}},
            **_sleep(sleep_databases),
        },
    )

    cache = redis(redis_name, region=REGION).with_fields(
        networking={"privateNetworkEndpoint": "redis" if prod else "redis-93ud"},
        deploy={
            "multiRegionConfig": {REGION: {"numReplicas": 1}},
            # Railway's own Redis template start command, carried verbatim from the
            # deployed service. Repeated here rather than left to the dashboard because
            # omitting a field clears it: a deploy block with no startCommand plans it to
            # null, dropping the --requirepass and --dir this store runs with.
            "startCommand": (
                '/bin/sh -c "rm -rf $RAILWAY_VOLUME_MOUNT_PATH/lost+found/ '
                "&& exec docker-entrypoint.sh redis-server --requirepass $REDIS_PASSWORD "
                '--save 60 1 --dir $RAILWAY_VOLUME_MOUNT_PATH"'
            ),
            **_sleep(sleep_databases),
        },
    )

    bot = service(
        "wwstatsbot",
        # Both branches are named because omitting one does not leave it alone: github()
        # defaults branch to "main", so a development file that said nothing would point
        # development at production's branch.
        #
        # Development's branch is a working knob — per the workflow in CLAUDE.md it gets
        # pointed at whichever feature branch is being live-tested, so it will legitimately
        # differ from `devel` while that test runs, and applying then yanks the environment
        # off the branch under test. The plan says so in as many words
        # — source.branch ("fix/..." -> "devel") — so read it before confirming, and
        # re-point the branch afterwards.
        source=github(
            REPO,
            branch="main" if prod else "devel",
            upstreamUrl=UPSTREAM,
            **({"checkSuites": False} if prod else {}),
        ),
        # Explicit, and load-bearing. Railway's own setting for this service is RAILPACK;
        # the image was only ever built from the Dockerfile because railway.json overrode
        # that at deploy time. Dropping that file without stating the builder here would
        # silently switch production to Railpack, which knows nothing about the uv-built
        # venv at /opt/venv, the non-root user, or the wwstatsbot/ package.
        build={"builder": "DOCKERFILE", "dockerfilePath": "/Dockerfile", "buildEnvironment": "V3"},
        healthcheck="/healthz",
        replicas={REGION: 1},
        deploy={
            "limitOverride": {"containers": {"cpu": 1, "memoryBytes": 1_000_000_000}},
            # No restartPolicy here, deliberately. railway.json set ON_FAILURE with 10
            # retries, which is word for word Railway's own default — so it normalises
            # back to null on apply and plan re-proposes it forever, the same trap as
            # sleepApplication above. Dropping it changes nothing about how the bot
            # restarts; state it again only to ask for something other than the default.
            "sleepApplication": True,
        },
        # Values stay on Railway. preserve() means "keep whatever is already set" —
        # BOT_TOKEN and the connection strings are secrets that must never be written into
        # this file, and DATABASE_URL / REDIS_URL are reference variables whose rendered
        # values differ per environment anyway.
        env={
            "BOT_TOKEN": preserve(),
            "DATABASE_URL": preserve(),
            "LOG_GROUP_ID": preserve(),
            "REDIS_URL": preserve(),
            "SUPERUSER_ID": preserve(),
            "WEBHOOK_URL": preserve(),
        },
    )

    return project(
        "Werewolf Stats Bot",
        resources=[bot, db, cache, postgres_volume, redis_volume],
    )
