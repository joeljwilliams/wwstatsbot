"""The game's role registry: teams, tags, and every name a player might type.

Nothing in this repo knew what a role *was* before this module. Roles only ever appeared
as free text out of the stats API (``stats["mostCommonRole"]``), which is fine for printing
and useless for reasoning. Achievement feasibility needs both — "is there a wolf that can
eat someone tonight", "can the cult convert this player" — so the taxonomy lives here, in
one place, and every consumer reads it through the helpers below rather than hardcoding
role ids.

Two distinctions are load-bearing and easy to collapse by accident:

* **team is not capability.** The Sorcerer and (until they turn) the Traitor are wolf-team
  but cannot perform the night kill, so ``team`` answers "who wins together" and the
  ``PACK`` tag answers "who can eat you". Using team membership as a proxy for the second
  advertises achievements that are impossible — a game whose only wolf-team member is a
  Sorcerer has no night kill at all.
* **starting roles understate the game.** Roles convert: the Alpha bites, the cult recruits,
  the Cursed turns, the Thief steals. The tags that describe those transitions
  (``POTENTIAL_WOLF``, ``CULT_IMMUNE``, ``STEAL_IMMUNE``, ``ROLE_SWING``) are what let a
  caller compute a reachable ceiling instead of a starting count.

Role display names are **data, not prose** — they are the game's own vocabulary, and a
player typing ``/role bk`` expects "Barkeep 🍸" back in any locale. They deliberately do
not live in templates.py and are not translated, for the same reason command words aren't.
"""

import difflib
import re

from unidecode import unidecode

# --- Teams -----------------------------------------------------------------

VILLAGE = "village"
WOLF = "wolf"
CULT = "cult"
SOLO = "solo"

TEAMS = (VILLAGE, WOLF, CULT, SOLO)

# --- Tags ------------------------------------------------------------------

# Eats with the pack at night. Strictly narrower than team WOLF: the Sorcerer wins with
# the wolves but never kills, so "is a wolf attack possible" is a PACK question.
PACK = "pack"
# Can cause a death by their own action, whatever the mechanism (eat, shoot, poison, burn).
KILLER = "killer"
# Leaves their house at night, so can be caught out by the Grave Digger's pits, the
# Arsonist's fire, or counted by the Chef's rice. Per the game's own list of visiting roles.
VISITOR = "visitor"
# The opposite: others come to *them* (the Barkeep's bar, the Grave Digger's graves).
VISITED = "visited"
# Village team today, wolf later: the Traitor when the wolves die, the Wild Child when
# their role model dies, the Cursed when bitten.
POTENTIAL_WOLF = "potential_wolf"
# The role identity itself can change, so this player may end up earning achievements
# belonging to a role they did not start as.
ROLE_SWING = "role_swing"
# Recruits others into their own team (cult conversion, the Alpha's bite).
CONVERTER = "converter"
# The cult cannot convert these, which caps how large the cult can ever get.
CULT_IMMUNE = "cult_immune"
# The Thief cannot steal these.
STEAL_IMMUNE = "steal_immune"
# "A bad role" in the game's own sense, as used by achievements like Double Shot.
BAD = "bad"
# Wins alone. Kept separate from BAD because the Tanner is solo without being a killer.
LONER = "loner"


def _tags(*names):
    """Small constructor so the table below reads as data rather than punctuation."""
    return frozenset(names)


# --- The registry ----------------------------------------------------------
#
# One entry per role the game bot can deal, keyed by a stable id of our own. The id is
# what rules and stored session state reference, so it must never change; display names
# and emoji are cosmetic and may.
#
# `aliases` holds only what normalise() cannot derive. The full display name is always
# accepted, so "Alpha Wolf", "alpha wolf" and "ALPHAWOLF" need no listing — what does is
# the /about command suffix (the codes players actually use, e.g. AppS, GA, BH) and any
# shorthand seen in play. The alias table is deliberately a *superset* of the game's own:
# accepting a spelling the real manager rejects costs nothing, while rejecting one it
# accepts is a player mid-game discovering the stand-in is worse.
ROLES = {
    # --- Village ---
    "villager": {"name": "Villager", "emoji": "👱", "team": VILLAGE, "tags": _tags(), "aliases": ("vg",)},
    "drunk": {"name": "Drunk", "emoji": "🍻", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "seer": {"name": "Seer", "emoji": "👳", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "cursed": {
        "name": "Cursed",
        "emoji": "😾",
        "team": VILLAGE,
        "tags": _tags(POTENTIAL_WOLF, ROLE_SWING),
        "aliases": (),
    },
    "harlot": {"name": "Harlot", "emoji": "💋", "team": VILLAGE, "tags": _tags(VISITOR), "aliases": ()},
    "beholder": {"name": "Beholder", "emoji": "👁", "team": VILLAGE, "tags": _tags(), "aliases": ("bh",)},
    "gunner": {"name": "Gunner", "emoji": "🔫", "team": VILLAGE, "tags": _tags(KILLER), "aliases": ()},
    # Wolf-team by win condition, but a villager in every mechanical sense until the last
    # wolf dies — they cannot eat, and the cult can convert them. Counting them as a wolf
    # is how "is an attack possible tonight" gets the wrong answer.
    "traitor": {
        "name": "Traitor",
        "emoji": "🖕",
        "team": VILLAGE,
        "tags": _tags(POTENTIAL_WOLF, ROLE_SWING),
        "aliases": ("tr",),
    },
    "guardian_angel": {
        "name": "Guardian Angel",
        "emoji": "👼",
        "team": VILLAGE,
        "tags": _tags(VISITOR),
        "aliases": ("ga", "angel"),
    },
    "detective": {"name": "Detective", "emoji": "🕵", "team": VILLAGE, "tags": _tags(), "aliases": ("det",)},
    "apprentice_seer": {
        "name": "Apprentice Seer",
        "emoji": "🙇",
        "team": VILLAGE,
        "tags": _tags(),
        "aliases": ("apps", "appseer", "apprentice"),
    },
    "cultist_hunter": {
        "name": "Cultist Hunter",
        "emoji": "💂",
        "team": VILLAGE,
        "tags": _tags(KILLER, VISITOR, CULT_IMMUNE),
        "aliases": ("ch", "culthunter"),
    },
    "wild_child": {
        "name": "Wild Child",
        "emoji": "👶",
        "team": VILLAGE,
        "tags": _tags(POTENTIAL_WOLF, ROLE_SWING),
        "aliases": ("wc",),
    },
    # A villager with broken visions, not a bad role: the Fool believes they are the Seer.
    "fool": {"name": "Fool", "emoji": "🃏", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "mason": {"name": "Mason", "emoji": "👷", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "cupid": {"name": "Cupid", "emoji": "🏹", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "hunter": {"name": "Hunter", "emoji": "🎯", "team": VILLAGE, "tags": _tags(KILLER), "aliases": ()},
    "mayor": {"name": "Mayor", "emoji": "🎖", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "prince": {"name": "Prince", "emoji": "👑", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "clumsy": {
        "name": "Clumsy Guy",
        "emoji": "🤕",
        "team": VILLAGE,
        "tags": _tags(),
        "aliases": ("clumsy", "cg"),
    },
    "blacksmith": {
        "name": "Blacksmith",
        "emoji": "⚒",
        "team": VILLAGE,
        "tags": _tags(),
        "aliases": ("bs", "smith"),
    },
    "sandman": {"name": "Sandman", "emoji": "💤", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "oracle": {"name": "Oracle", "emoji": "🌀", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    # A lowly villager the Seer misreads as a wolf. Village team, no transformation of its
    # own — being bitten by the Alpha is something that can happen to anyone.
    "wolfman": {"name": "WolfMan", "emoji": "👱🌚", "team": VILLAGE, "tags": _tags(), "aliases": ("wm",)},
    "pacifist": {"name": "Pacifist", "emoji": "☮️", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "wise_elder": {
        "name": "Wise Elder",
        "emoji": "📚",
        "team": VILLAGE,
        "tags": _tags(),
        "aliases": ("we", "elder"),
    },
    "troublemaker": {"name": "Troublemaker", "emoji": "🤯", "team": VILLAGE, "tags": _tags(), "aliases": ("tm",)},
    "chemist": {
        "name": "Chemist",
        "emoji": "👨‍🔬",
        "team": VILLAGE,
        "tags": _tags(VISITOR, KILLER),
        "aliases": (),
    },
    "grave_digger": {
        "name": "Grave Digger",
        "emoji": "☠️",
        "team": VILLAGE,
        "tags": _tags(VISITED),
        "aliases": ("gd", "digger"),
    },
    "augur": {"name": "Augur", "emoji": "🦅", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    "chef": {"name": "Chef", "emoji": "🍚", "team": VILLAGE, "tags": _tags(), "aliases": ()},
    # Visited, never visiting: the villagers come to the bar. Tagging the Barkeep as a
    # visitor inflates "It Was a Busy Night!", which counts roles that visit *you*.
    "barkeep": {
        "name": "Barkeep",
        "emoji": "🍸",
        "team": VILLAGE,
        "tags": _tags(VISITED),
        "aliases": ("bk", "bar"),
    },
    # --- Wolf ---
    "werewolf": {
        "name": "Werewolf",
        "emoji": "🐺",
        "team": WOLF,
        "tags": _tags(PACK, KILLER, VISITOR, BAD, CULT_IMMUNE, STEAL_IMMUNE),
        "aliases": ("ww", "wolf"),
    },
    "alpha_wolf": {
        "name": "Alpha Wolf",
        "emoji": "⚡️",
        "team": WOLF,
        "tags": _tags(PACK, KILLER, VISITOR, BAD, CONVERTER, CULT_IMMUNE, STEAL_IMMUNE),
        "aliases": ("aw", "alpha"),
    },
    # Pack member, but kills nothing itself — its death grants the pack a second victim.
    "wolf_cub": {
        "name": "Wolf Cub",
        "emoji": "🐶",
        "team": WOLF,
        "tags": _tags(PACK, VISITOR, BAD, CULT_IMMUNE, STEAL_IMMUNE),
        "aliases": ("cub",),
    },
    # Freezes rather than eats, so PACK without KILLER.
    "snow_wolf": {
        "name": "Snow Wolf",
        "emoji": "🐺☃️",
        "team": WOLF,
        "tags": _tags(PACK, VISITOR, BAD, CULT_IMMUNE, STEAL_IMMUNE),
        "aliases": ("sw",),
    },
    # The mirror of the WolfMan: a real wolf the Seer reads as a villager.
    "lycan": {
        "name": "Lycan",
        "emoji": "🐺🌝",
        "team": WOLF,
        "tags": _tags(PACK, KILLER, VISITOR, BAD, CULT_IMMUNE, STEAL_IMMUNE),
        "aliases": (),
    },
    # Wolf team, wolf-aligned seer, no kill of its own — and notably neither cult- nor
    # steal-immune, since only actual wolves are protected.
    "sorcerer": {"name": "Sorcerer", "emoji": "🔮", "team": WOLF, "tags": _tags(BAD), "aliases": ("sorc",)},
    # --- Cult ---
    "cultist": {
        "name": "Cultist",
        "emoji": "👤",
        "team": CULT,
        "tags": _tags(BAD, CONVERTER, VISITOR, STEAL_IMMUNE),
        "aliases": ("cult",),
    },
    # --- Solo ---
    "serial_killer": {
        "name": "Serial Killer",
        "emoji": "🔪",
        "team": SOLO,
        "tags": _tags(KILLER, VISITOR, BAD, LONER, CULT_IMMUNE, STEAL_IMMUNE),
        "aliases": ("sk",),
    },
    "arsonist": {
        "name": "Arsonist",
        "emoji": "🔥",
        "team": SOLO,
        "tags": _tags(KILLER, VISITOR, BAD, LONER, CULT_IMMUNE),
        "aliases": ("arso",),
    },
    # Solo but not a killer, which is why LONER and BAD are separate tags.
    "tanner": {"name": "Tanner", "emoji": "👺", "team": SOLO, "tags": _tags(LONER), "aliases": ()},
    "doppelganger": {
        "name": "Doppelgänger",
        "emoji": "🎭",
        "team": SOLO,
        "tags": _tags(ROLE_SWING, LONER, CULT_IMMUNE),
        "aliases": ("dg", "dopp"),
    },
    "thief": {
        "name": "Thief",
        "emoji": "😈",
        "team": SOLO,
        "tags": _tags(ROLE_SWING, VISITOR, CULT_IMMUNE),
        "aliases": (),
    },
}


# A player told they are the Seer cannot know they are not the Fool — the game tells both
# the same thing. `/role sf` records that honestly rather than forcing a guess, and the
# player is treated as eligible for either role's achievements until they find out.
SEER_FOOL = ("seer", "fool")
_SEER_FOOL_ALIASES = ("sf", "s/f", "seerfool", "seer/fool", "foolseer")


_NON_ALNUM = re.compile(r"[^a-z0-9]")


def normalise(text):
    """Fold a typed role name to its lookup key.

    Case, spacing and punctuation all vary in play ("Alpha Wolf", "alpha-wolf", "ALPHAWOLF"),
    and the game's own names carry accents and emoji the keyboard makes awkward — hence
    unidecode, which is already a dependency: it maps "Doppelgänger" to "doppelganger" so
    the obvious ASCII spelling resolves. Returns "" for input with nothing to match on.
    """
    if not text:
        return ""
    return _NON_ALNUM.sub("", unidecode(text).lower())


def _build_alias_index():
    """name/alias key -> tuple of role ids.

    Built once at import. A tuple rather than a single id because of the Seer/Fool pair;
    every other entry has exactly one. Collisions are a programming error, not a runtime
    condition — two roles claiming one alias means a player's `/role` silently resolves to
    whichever was defined last — so tests/test_roles.py asserts the index is injective.
    """
    index = {}
    for role_id, role in ROLES.items():
        for spelling in (role_id, role["name"]) + tuple(role["aliases"]):
            key = normalise(spelling)
            if key:
                index[key] = (role_id,)
    for spelling in _SEER_FOOL_ALIASES:
        index[normalise(spelling)] = SEER_FOOL
    return index


ALIASES = _build_alias_index()


def resolve(text):
    """Role ids for a typed name: one id, two for the Seer/Fool pair, or () if unknown.

    Always a tuple, so callers cannot forget the ambiguous case and end up storing a bare
    string that means "seer" when the player only knows they were told they were one.
    """
    return ALIASES.get(normalise(text), ())


def suggest(text, limit=3):
    """Display names closest to unrecognised input, for a "did you mean" reply.

    Matching happens on the normalised keys — the alias table's own vocabulary — so a
    near-miss on a short code ("bl" for the Blacksmith) is as findable as one on a full
    name. Returns display names in best-first order, de-duplicated: several keys map to
    one role, and offering "Blacksmith, Blacksmith, Blacksmith" reads as a bug.
    """
    key = normalise(text)
    if not key:
        return []
    names = []
    for match in difflib.get_close_matches(key, ALIASES.keys(), n=limit * 3, cutoff=0.6):
        for role_id in ALIASES[match]:
            name = ROLES[role_id]["name"]
            if name not in names:
                names.append(name)
        if len(names) >= limit:
            break
    return names[:limit]


def display(role_id):
    """A role as players see it: "Alpha Wolf ⚡️". Falls back to the raw id if unknown.

    Never raises. Session state is persisted and reloaded, so a payload written before a
    role id was renamed must still render something rather than take the message down.
    """
    role = ROLES.get(role_id)
    if role is None:
        return role_id
    return "{} {}".format(role["name"], role["emoji"])


def team_of(role_id):
    """The role's team, or None if the id is unknown."""
    role = ROLES.get(role_id)
    return None if role is None else role["team"]


def has_tag(role_id, tag):
    """Whether a role carries a tag. False for unknown ids rather than raising."""
    role = ROLES.get(role_id)
    return role is not None and tag in role["tags"]


def with_tag(tag):
    """Every role id carrying `tag`, in registry order."""
    return tuple(role_id for role_id, role in ROLES.items() if tag in role["tags"])


def in_team(team):
    """Every role id on `team`, in registry order."""
    return tuple(role_id for role_id, role in ROLES.items() if role["team"] == team)
