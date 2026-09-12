"""Message builders shared by the command handlers and the inline query.

Each builder turns stats-API data (or the achievement cache) into a finished,
Telegram-ready string. They live apart from the handlers because both the slash commands
and inline mode render the *same* messages — /stats and an inline "My Stats" card are
byte-identical — so these are the single source of truth for what a user actually sees.

All output is HTML built by concatenation, which means **every interpolated value needs
html.escape() at exactly one place**: the escape happens here, and callers pass raw
values. Escaping upstream too would double-escape (a player named "Al & Sons" becoming
"Al &amp;amp; Sons"); skipping it lets a name containing "<" break the whole message.

tests/test_render_golden.py asserts the exact bytes of everything below. A change there
is a change to what users see.
"""

import html

import db
import notes
import playerdata
import roles
import templates as t


def role_label(api_role):
    """The stats API's role string with the game's emoji on it: "Chemist 🧪".

    The API sends a bare display name, and roles.py already holds the emoji for every role
    the game can deal, so the two only need joining. Resolution goes through roles.resolve()
    rather than a dict lookup on the name because that is the module's own vocabulary —
    accents, spacing and the odd trailing emoji all fold to the same key.

    Anything the registry does not recognise is passed through escaped and unadorned. A
    role the API knows and we do not is a stats card that reads slightly plainer, which is
    a great deal better than one that raises or prints nothing where the role should be.
    """
    found = roles.resolve(api_role)
    # A single match only: the Seer/Fool spellings resolve to two ids, and a stats card
    # cannot be ambiguous about which one a player most often was.
    if len(found) == 1:
        return roles.display(found[0])
    return html.escape(api_role or "")


# Every fetch below goes through playerdata rather than api directly, which is what records
# it and what lets a lookup be answered from the record when tgwerewolf.com is down. The
# price is that a fetcher hands back a Reading (.data and .age) instead of a bare payload,
# and the age has to be carried to the end of the message: `stale_notice` renders nothing
# for a live answer, so the golden output of a working lookup is unchanged.
#
# The player's name is deliberately NOT passed down to be recorded. By the time it reaches
# here it has already been html.escape()-ed by the caller, and storing markup would have it
# escaped a second time wherever the record is read back. The callers that hold a raw name
# (handlers/search.py, handlers/welcome.py, handlers/gamesession.py) pass one.


async def build_kills_msg(user_id, name):
    kills = await playerdata.get_kills(user_id)
    msg = t.KILLS_HEADER.format(user_id=user_id, name=name)
    for k in kills.data:
        msg += t.COUNT_ROW.format(count=k["times"], label=html.escape(k["name"]))
    return msg + playerdata.stale_notice(kills.age)


async def build_killed_by_msg(user_id, name):
    killedby = await playerdata.get_killed_by(user_id)
    msg = t.KILLED_BY_HEADER.format(user_id=user_id, name=name)
    for k in killedby.data:
        msg += t.COUNT_ROW.format(count=k["times"], label=html.escape(k["name"]))
    return msg + playerdata.stale_notice(killedby.age)


async def build_deaths_msg(user_id, name):
    deaths = await playerdata.get_deaths(user_id)
    stats = await playerdata.get_stats(user_id)
    msg = t.DEATHS_HEADER.format(user_id=user_id, name=name)
    for d in deaths.data:
        # The total per kill method is derived from the percentage in the JSON,
        # so the value is approximate rather than exact.
        total = round((stats.data["gamesPlayed"] - stats.data["survived"]["total"]) * float(d["percent"]) / 100)
        msg += t.DEATH_ROW.format(percent=d["percent"], method=d["method"], total=total)
    # Two endpoints, one message: the notice reports the older of them, because that is how
    # fresh the whole thing is.
    return msg + playerdata.stale_notice(deaths.age, stats.age)


async def build_stats_msg(user_id, name, by_id=False):
    reading = await playerdata.get_stats(user_id)
    count = await playerdata.get_achievement_count(user_id)
    stats, achievements = reading.data, count.data

    if not stats:
        template = t.NO_GAMES_BY_ID if by_id else t.NO_GAMES
        return template.format(user_id=user_id, name=name)

    name_template = t.STATS_NAME_BY_ID if by_id else t.STATS_NAME
    msg = name_template.format(user_id=user_id, name=name, role=role_label(stats["mostCommonRole"]))
    msg += t.STATS_ACHIEVEMENTS.format(count=achievements)
    msg += t.STATS_WON.format(total=stats["won"]["total"], percent=stats["won"]["percent"])
    msg += t.STATS_LOST.format(total=stats["lost"]["total"], percent=stats["lost"]["percent"])
    msg += t.STATS_SURVIVED.format(total=stats["survived"]["total"], percent=stats["survived"]["percent"])
    msg += t.STATS_TOTAL.format(total=stats["gamesPlayed"])
    if stats["mostKilled"]:
        msg += t.STATS_MOST_KILLED.format(
            times=stats["mostKilled"]["times"], name=html.escape(stats["mostKilled"]["name"])
        )
    if stats["mostKilledBy"]:
        msg += t.STATS_MOST_KILLED_BY.format(
            times=stats["mostKilledBy"]["times"], name=html.escape(stats["mostKilledBy"]["name"])
        )
    return msg + playerdata.stale_notice(reading.age, count.age)


# At or below this length a query means an initialism and nothing else -- it never
# reaches full-text search. Two characters is far too short for FTS to say anything
# useful: all it can do is prefix-match, so "sa" returned nine achievements, one of them
# only because the word "silver" appears in a description. The one answer a human means
# by "sa" -- Strongest Alpha -- was buried in the noise it came with.
#
# Deliberately a hard cutover rather than a ranking boost. Putting initialism hits in
# front of the FTS results still leaves the other eight on screen, and /sch renders a
# list: being right in position one does not help when positions two through nine are
# wrong. The cost is that a two-letter query nobody registered as an initialism ("he")
# now finds nothing until the third character arrives, which for inline as-you-type is
# one keystroke of patience.
#
# Anything longer is left alone -- the search_tsv column already indexes initialisms at
# weight B, so "dygy" and "SSS" resolve through FTS with the ranking /info depends on.
_INITIALISM_ONLY_MAX_LEN = 2


async def build_info_results(search):
    """Full-text achievement search (name / name-initialism / description), with
    a substring-on-name fallback when FTS finds nothing.

    A query of _INITIALISM_ONLY_MAX_LEN characters or fewer skips both and is answered
    from the initialisms alone.
    """
    if len(search) <= _INITIALISM_ONLY_MAX_LEN and search.isalnum():
        return db.search_initialism(search)
    matches = await db.search_achievements(search)
    if not matches:
        # FTS found nothing (e.g. a stopword-only query, or a mid-word substring that
        # prefix matching can't catch). Fall back to the old case-insensitive
        # substring-on-name scan over the in-memory cache.
        s = search.lower()
        matches = [a for a in db.get_achievements() if s in a["name"].lower()]
    return matches


def format_single_achv(achv):
    """HTML block for one achievement, including the type and notes fields."""
    msg = t.ACHV_CARD.format(
        name=html.escape(achv["name"]),
        desc=html.escape(achv["desc"]),
        type=achv.get("type", "instantaneous"),
    )
    # Normalise through parse/serialize so display is always canonical (markers
    # present and ordered) even for legacy or /db-console-edited notes.
    # Named `rendered`, not `notes`: assigning to `notes` anywhere in this function
    # would shadow the module import and make the call below an UnboundLocalError.
    rendered = notes.serialize_notes(notes.parse_notes(achv.get("notes", "")))
    if rendered:
        # Expandable blockquote (Bot API 7.0+) so long notes collapse by default.
        msg += t.ACHV_CARD_NOTES.format(notes=html.escape(rendered))
    return msg
