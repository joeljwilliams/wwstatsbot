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

import api
import db
import notes
import templates as t


async def build_kills_msg(user_id, name):
    kills = await api.get_kills(user_id)
    msg = t.KILLS_HEADER.format(user_id=user_id, name=name)
    for k in kills:
        msg += t.COUNT_ROW.format(count=k["times"], label=html.escape(k["name"]))
    return msg


async def build_killed_by_msg(user_id, name):
    killedby = await api.get_killed_by(user_id)
    msg = t.KILLED_BY_HEADER.format(user_id=user_id, name=name)
    for k in killedby:
        msg += t.COUNT_ROW.format(count=k["times"], label=html.escape(k["name"]))
    return msg


async def build_deaths_msg(user_id, name):
    deaths = await api.get_deaths(user_id)
    stats = await api.get_stats(user_id)
    msg = t.DEATHS_HEADER.format(user_id=user_id, name=name)
    for d in deaths:
        # The total per kill method is derived from the percentage in the JSON,
        # so the value is approximate rather than exact.
        total = round((stats["gamesPlayed"] - stats["survived"]["total"]) * float(d["percent"]) / 100)
        msg += t.DEATH_ROW.format(percent=d["percent"], method=d["method"], total=total)
    return msg


async def build_stats_msg(user_id, name, by_id=False):
    stats = await api.get_stats(user_id)
    achievements = await api.get_achievement_count(user_id)

    if not stats:
        template = t.NO_GAMES_BY_ID if by_id else t.NO_GAMES
        return template.format(user_id=user_id, name=name)

    name_template = t.STATS_NAME_BY_ID if by_id else t.STATS_NAME
    msg = name_template.format(user_id=user_id, name=name, role=stats["mostCommonRole"])
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
    return msg


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
