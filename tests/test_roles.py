"""The role registry's contract with the game and with the people typing into it.

Three things are pinned here, each of which fails silently in production if it drifts:

1. **Every `/about` code resolves.** Those are the names the game bot itself publishes, so
   they are the ones players have in their fingers. A code that stops resolving means a
   player mid-game gets "did you mean" instead of a recorded role.
2. **No alias claims two roles.** The index is a plain dict, so a duplicate key silently
   resolves to whichever role was defined last — no error, just the wrong role recorded
   for the rest of the game.
3. **The taxonomy's internal invariants**, e.g. every pack member is on the wolf team and
   is immune to conversion. These encode the reasoning the feasibility rules depend on;
   breaking one makes a rule wrong somewhere far away from the edit that caused it.
"""

import roles

# The game bot's own /rolelist, verbatim: the /about suffix and the role it names. This is
# the authoritative vocabulary — 44 roles — and the reason the table is spelled out rather
# than derived from ROLES is that deriving it would test nothing at all.
ABOUT_CODES = {
    "VG": "villager",
    "WW": "werewolf",
    "Drunk": "drunk",
    "Seer": "seer",
    "Cursed": "cursed",
    "Harlot": "harlot",
    "BH": "beholder",
    "Gunner": "gunner",
    "Traitor": "traitor",
    "GA": "guardian_angel",
    "Detective": "detective",
    "AppS": "apprentice_seer",
    "Cult": "cultist",
    "CH": "cultist_hunter",
    "WC": "wild_child",
    "Fool": "fool",
    "Mason": "mason",
    "DG": "doppelganger",
    "Cupid": "cupid",
    "Hunter": "hunter",
    "SK": "serial_killer",
    "Tanner": "tanner",
    "Mayor": "mayor",
    "Prince": "prince",
    "Sorcerer": "sorcerer",
    "Clumsy": "clumsy",
    "Blacksmith": "blacksmith",
    "AlphaWolf": "alpha_wolf",
    "WolfCub": "wolf_cub",
    "Sandman": "sandman",
    "Oracle": "oracle",
    "WolfMan": "wolfman",
    "Lycan": "lycan",
    "Pacifist": "pacifist",
    "WiseElder": "wise_elder",
    "Thief": "thief",
    "Troublemaker": "troublemaker",
    "Chemist": "chemist",
    "SnowWolf": "snow_wolf",
    "GraveDigger": "grave_digger",
    "Arsonist": "arsonist",
    "Augur": "augur",
    "Chef": "chef",
    "Barkeep": "barkeep",
}


# --- The vocabulary --------------------------------------------------------


def test_every_about_code_resolves():
    for code, expected in ABOUT_CODES.items():
        assert roles.resolve(code) == (expected,), code


def test_registry_covers_exactly_the_published_roles():
    """No role invented, none missed. 44 is the game's count, not ours."""
    assert set(ROLE_IDS := set(roles.ROLES)) == set(ABOUT_CODES.values())
    assert len(ROLE_IDS) == 44


def test_every_display_name_resolves_to_itself():
    for role_id, role in roles.ROLES.items():
        assert roles.resolve(role["name"]) == (role_id,), role["name"]


def test_role_ids_resolve():
    """The stored form has to round-trip: session state holds ids, not typed text."""
    for role_id in roles.ROLES:
        assert roles.resolve(role_id) == (role_id,)


def test_no_alias_claims_two_roles():
    """A duplicate key would resolve to whichever role was defined last, silently."""
    claims = {}
    for role_id, role in roles.ROLES.items():
        for spelling in (role_id, role["name"]) + tuple(role["aliases"]):
            key = roles.normalise(spelling)
            assert key not in claims or claims[key] == role_id, "{!r} claimed by {} and {}".format(
                key, claims.get(key), role_id
            )
            claims[key] = role_id


def test_shorthand_seen_in_play_resolves():
    """`bk` came from a real /role in the group — it is not an /about code."""
    assert roles.resolve("bk") == ("barkeep",)
    assert roles.resolve("alpha") == ("alpha_wolf",)
    assert roles.resolve("wolf") == ("werewolf",)
    assert roles.resolve("elder") == ("wise_elder",)


# --- Normalisation ---------------------------------------------------------


def test_normalise_folds_case_spacing_and_punctuation():
    for spelling in ("Alpha Wolf", "alpha wolf", "ALPHAWOLF", "alpha-wolf", " Alpha  Wolf "):
        assert roles.resolve(spelling) == ("alpha_wolf",), spelling


def test_accents_fold_to_the_ascii_spelling():
    """Nobody types "Doppelgänger" on a phone mid-game."""
    assert roles.resolve("Doppelgänger") == ("doppelganger",)
    assert roles.resolve("doppelganger") == ("doppelganger",)


def test_unknown_input_resolves_to_nothing():
    for text in ("", None, "!!!", "bartender"):
        assert roles.resolve(text) == ()


# --- The Seer/Fool pair ----------------------------------------------------


def test_seer_fool_resolves_to_both():
    """A player told they are the Seer cannot know they are not the Fool."""
    for spelling in ("sf", "s/f", "seerfool", "SF"):
        assert roles.resolve(spelling) == roles.SEER_FOOL, spelling
    assert set(roles.SEER_FOOL) == {"seer", "fool"}


def test_seer_and_fool_still_resolve_alone():
    assert roles.resolve("seer") == ("seer",)
    assert roles.resolve("fool") == ("fool",)


# --- Did you mean ----------------------------------------------------------


def test_suggest_finds_the_near_miss():
    assert "Blacksmith" in roles.suggest("blacksmit")
    assert "Barkeep" in roles.suggest("barkep")


def test_suggest_does_not_repeat_a_role():
    """Several keys map to one role; offering it three times reads as a bug."""
    for text in ("alpha wolfe", "doppelgangr", "serial killa"):
        names = roles.suggest(text)
        assert len(names) == len(set(names)), text


def test_suggest_on_nothing_is_empty():
    assert roles.suggest("") == []


# --- Display ---------------------------------------------------------------


def test_display_pairs_name_and_emoji():
    assert roles.display("alpha_wolf") == "Alpha Wolf ⚡️"
    assert roles.display("barkeep") == "Barkeep 🍸"


def test_display_of_an_unknown_id_falls_back_rather_than_raising():
    """Session state is persisted; a payload written before a rename must still render."""
    assert roles.display("no_such_role") == "no_such_role"


# --- Taxonomy invariants ---------------------------------------------------


def test_every_role_is_on_a_known_team():
    for role_id, role in roles.ROLES.items():
        assert role["team"] in roles.TEAMS, role_id


def test_the_pack_is_a_subset_of_the_wolf_team():
    for role_id in roles.with_tag(roles.PACK):
        assert roles.team_of(role_id) == roles.WOLF, role_id


def test_the_pack_is_exactly_the_roles_that_eat():
    """Sorcerer is wolf-team and must not be in it; Snow Wolf and Wolf Cub must."""
    assert set(roles.with_tag(roles.PACK)) == {"werewolf", "alpha_wolf", "wolf_cub", "snow_wolf", "lycan"}
    assert not roles.has_tag("sorcerer", roles.PACK)


def test_snow_wolf_and_wolf_cub_are_pack_but_not_killers():
    """The Snow Wolf freezes and the Cub's death grants the eat — neither kills directly."""
    for role_id in ("snow_wolf", "wolf_cub"):
        assert roles.has_tag(role_id, roles.PACK)
        assert not roles.has_tag(role_id, roles.KILLER)


def test_potential_wolves_start_on_the_village_team():
    """Traitor, Cursed and Wild Child win with the wolves eventually, not tonight."""
    assert set(roles.with_tag(roles.POTENTIAL_WOLF)) == {"traitor", "cursed", "wild_child"}
    for role_id in roles.with_tag(roles.POTENTIAL_WOLF):
        assert roles.team_of(role_id) == roles.VILLAGE, role_id


def test_wolves_can_be_neither_converted_nor_robbed():
    for role_id in roles.with_tag(roles.PACK):
        assert roles.has_tag(role_id, roles.CULT_IMMUNE), role_id
        assert roles.has_tag(role_id, roles.STEAL_IMMUNE), role_id


def test_the_sorcerer_is_wolf_team_but_unprotected():
    """Only actual wolves are immune — the Sorcerer can be converted and can be robbed."""
    assert roles.team_of("sorcerer") == roles.WOLF
    assert not roles.has_tag("sorcerer", roles.CULT_IMMUNE)
    assert not roles.has_tag("sorcerer", roles.STEAL_IMMUNE)


def test_the_arsonist_can_be_robbed_but_not_converted():
    assert roles.has_tag("arsonist", roles.CULT_IMMUNE)
    assert not roles.has_tag("arsonist", roles.STEAL_IMMUNE)


def test_steal_immunity_is_wolves_the_serial_killer_and_the_cult():
    assert set(roles.with_tag(roles.STEAL_IMMUNE)) == set(roles.with_tag(roles.PACK)) | {
        "serial_killer",
        "cultist",
    }


def test_the_wolfman_is_a_villager_the_seer_misreads():
    """Wolf in name only. Counting it as one inflates every wolf total in the game."""
    assert roles.team_of("wolfman") == roles.VILLAGE
    assert not roles.has_tag("wolfman", roles.PACK)
    assert not roles.has_tag("wolfman", roles.ROLE_SWING)


def test_the_lycan_is_a_real_wolf_the_seer_misreads():
    """The mirror of the WolfMan, and the one that actually counts."""
    assert roles.team_of("lycan") == roles.WOLF
    assert roles.has_tag("lycan", roles.PACK)


def test_the_fool_is_a_villager_and_not_a_bad_role():
    assert roles.team_of("fool") == roles.VILLAGE
    assert not roles.has_tag("fool", roles.BAD)


def test_the_barkeep_is_visited_not_a_visitor():
    """The villagers come to the bar; tagging it a visitor inflates Busy Night."""
    assert roles.has_tag("barkeep", roles.VISITED)
    assert not roles.has_tag("barkeep", roles.VISITOR)


# Every role that leaves home at night. VISITOR drives three achievements (It Was a Busy
# Night!, Traffic Control, Food Waste), all of which count *distinct roles that visited
# you*, so a wrong entry here is an achievement offered in a game where it cannot happen —
# or withheld from one where it can.
#
# The Chemist is confirmed part of this list and is worth spelling out, because it was
# missing from the first pass: the role text is explicit ("Good luck when they visit
# you!"), and two achievements are built on the visit — "Lucky Night" (survive the
# chemist's visit and the harlot's in one night) and "At least you tried..." (the guardian
# angel saves someone who then dies to the chemist's poison). Neither can happen if the
# Chemist stays home.
VISITING_ROLES = {
    "chemist",
    "werewolf",
    "alpha_wolf",
    "wolf_cub",
    "snow_wolf",
    "lycan",
    "harlot",
    "guardian_angel",
    "cultist",
    "cultist_hunter",
    "serial_killer",
    "thief",
    "arsonist",
}


def test_the_visiting_roles_are_the_games_own_list():
    assert set(roles.with_tag(roles.VISITOR)) == VISITING_ROLES


def test_role_swing_is_the_roles_whose_identity_changes():
    assert set(roles.with_tag(roles.ROLE_SWING)) == {
        "cursed",
        "wild_child",
        "traitor",
        "doppelganger",
        "thief",
    }


def test_the_tanner_is_solo_without_being_a_killer():
    """LONER and BAD are separate tags precisely because of this role."""
    assert roles.has_tag("tanner", roles.LONER)
    assert not roles.has_tag("tanner", roles.KILLER)


def test_in_team_and_with_tag_agree_with_the_registry():
    assert set(roles.in_team(roles.CULT)) == {"cultist"}
    assert set(roles.in_team(roles.SOLO)) == {"serial_killer", "arsonist", "tanner", "doppelganger", "thief"}
    for role_id in roles.in_team(roles.VILLAGE):
        assert roles.team_of(role_id) == roles.VILLAGE
