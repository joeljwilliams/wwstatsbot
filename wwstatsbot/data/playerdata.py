"""The bot's own record of what the stats API said, and the only way to ask it.

Every fetcher in `api.py` is wrapped here exactly once, and nothing outside this module
calls one directly any more. Going through a single door buys three things that are all
the same mechanism seen from different sides:

* **A record.** Each successful lookup is written to `player_snapshots`, one row per
  player per endpoint. Nobody has to remember to record anything, because recording is
  what a lookup *is*.
* **A name for a bare id.** None of the five stat endpoints carries the player's *own*
  name, only the names of players they killed or were killed by. `player_profile()` asks
  the profile endpoint, which is the only source of a name *and* of the username that is the
  only link either caller can use — and the only endpoint that raises for an id the game has
  never seen.
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
import re

import structlog
from telegram.constants import ParseMode

from wwstatsbot.data import api, db
from wwstatsbot.render import badges
from wwstatsbot.render import templates as t
from wwstatsbot.runtime import settings

logger = structlog.get_logger(__name__)

# What a fetcher hands back. `age` is None for a live answer and a float of seconds for one
# read out of the record — callers test `age is not None` to decide whether to say so.
Reading = collections.namedtuple("Reading", "data age")

# What the stats site knows a player as. Either field may be None on its own: a player who
# has never set a username has a name and no link, which is ordinary rather than a failure.
Profile = collections.namedtuple("Profile", "name username")

# The `kind` column's vocabulary, and the fetcher behind each one. Strings rather than an
# enum because they are stored in the database, where a row has to stay readable from /db
# years after whatever wrote it.
STATS = "stats"
KILLS = "kills"
KILLED_BY = "killedby"
DEATHS = "deaths"
ACHIEVEMENTS = "achievements"
# The player's own profile — the only endpoint that carries their *name*, which none of the
# five stat endpoints do. Recorded like the rest, so a name learned once outlives the site
# being down.
PLAYER = "player"

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
    PLAYER: "get_player",
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


async def get_player(user_id, name=None):
    return await _read(PLAYER, user_id, name)


async def get_achievement_count(user_id, name=None):
    """The total only, keeping the age so a stale count can still be labelled as one."""
    reading = await get_achievements(user_id, name)
    return Reading(len(reading.data), reading.age)


async def player_profile(user_id):
    """What the stats site knows a player as: `Profile(name, username)`. **Never raises.**

    The one way to put a name to a bare user id, and the reason it is worth an extra request:
    the five stat endpoints carry the names of *other* players (who you killed, who killed
    you) and never your own. So `/stats <id>` had nothing to title the card with but the
    digits that were typed, and a log-group announcement had nothing to call a player whose
    lookup arrived without one.

    The username matters as much as the name, because it is the only **link** either of those
    two places can use. `tg://user?id=` resolves only in a client that has already met that
    user, which a log group reading about strangers generally has not, and a `/stats <id>`
    card could not be linked at all for the same reason — so both were plain text about
    somebody nobody could tap through to. `https://t.me/<username>` resolves for anyone.

    Failure is an answer here rather than an error. An id the game has never seen — a typo in
    `/stats <number>`, most of the time — comes back as an HTML error page (see api.get_player),
    and "we could not name them" has to degrade to showing the id rather than to a failed
    command. Either field can be missing on its own: plenty of players have never set a
    username, so a name with no link is an ordinary outcome rather than a degraded one.
    """
    try:
        profile = await get_player(user_id)
    except Exception as exc:
        logger.info("player_profile_unknown", user_id=user_id, error=str(exc))
        return Profile(None, None)
    data = profile.data if isinstance(profile.data, dict) else {}
    return Profile(data.get("name") or None, _tidy_username(data.get("username")))


# Telegram's own rule for a username: letters, digits and underscores, 5-32 characters. A
# leading @ is tolerated because the field is free text in somebody else's database, not
# because the API has ever been seen to send one.
#
# Checked rather than trusted because the value goes straight into an href. It arrives from
# the game's database, which got it from Telegram years ago and has never revalidated it —
# and a "username" containing a quote would close the attribute and put whatever followed
# into the markup of a message this bot sends. Anything not matching is treated as no
# username at all, which is already an ordinary case.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def _tidy_username(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lstrip("@")
    return value if _USERNAME_RE.match(value) else None


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
    if payload is None or payload == "":
        # The two ways the site says "no such player": a JSON null, and the empty *string*
        # the stat endpoints actually answer with (`return Json("")` upstream, confirmed
        # against the live API). Neither is worth a row — and storing one would make "we
        # have no record of this player" and "we recorded that there is nothing" read the
        # same to everything that reads the row back.
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
    template, who = await _who(user_id, name)
    msg = template.format(count=len(earned), plural="" if len(earned) == 1 else "s", **who)
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


async def _who(user_id, name):
    """How to name this player in the log group: (header template, its name fields).

    The template and its fields travel together rather than the line being built here,
    because the header also carries {count} and {plural}, and str.format cannot fill some
    fields and leave others — a half-formatted template raises on the fields it was not
    given.

    Who the player is, is asked of the site rather than taken from `name`: `name` is whatever
    the caller happened to hold, and most lookups hold nothing (theirs is already escaped by
    the time it reaches a fetcher, so it is deliberately not passed down). That left the great
    majority of announcements naming a player by bare user id.

    One extra request, and only on an announcement: those happen when somebody actually earns
    something, not on every lookup. A caller's own name is the fallback ahead of the id, so a
    roster or join lookup still reads as a name when the site cannot be asked.

    **The link is `https://t.me/<username>` when there is a username, and only then.** A log
    group reads about players none of its members has necessarily met, and `tg://user?id=`
    resolves for nobody in that position — so the id-based mention is the fallback rather
    than the rule, kept because it does work for a player somebody in the room has seen.

    Escaped here, at the one place it is rendered, like every other name in this bot.
    """
    profile = await player_profile(user_id)
    shown = html.escape(profile.name or name or str(user_id))
    badge = badges.of(user_id)
    if profile.username:
        return t.LOG_ACHIEVEMENT_HEADER_LINKED, {"username": profile.username, "name": shown, "badge": badge}
    return t.LOG_ACHIEVEMENT_HEADER, {"user_id": user_id, "name": shown, "badge": badge}


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
