"""The bot's own record of what the stats API said, and the only way to ask it.

Every fetcher in `api.py` is wrapped here exactly once, and nothing outside this module
calls one directly any more. Going through a single door buys three things that are all
the same mechanism seen from different sides:

* **A record.** Each successful lookup is written to `player_snapshots`, one row per
  player per endpoint. Nobody has to remember to record anything, because recording is
  what a lookup *is*.
* **New achievements.** A lookup compares what came back against the row it replaced, and
  anything that was not there before is announced to the log group. The API has no "since"
  parameter and no notification of any kind, so a diff against our last answer is the only
  way to learn that somebody earned something.
* **An answer when the site is down.** tgwerewolf.com is a volunteer-run stats site and is
  periodically unreachable. A failed fetch falls back to the recorded payload rather than
  raising, so a command answers instead of erroring — and says, in the message, that it is
  reading from the record.

**Every fetcher returns a `Reading`, never a bare payload.** That is deliberate and is
the reason the call sites all changed: `age` is None when the data came from the API this
second, and a number of seconds when it came from our record. A function that returned only
the payload would make stale data indistinguishable from fresh at every call site, and this
bot already has a rule about that — /schall's remembered player list carries a 🕐 with its
age for exactly the same reason. A record must never pass for a live answer.

**The database is never a precondition for answering.** Recording is wrapped so that a
write failure — or no pool at all, which is how the test suite runs — logs and is otherwise
invisible. A stats lookup that started failing because Postgres hiccuped would be a strictly
worse bot than the one that had no record at all.
"""

import collections
import html

import structlog
from telegram.constants import ParseMode

import api
import db
import settings
import templates as t

logger = structlog.get_logger(__name__)

# What a fetcher hands back. `age` is None for a live answer and a float of seconds for one
# read out of the record — callers test `age is not None` to decide whether to say so.
Reading = collections.namedtuple("Reading", "data age")

# The `kind` column's vocabulary, and the fetcher behind each one. Strings rather than an
# enum because they are stored in the database, where a row has to stay readable from /db
# years after whatever wrote it.
STATS = "stats"
KILLS = "kills"
KILLED_BY = "killedby"
DEATHS = "deaths"
ACHIEVEMENTS = "achievements"

# The name of the api.py function behind each kind, resolved with getattr at call time
# rather than held here as the function object. api.py's own docstring records why: binding
# a name at import time makes a later swap of it invisible to whoever bound it, and the test
# suite swaps these. A dict of function objects would still pass every test while the tests
# were patching something nothing calls.
_FETCHERS = {
    STATS: "get_stats",
    KILLS: "get_kills",
    KILLED_BY: "get_killed_by",
    DEATHS: "get_deaths",
    ACHIEVEMENTS: "get_achievements",
}

KINDS = tuple(_FETCHERS)

# At most this many achievement names in one announcement; the rest are counted. A player
# looked up for the first time never reaches here (see _record), so the cap is for the
# player nobody has checked in months, not for a baseline flood.
_ANNOUNCE_MAX = 10

# The bot used to post to the log group, installed by main's post_init. Module-level rather
# than threaded through every call because the announcement happens under a builder that has
# no `context` and no business acquiring one — builders.py renders messages and knows
# nothing about sending them. None until the application starts, and None for the whole of
# the test suite unless a test installs a fake, which is why every use is guarded.
_bot = None


def set_announcer(bot):
    """Install (or clear, with None) the bot that posts new achievements to the log group."""
    global _bot
    _bot = bot


# --- Fetchers ---------------------------------------------------------------


async def get_stats(user_id, name=None):
    return await _read(STATS, user_id, name)


async def get_kills(user_id, name=None):
    return await _read(KILLS, user_id, name)


async def get_killed_by(user_id, name=None):
    return await _read(KILLED_BY, user_id, name)


async def get_deaths(user_id, name=None):
    return await _read(DEATHS, user_id, name)


async def get_achievements(user_id, name=None):
    return await _read(ACHIEVEMENTS, user_id, name)


async def get_achievement_count(user_id, name=None):
    """The total only, keeping the age so a stale count can still be labelled as one."""
    reading = await get_achievements(user_id, name)
    return Reading(len(reading.data), reading.age)


async def _read(kind, user_id, name):
    """Fetch one endpoint, record what came back, and fall back to the record on failure.

    `name` is the player's display name **unescaped**, and only for the record: callers
    holding an HTML-escaped name (everything reached through builders.py, where escaping has
    already happened) pass nothing rather than storing markup that a later render would
    escape a second time.

    The fallback deliberately re-raises when there is nothing recorded. A caller that has
    always handled a failed lookup — /schall reports the player as uncheckable, the join
    announcement stays quiet about them — must keep getting the failure it knows how to
    describe, rather than an empty payload that reads as a statement about the player.
    """
    try:
        data = await getattr(api, _FETCHERS[kind])(user_id)
    except Exception as exc:
        stored = await _recall(user_id, kind)
        if stored is None:
            raise
        payload, age = stored
        logger.warning("stats_api_fallback", kind=kind, user_id=user_id, age=int(age), error=str(exc))
        return Reading(payload, age)
    await _record(user_id, kind, data, name)
    return Reading(data, None)


# --- The record -------------------------------------------------------------


async def _recall(user_id, kind):
    """The recorded payload as (payload, age), or None when there is nothing to read."""
    if not db.has_pool():
        return None
    try:
        return await db.load_player_snapshot(user_id, kind)
    except Exception:
        # The fallback's own fallback. Reached only when the API is already down, so the
        # caller is about to be told the lookup failed either way.
        logger.exception("player_snapshot_read_failed", kind=kind, user_id=user_id)
        return None


async def _record(user_id, kind, payload, name):
    """Store one fresh payload, and announce any achievement it reveals.

    Never raises. Answering the user is the job; keeping the record is a side effect of
    doing it, and an error here must not turn a working command into a failed one.
    """
    if not db.has_pool():
        return
    if payload is None:
        # What the API sends for a player who has never played. There is nothing to
        # remember, and storing a JSON null would make "we have no record of this player"
        # and "we recorded that there is nothing" the same row to every reader of it.
        return
    if kind == ACHIEVEMENTS and not payload and await _has_achievements(user_id):
        # A player's achievements cannot go from "some" to "none": they are never revoked.
        # An empty list where we hold a full one is the API answering badly — a 200 with a
        # body that decoded, which is exactly the failure the try/except above cannot see.
        # Recording it would erase the record and then report every achievement as new the
        # moment the site recovered, filling the log group with a player's whole history.
        logger.warning("player_achievements_empty_ignored", user_id=user_id)
        return
    try:
        previous = await db.save_player_snapshot(user_id, kind, payload, name)
    except Exception:
        logger.exception("player_snapshot_write_failed", kind=kind, user_id=user_id)
        return
    if kind != ACHIEVEMENTS or previous is None:
        # `previous is None` is the first time this player has ever been looked up. It is a
        # baseline, not news: announcing a diff against nothing would post a hundred-line
        # message for a player who earned all of it long before this bot watched them.
        return
    earned = _newly_earned(previous, payload)
    if earned:
        await _announce(user_id, name, earned)


async def _has_achievements(user_id):
    stored = await _recall(user_id, ACHIEVEMENTS)
    return bool(stored and stored[0])


def _newly_earned(previous, current):
    """Achievement names in `current` that `previous` did not have, in the API's order.

    Compared by name because that is all the API's achievement objects reliably carry, and
    it is the same key `achievements.name` is stored under. A renamed achievement therefore
    reads as a new one — rare, and the log group is where an operator would want to see it
    anyway.
    """
    had = {a.get("name") for a in previous}
    return [a["name"] for a in current if a.get("name") not in had]


async def _announce(user_id, name, earned):
    """Post newly earned achievements to the log group, if there is one to post to."""
    if _bot is None or not settings.LOG_GROUP_ID:
        return
    msg = t.LOG_ACHIEVEMENT_HEADER.format(
        user_id=user_id,
        # Only some lookups carry a name (see _read), so the id stands in when none was
        # recorded. Escaped here, at the one place it is rendered, like every other name.
        name=_display_name(name, user_id),
        count=len(earned),
        plural="" if len(earned) == 1 else "s",
    )
    for achv_name in earned[:_ANNOUNCE_MAX]:
        msg += t.LOG_ACHIEVEMENT_ROW.format(name=html.escape(achv_name))
    if len(earned) > _ANNOUNCE_MAX:
        msg += t.LOG_ACHIEVEMENT_MORE.format(count=len(earned) - _ANNOUNCE_MAX)
    try:
        await _bot.send_message(settings.LOG_GROUP_ID, msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception:
        # Same shape as the error handler's own report: the log group is where problems are
        # described, so failing to reach it can only be logged.
        logger.exception("achievement_announce_failed", user_id=user_id)
    else:
        logger.info("achievements_announced", user_id=user_id, count=len(earned))


def _display_name(name, user_id):
    return html.escape(name) if name else str(user_id)


# --- Reporting an age -------------------------------------------------------

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR


def age_label(seconds):
    """A recorded payload's age, in the largest unit that fits: "3m", "5h", "2d".

    Its own formatter rather than handlers.common.describe_age, which exists for /schall's
    remembered player list and is tuned to an hour-long window — it renders everything in
    minutes, so a record from last Tuesday would read "9,431m ago". These two measure
    different things and neither scale is wrong for the other's job.
    """
    seconds = max(0, int(seconds))
    if seconds < _MINUTE:
        return t.AGE_MOMENTS
    if seconds < _HOUR:
        return t.AGE_MINUTES.format(count=seconds // _MINUTE)
    if seconds < _DAY:
        return t.AGE_HOURS.format(count=seconds // _HOUR)
    return t.AGE_DAYS.format(count=seconds // _DAY)


def stale_notice(*ages):
    """The footer for a message built from the record, or "" when everything was live.

    Takes every age a message was assembled from — /stats reads two endpoints, /deaths two —
    and reports the oldest, because that is the one the whole message is only as fresh as.
    """
    known = [age for age in ages if age is not None]
    if not known:
        return ""
    return t.STALE_NOTICE.format(age=age_label(max(known)))
