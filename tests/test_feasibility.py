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

from wwstatsbot.data import rulelist
from wwstatsbot.data.rulelist import RULES
from wwstatsbot.game import feasibility, roles

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


def test_a_game_with_nothing_to_start_a_pack_can_never_have_a_wolf():
    """The Cursed needs a bite and the Traitor needs wolves to have died; neither is a
    first wolf. Counting them as one offered the Angel a wolf to guard in a game with
    none."""
    assert comp("cursed", "traitor", "villager", "seer").max_possible_wolves() == 0
    assert comp("wild_child", "villager", "seer").max_possible_wolves() == 1, "the role model just dies"
    assert comp("doppelganger", "villager", "seer").max_possible_wolves() == 0, "nothing to copy"


def test_night_killers_exclude_the_roles_that_only_fire_by_day():
    assert comp("gunner", "hunter", "villager").night_killers() == 0
    assert comp("werewolf", "serial_killer", "gunner").night_killers() == 2


def test_distinct_visiting_roles_counts_roles_where_the_tag_count_counts_players():
    """Three werewolves are three visitors and one visiting role — the difference between
    "It Was a Busy Night!" and "Traffic Control"."""
    c = comp("werewolf", "werewolf", "werewolf", "harlot")
    assert c.count_tag(roles.VISITOR) == 4
    assert c.distinct_tagged_roles(roles.VISITOR) == 2


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


def test_the_cursed_reaches_nothing_in_a_game_that_can_have_no_wolf():
    """The other half of the same rule the wolf ceiling follows.

    Nobody bites the Cursed in a game dealt no pack and no Wild Child, so listing the
    wolves' achievements under them said a game with no wolves had one.
    """
    c = comp("cursed", "traitor", "villager", "seer")
    assert c.max_possible_wolves() == 0
    assert feasibility.reachable_roles(("cursed",), c) == {"cursed"}


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


def test_a_thief_puts_the_stealable_roles_within_everybodys_reach():
    """Not only the Thief's own. The theft moves an identity between two players, so the
    Barkeep who already holds Liquid Business is not the only one who might be the Barkeep
    by morning — and a list that said otherwise left fifteen players short of rows that
    were really on the table."""
    c = comp("thief", "barkeep", "villager", "seer", "werewolf")
    assert {"barkeep", "seer", "thief"} <= feasibility.reachable_roles(("villager",), c)
    assert {"villager", "seer", "thief"} <= feasibility.reachable_roles(("barkeep",), c)


def test_the_steal_immune_are_immune_in_both_directions():
    """Nothing is shuffled onto a wolf, a cultist or the serial killer, and nothing is
    shuffled off one: they cannot be robbed, so their seat is not part of the exchange."""
    c = comp("thief", "barkeep", "werewolf", "cultist", "serial_killer")
    for immune in ("werewolf", "cultist", "serial_killer"):
        assert feasibility.reachable_roles((immune,), c) == {immune}, immune
    assert "werewolf" not in feasibility.reachable_roles(("barkeep",), c)


def test_without_a_thief_nobody_is_shuffled_anywhere():
    c = comp("chef", "villager", "seer")
    assert feasibility.reachable_roles(("villager",), c) == {"villager"}


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
        if not rulelist.is_listed(rule) or rule["name"] in NOT_SATISFIED_BY_A_FULL_GAME:
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
    per_player, shared = feasibility.feasible({}, CATALOGUE)
    assert per_player == {}
    assert shared, "the roleless achievements are still true of the game itself"


def test_opted_out_rules_never_pass():
    sink = feasibility.Composition([(role_id,) for role_id in roles.ROLES])
    passing = feasibility.passing_rules(sink, CATALOGUE)
    for name, rule in CATALOGUE.items():
        if not rulelist.is_listed(rule):
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


def test_achievements_anyone_can_earn_are_returned_once_not_per_player():
    """Repeating a roleless achievement under each of sixteen players says the same thing
    sixteen times and crowds out the rows that are actually about that player."""
    game = {"a": ("villager",), "b": ("seer",), "c": ("werewolf",), "d": ("serial_killer",)}
    per_player, shared = feasibility.feasible(game, CATALOGUE)
    names = {entry["name"] for entry in shared}

    assert "Welcome to Hell" in names, "no role gate at all"
    assert "Sunday Bloody Sunday" in names, "gated on the composition, but on nobody's role"
    for key in per_player:
        assert not names_for(per_player, key) & names


def test_shared_entries_carry_a_name_and_nothing_else():
    """No tier and no swing: an achievement belonging to no role cannot be reached by
    changing role, and the catalogue no longer grades how likely any of it is."""
    _, shared = feasibility.feasible({"a": ("villager",), "b": ("tanner",), "c": ("cupid",)}, CATALOGUE)
    names = {entry["name"] for entry in shared}
    assert {"Welcome to Hell", "Romeo and Juliet"} <= names
    assert all(set(entry) == {"name"} for entry in shared), shared


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


# --- Regression: the game that missed "I Helped!" ---------------------------

# A real eight-player game. It was dealt exactly one pack member — the Wolf Cub — with the
# second wolf arriving only when the Traitor turned, which happens the moment the cub dies.
# That is precisely when "I Helped!" fires ("the alive pack has 2 successful eat attempts
# after you die"), and counting the pack as *dealt* meant it was never offered.
GAME_THAT_MISSED_I_HELPED = [
    "doppelganger",
    "sorcerer",
    "serial_killer",
    "drunk",
    "wolfman",
    "traitor",
    "wolf_cub",
    "harlot",
]


def test_i_helped_is_offered_when_the_second_wolf_can_only_arrive_by_turning():
    composition = feasibility.Composition([(role,) for role in GAME_THAT_MISSED_I_HELPED])
    assert composition.count_tag(roles.PACK) == 1, "the cub was the whole pack"

    per_player, _ = feasibility.feasible(
        {uid: (role,) for uid, role in enumerate(GAME_THAT_MISSED_I_HELPED)}, CATALOGUE
    )
    cub = GAME_THAT_MISSED_I_HELPED.index("wolf_cub")
    assert "I Helped!" in {entry["name"] for entry in per_player[cub]}


def test_i_helped_is_not_offered_to_a_cub_with_no_possible_second_wolf():
    """A lone cub in a game with nothing that can turn has no pack to outlive it."""
    lone = ["wolf_cub", "villager", "seer", "harlot"]
    per_player, _ = feasibility.feasible({uid: (role,) for uid, role in enumerate(lone)}, CATALOGUE)
    assert "I Helped!" not in {entry["name"] for entry in per_player[0]}


# --- What the game has already decided -------------------------------------
#
# The third question, after "can this game produce it" and "whose role is it": has a
# choice already been made that closes it. Cupid's couple and the Wild Child's role model
# are both made mid-game, and until these existed a post went on offering "be in love with
# the tanner" to eighteen players who provably could not be.


def facts(**players):
    """Session facts for the players named, each given the full shape."""
    return {
        key: {"lover": entry.get("lover", False), "partner": entry.get("partner"), "model": entry.get("model")}
        for key, entry in players.items()
    }


LOVE_GAME = {"cupid": ("cupid",), "tanner": ("tanner",), "harlot": ("harlot",), "wolf": ("werewolf",)}


def test_nothing_known_answers_exactly_as_it_did_before_facts_existed():
    """The whole feature has to be invisible in a session that was told nothing."""
    with_none, shared_none = feasibility.feasible(LOVE_GAME, CATALOGUE)
    with_empty, shared_empty = feasibility.feasible(LOVE_GAME, CATALOGUE, {})
    assert with_none == with_empty
    assert shared_none == shared_empty
    assert "Affectionate" in names_for(with_none, "harlot")


def test_one_lover_does_not_close_the_question():
    """`/love` takes a bare player, so one name says nothing about who the other half is."""
    one = facts(tanner={"lover": True})
    per_player, _ = feasibility.feasible(LOVE_GAME, CATALOGUE, one)
    assert "Affectionate" in names_for(per_player, "harlot"), "the harlot may still be the other half"


def test_a_named_couple_takes_the_lover_achievements_off_everybody_else():
    couple = facts(tanner={"lover": True, "partner": "wolf"}, wolf={"lover": True, "partner": "tanner"})
    per_player, _ = feasibility.feasible(LOVE_GAME, CATALOGUE, couple)
    assert "Affectionate" not in names_for(per_player, "harlot"), "the harlot is not in the couple"
    assert "Self Loving" not in names_for(per_player, "cupid"), "Cupid picked somebody else"
    assert "Should've Said Something" in names_for(per_player, "wolf"), "the wolf is half of it"


def test_a_shared_row_moves_under_the_couple_rather_than_disappearing():
    """ "Anyone can earn this" stops being true the moment the game names two people.

    Romeo and Juliet has no role gate at all, so it is summarised once at the foot of the
    post. A couple makes it a fact about two players instead, and the post has somewhere
    better to say it.
    """
    open_game, shared = feasibility.feasible(LOVE_GAME, CATALOGUE)
    assert "Romeo and Juliet" in {entry["name"] for entry in shared}
    assert "Romeo and Juliet" not in names_for(open_game, "harlot")

    couple = facts(tanner={"lover": True, "partner": "wolf"}, wolf={"lover": True, "partner": "tanner"})
    per_player, shared = feasibility.feasible(LOVE_GAME, CATALOGUE, couple)
    assert "Romeo and Juliet" not in {entry["name"] for entry in shared}
    assert "Romeo and Juliet" in names_for(per_player, "wolf")
    assert "Romeo and Juliet" not in names_for(per_player, "harlot")


# A Thief is in it so that the plain roles are subject-matched too: a Thief at the table
# puts every stealable role within reach of everybody holding one, which is what makes
# Indestructible a row on the Villager's list at all. Without one there would be nothing
# for the model gate to narrow.
MODEL_GAME = {
    "dg": ("doppelganger",),
    "wc": ("wild_child",),
    "vil": ("villager",),
    "seer": ("seer",),
    "th": ("thief",),
}


def test_a_role_model_nobody_has_finished_choosing_narrows_nothing():
    """One of the pair still to choose means the next model could be anybody."""
    half = facts(dg={"model": "vil"})
    per_player, _ = feasibility.feasible(MODEL_GAME, CATALOGUE, half)
    assert "Indestructible" in names_for(per_player, "seer"), "the Wild Child may still point here"


def test_once_every_model_is_chosen_only_the_players_pointed_at_can_be_their_own():
    chosen = facts(dg={"model": "vil"}, wc={"model": "dg"})
    per_player, _ = feasibility.feasible(MODEL_GAME, CATALOGUE, chosen)
    assert "Indestructible" in names_for(per_player, "vil"), "the Doppelgänger points here"
    assert "Indestructible" in names_for(per_player, "dg"), "and the Wild Child here"
    assert "Indestructible" not in names_for(per_player, "wc"), "nobody is pointing at the Wild Child"
    assert "Indestructible" not in names_for(per_player, "seer")
    assert "Indestructible" not in names_for(per_player, "th"), "not even the Thief who could take the role"


def test_a_game_with_no_models_at_all_is_not_narrowed_to_nobody():
    """ "Every Doppelgänger has chosen" is vacuously true with none in the game.

    Answering yes there would take Indestructible off the whole table in a game a Thief
    could still put somebody into the role.
    """
    thief_game = {"th": ("thief",), "dg": ("doppelganger",), "wc": ("wild_child",)}
    per_player, _ = feasibility.feasible(thief_game, CATALOGUE, facts(th={"lover": True}))
    assert "Indestructible" in names_for(per_player, "th")


def test_a_doppelganger_who_pointed_somewhere_other_than_their_lover_loses_deep_love():
    game = {"dg": ("doppelganger",), "cu": ("cupid",), "a": ("villager",), "b": ("seer",)}
    pointing_at_the_partner = facts(
        dg={"lover": True, "partner": "a", "model": "a"}, a={"lover": True, "partner": "dg"}
    )
    per_player, _ = feasibility.feasible(game, CATALOGUE, pointing_at_the_partner)
    assert "Deep Love" in names_for(per_player, "dg")

    pointing_elsewhere = facts(dg={"lover": True, "partner": "a", "model": "b"}, a={"lover": True, "partner": "dg"})
    per_player, _ = feasibility.feasible(game, CATALOGUE, pointing_elsewhere)
    assert "Deep Love" not in names_for(per_player, "dg"), "that choice is spent"


# --- The sandbox, one player at a time -------------------------------------


def test_a_broken_player_expression_fails_open():
    """The opposite of `evaluate`, and deliberately.

    A composition expression that blows up drops one achievement. A player gate only ever
    narrows a row two other checks have already agreed on, so a broken one must leave the
    answer the bot gave before the gate existed rather than hiding the row from everybody.
    """
    composition = comp("villager")
    known = feasibility.Facts({}, {})
    assert feasibility.evaluate_for_player("may_love(", composition, known, "a") is True
    assert feasibility.evaluate_for_player("no_such_function()", composition, known, "a") is True


def test_a_player_expression_may_also_ask_about_the_composition():
    """One expression rather than two fields that would then have to agree."""
    composition = comp("cupid", "tanner")
    known = feasibility.Facts({}, {})
    assert feasibility.evaluate_for_player("may_love() and ispresent('cupid')", composition, known, "a") is True
    assert feasibility.evaluate_for_player("may_love() and ispresent('seer')", composition, known, "a") is False


def test_validate_player_accepts_the_vocabulary_and_rejects_nonsense():
    assert feasibility.validate_player("may_love()")[0] is True
    assert feasibility.validate_player("may_be_own_model() and players >= 4")[0] is True

    ok, message = feasibility.validate_player("no_such_function()")
    assert ok is False
    assert "no_such_function" in message


def test_validate_player_probes_both_branches_of_a_may():
    """A player nothing is known about takes the other branch, so one probe is not enough."""
    ok, _ = feasibility.validate_player("is_lover() and no_such_function()")
    assert ok is False


def test_every_catalogue_player_expression_is_valid():
    """The same guard the composition expressions get: stored-but-never-run is the failure."""
    for rule in RULES:
        gate = rule.get("player_expr", "")
        if not gate:
            continue
        ok, message = feasibility.validate_player(gate)
        assert ok, "{}: {} -- {}".format(rule["name"], gate, message)
