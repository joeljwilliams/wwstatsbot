"""Evaluating rules against a role composition.

Three things are being pinned, in rising order of how badly they fail:

1. **The derived counts**, which encode game mechanics the expressions rely on. Get
   `max_possible_cultists()` wrong and "Cultist Convention" is either never offered or
   offered in a game with no cult.
2. **The sandbox**, because rule expressions come out of the database and are editable at
   runtime. A bad one must be inert, not fatal, and never reach anything but the counts.
3. **That every canonical expression actually runs.** Until this file existed the 109
   expressions in rulelist.py had been stored and never once evaluated — a typo in any of
   them would have surfaced as an achievement silently missing from a live game.
"""

import feasibility
import roles
import rulelist
from rulelist import RULES

# db.get_rules() shape: name -> rule. The rulelist entries carry an extra "name" key, which
# nothing reads, so the catalogue can be used directly as the rule source here.
CATALOGUE = {r["name"]: r for r in RULES}


def comp(*role_ids):
    """A composition of one role per player, in the order given."""
    return feasibility.Composition([(role_id,) for role_id in role_ids])


# --- Counting --------------------------------------------------------------


def test_counts_are_by_player_not_by_role():
    c = comp("werewolf", "werewolf", "villager")
    assert c.players == 3
    assert c.count("werewolf") == 2
    assert c.count("villager") == 1
    assert c.count("seer") == 0


def test_an_unresolved_seer_fool_counts_as_both():
    """One player, two role questions — the optimistic reading the list exists to give."""
    c = feasibility.Composition([roles.SEER_FOOL, ("villager",)])
    assert c.players == 2
    assert c.count("seer") == 1
    assert c.count("fool") == 1
    assert c.present("seer") and c.present("fool")


def test_count_accepts_several_names():
    c = comp("hunter", "gunner", "villager")
    assert c.count("hunter", "gunner") == 2


def test_present_and_all_present():
    c = comp("seer", "beholder")
    assert c.present("seer")
    assert c.present("seer", "tanner")
    assert c.all_present("seer", "beholder")
    assert not c.all_present("seer", "tanner")


def test_team_and_tag_counts():
    c = comp("werewolf", "sorcerer", "villager", "cultist")
    assert c.count_team(roles.WOLF) == 2
    assert c.count_tag(roles.PACK) == 1, "the Sorcerer is wolf-team but not pack"
    assert c.count_team(roles.CULT) == 1


# --- Reachable ceilings ----------------------------------------------------


def test_pack_count_excludes_the_sorcerer_and_the_traitor():
    """ "Is a wolf attack possible tonight" is a pack question, not a team one."""
    c = comp("sorcerer", "traitor", "villager")
    assert c.count_team(roles.WOLF) == 1
    assert c.count_tag(roles.PACK) == 0


def test_max_possible_wolves_counts_the_roles_that_turn():
    c = comp("werewolf", "cursed", "wild_child", "traitor", "villager")
    assert c.max_possible_wolves() == 4


def test_an_alpha_lifts_the_wolf_ceiling_to_everybody():
    """The bite turns whoever the pack eats, so no other role caps the count."""
    c = comp("alpha_wolf", "villager", "villager", "seer")
    assert c.max_possible_wolves() == 4


def test_the_wolf_ceiling_never_exceeds_the_player_count():
    c = comp("werewolf", "cursed")
    assert c.max_possible_wolves() == 2


def test_cultable_count_excludes_the_immune():
    """Wolves, the serial killer, the thief, the doppelganger and the cult hunter."""
    c = comp("cultist", "villager", "werewolf", "serial_killer", "cultist_hunter")
    assert c.cultable_count() == 2, "only the cultist and the villager can be converted"


def test_the_sorcerer_can_be_converted():
    """Wolf-team, but not a wolf — only actual wolves are immune."""
    c = comp("cultist", "sorcerer")
    assert c.cultable_count() == 2


def test_no_cultist_means_no_cult():
    assert comp("villager", "seer").max_possible_cultists() == 0
    assert comp("cultist", "villager", "seer").max_possible_cultists() == 3


def test_the_barkeep_manufactures_drunks_from_lowly_villagers():
    """Three dealt Drunks is not the only way to reach three drunks."""
    assert comp("barkeep", "villager", "villager", "villager").max_possible_drunks() == 3
    assert comp("barkeep", "seer", "hunter").max_possible_drunks() == 0, "the village team does not drink"
    assert comp("drunk", "villager", "villager").max_possible_drunks() == 1, "no bar, no extra drunks"


def test_attackers_are_wolves_potential_wolves_and_cultists():
    c = comp("werewolf", "cursed", "cultist", "sorcerer", "villager")
    assert c.attackers() == 3, "the Sorcerer attacks nobody"


def test_burnable_houses_exclude_the_arsonists_own_and_the_serial_killers():
    assert comp(*(["arsonist"] + ["villager"] * 10)).max_burnable_houses() == 10
    assert comp(*(["arsonist", "serial_killer"] + ["villager"] * 9)).max_burnable_houses() == 9


def test_burnable_houses_never_goes_negative():
    assert feasibility.Composition(()).max_burnable_houses() == 0
    assert comp("arsonist").max_burnable_houses() == 0


# --- Reachable roles -------------------------------------------------------


def test_a_reported_role_is_always_reachable():
    c = comp("seer", "villager")
    assert "seer" in feasibility.reachable_roles(("seer",), c)


def test_the_cursed_can_reach_the_wolf_achievements():
    """Without this a Cursed player's list is nearly empty, which is the wrong answer."""
    c = comp("cursed", "werewolf", "villager")
    reachable = feasibility.reachable_roles(("cursed",), c)
    assert "werewolf" in reachable


def test_turning_makes_a_plain_wolf_never_an_alpha():
    c = comp("cursed", "alpha_wolf", "villager")
    assert "alpha_wolf" not in feasibility.reachable_roles(("cursed",), c)


def test_an_alpha_puts_every_player_within_reach_of_the_pack():
    c = comp("alpha_wolf", "villager", "seer")
    assert "werewolf" in feasibility.reachable_roles(("seer",), c)


def test_without_an_alpha_an_ordinary_villager_stays_put():
    c = comp("werewolf", "villager", "seer")
    assert feasibility.reachable_roles(("seer",), c) == {"seer"}


def test_the_doppelganger_can_reach_anything_in_the_game():
    c = comp("doppelganger", "seer", "serial_killer")
    reachable = feasibility.reachable_roles(("doppelganger",), c)
    assert {"seer", "serial_killer"} <= reachable


def test_the_thief_cannot_reach_wolves_the_serial_killer_or_cultists():
    c = comp("thief", "seer", "werewolf", "serial_killer", "cultist", "arsonist", "sorcerer")
    reachable = feasibility.reachable_roles(("thief",), c)
    assert "seer" in reachable
    assert "arsonist" in reachable, "the Arsonist can be robbed"
    assert "sorcerer" in reachable, "the Sorcerer can be robbed"
    for immune in ("werewolf", "serial_killer", "cultist"):
        assert immune not in reachable, immune


def test_a_villager_can_reach_the_drunk_through_the_bar():
    assert "drunk" in feasibility.reachable_roles(("villager",), comp("barkeep", "villager"))
    assert "drunk" not in feasibility.reachable_roles(("villager",), comp("seer", "villager"))


# --- The sandbox -----------------------------------------------------------


def test_a_true_expression_evaluates():
    assert feasibility.evaluate("players >= 2", comp("seer", "villager")) is True
    assert feasibility.evaluate("players >= 3", comp("seer", "villager")) is False


def test_the_registered_vocabulary_is_callable():
    c = comp("alpha_wolf", "drunk", "villager")
    assert feasibility.evaluate("max_possible_drunks() >= 1", c)
    assert feasibility.evaluate("pack_count() > 0 and ispresent('drunk')", c)


def test_imports_and_attribute_access_are_refused():
    """These expressions are editable at runtime; the sandbox is the only thing between
    a rule and the process."""
    c = comp("villager")
    for hostile in (
        "__import__('os').system('true')",
        "().__class__",
        "open('/etc/passwd')",
        "players.__class__.__mro__",
    ):
        assert feasibility.evaluate(hostile, c) is False, hostile


def test_a_broken_expression_is_inert_rather_than_fatal():
    """One bad rule must not cost the other 108."""
    assert feasibility.evaluate("count(", comp("villager")) is False
    assert feasibility.evaluate("no_such_function()", comp("villager")) is False
    assert feasibility.evaluate("1 / 0", comp("villager")) is False


def test_validate_accepts_the_vocabulary_and_rejects_nonsense():
    assert feasibility.validate("players >= 5")[0] is True
    assert feasibility.validate("max_possible_wolves() >= 3")[0] is True

    ok, message = feasibility.validate("no_such_function()")
    assert ok is False
    assert "no_such_function" in message


def test_validate_catches_an_expression_that_only_breaks_on_an_empty_game():
    """The probe includes a game with no players, which is where division blows up."""
    ok, _ = feasibility.validate("100 / players > 1")
    assert ok is False


# --- Every canonical expression --------------------------------------------


def test_every_catalogue_expression_is_valid():
    """The first time these 109 expressions have ever been run.

    Stored-but-never-evaluated is the failure this catches: a typo would otherwise show up
    as an achievement quietly missing from a live game.
    """
    for rule in RULES:
        ok, message = feasibility.validate(rule["expr"])
        assert ok, "{}: {} -- {}".format(rule["name"], rule["expr"], message)


# The kitchen sink is *one player per role*: 44 players, every role present, none of them
# twice. Three rules cannot fire there, and none of them is a defect — they want a game
# shaped differently rather than a game with more in it. Listing them explicitly is what
# keeps the check below meaningful: anything *else* that cannot fire with all 44 roles
# present is unreachable by anybody, in any game.
NOT_SATISFIED_BY_A_FULL_GAME = {
    "Introvert": "wants exactly 5 players; the sink has 44",
    "Lone Wolf": "wants exactly one wolf-team player; the sink has six",
    "Liquid Business": "wants three lowly villagers; the sink has one of everything",
}


def test_every_listed_rule_can_pass_in_a_game_containing_every_role():
    """A rule that cannot fire even with all 44 roles present is unreachable by anyone."""
    sink = feasibility.Composition([(role_id,) for role_id in roles.ROLES])
    for rule in RULES:
        if rule["tier"] == rulelist.SKIP or rule["name"] in NOT_SATISFIED_BY_A_FULL_GAME:
            continue
        assert feasibility.evaluate(rule["expr"], sink), "{}: {}".format(rule["name"], rule["expr"])


def test_the_exempted_rules_really_are_unsatisfiable_there():
    """Guard the exemption list: an entry that *would* pass is a stale excuse."""
    sink = feasibility.Composition([(role_id,) for role_id in roles.ROLES])
    for name in NOT_SATISFIED_BY_A_FULL_GAME:
        assert not feasibility.evaluate(CATALOGUE[name]["expr"], sink), name


def test_an_empty_game_lists_nothing_for_anybody():
    """Composition-level rules with no gate still "pass" with no players — what matters is
    that nothing is rendered, because there is nobody to render it under."""
    per_player, universal = feasibility.feasible({}, CATALOGUE)
    assert per_player == {}
    assert universal, "the always-achievements are still true of the game itself"


def test_skipped_rules_never_pass():
    sink = feasibility.Composition([(role_id,) for role_id in roles.ROLES])
    passing = feasibility.passing_rules(sink, CATALOGUE)
    for name, rule in CATALOGUE.items():
        if rule["tier"] == rulelist.SKIP:
            assert name not in passing, name


# --- End to end ------------------------------------------------------------


def names_for(per_player, key):
    return {entry["name"] for entry in per_player[key]}


def test_an_achievement_is_listed_only_under_its_subject():
    """Cold as Ice belongs to the Snow Wolf, and only when there is a harlot to freeze."""
    per_player, _ = feasibility.feasible(
        {"wolf": ("snow_wolf",), "harlot": ("harlot",), "other": ("villager",)}, CATALOGUE
    )
    assert "Cold as Ice" in names_for(per_player, "wolf")
    assert "Cold as Ice" not in names_for(per_player, "harlot")
    assert "Cold as Ice" not in names_for(per_player, "other")


def test_the_same_achievement_disappears_without_its_condition():
    per_player, _ = feasibility.feasible({"wolf": ("snow_wolf",), "other": ("villager",)}, CATALOGUE)
    assert "Cold as Ice" not in names_for(per_player, "wolf")


def test_universal_achievements_are_returned_once_not_per_player():
    per_player, universal = feasibility.feasible({"a": ("villager",), "b": ("seer",)}, CATALOGUE)
    assert "Welcome to Hell" in universal
    for key in per_player:
        assert "Welcome to Hell" not in names_for(per_player, key)


def test_a_swing_reachable_row_is_marked_as_such():
    """The Cursed sees the wolf rows, flagged so nobody reads them as available now."""
    per_player, _ = feasibility.feasible(
        {"cursed": ("cursed",), "wolf": ("werewolf",), "sorc": ("sorcerer",)}, CATALOGUE
    )
    cursed = {entry["name"]: entry for entry in per_player["cursed"]}
    assert "No Sorcery!" in cursed, "reachable once the wolves turn them"
    assert cursed["No Sorcery!"]["swing"] is True

    wolf = {entry["name"]: entry for entry in per_player["wolf"]}
    assert wolf["No Sorcery!"]["swing"] is False, "the wolf can do it now"


def test_an_unresolved_seer_fool_gets_both_roles_achievements():
    per_player, _ = feasibility.feasible({"unsure": roles.SEER_FOOL, "bh": ("beholder",)}, CATALOGUE)
    listed = names_for(per_player, "unsure")
    assert "Should Have Known" in listed, "the Seer's"
    assert "Am I Your Seer?" in listed, "the Fool's"


def test_entries_keep_the_catalogue_order():
    """Rendered lists should read in /achievements order, not alphabetically."""
    per_player, _ = feasibility.feasible({"a": ("hunter",)}, CATALOGUE)
    listed = [entry["name"] for entry in per_player["a"]]
    order = [r["name"] for r in RULES]
    assert listed == sorted(listed, key=order.index)
