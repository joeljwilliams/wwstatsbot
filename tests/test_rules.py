"""The rule catalogue's contract with the achievement list.

This is `test_templates.py`'s job done for rules: a **two-way** check that every rule names
a real achievement and every achievement has a rule. Both directions matter, and the second
is the one that earns its keep — a rule naming a nonexistent achievement fails loudly at
startup (the foreign key rejects it), but an achievement with *no* rule fails silently and
forever: it is simply never offered to anyone, and nobody can tell the difference between
"impossible in this game" and "we forgot".

The earlier drafts of this catalogue missed five achievements and misspelled a sixth
("Demoted by Death" for "Demoted by the Death"). Nothing in the running bot would have
noticed either.

Skipped achievements count as covered — they carry an empty subject and a reason — so
opting one out is a visible decision in the catalogue rather than an omission.
"""

import pytest

from wwstatsbot.data import rulelist
from wwstatsbot.data.achvlist import ACHV
from wwstatsbot.data.rulelist import RULES
from wwstatsbot.game import roles

ACHIEVEMENT_NAMES = [a["name"] for a in ACHV]
RULE_NAMES = [r["name"] for r in RULES]


# --- The two-way drift guard -----------------------------------------------


def test_every_rule_names_a_real_achievement():
    """A rule for a nonexistent achievement is rejected by the foreign key at startup."""
    unknown = set(RULE_NAMES) - set(ACHIEVEMENT_NAMES)
    assert not unknown, "rules for achievements that do not exist: {}".format(sorted(unknown))


def test_every_achievement_has_a_rule():
    """The direction that fails silently: no rule means never listed, ever."""
    missing = set(ACHIEVEMENT_NAMES) - set(RULE_NAMES)
    assert not missing, "achievements with no rule (use _skip if deliberate): {}".format(sorted(missing))


def test_no_achievement_has_two_rules():
    """The table is keyed by achievement, so a duplicate would silently win on upsert."""
    duplicates = {name for name in RULE_NAMES if RULE_NAMES.count(name) > 1}
    assert not duplicates, sorted(duplicates)


def test_rules_are_in_achievement_order():
    """Keeps the catalogue diffable against achvlist.py, and the seeded table readable."""
    assert RULE_NAMES == ACHIEVEMENT_NAMES


# --- Field validity --------------------------------------------------------


@pytest.mark.parametrize("rule", RULES, ids=[r["name"] for r in RULES])
def test_rule_fields_are_well_formed(rule):
    assert rule["expr"], "expr must never be empty; use 'True' for no gate"
    assert rule["note"], "every rule carries its reasoning"
    assert isinstance(rule["subject"], str)
    # Empty is the ordinary case here, and the one that means "no gate" -- the opposite of
    # `expr`, where empty would be a rule that never runs.
    assert isinstance(rule["player_expr"], str)


def test_an_opted_out_rule_has_no_player_gate():
    """A gate on a rule nothing renders is a gate nobody will ever read.

    It would also be the first thing to mislead somebody turning the rule back on: the
    subject is the switch, and a player expression sitting behind an empty one looks like
    a decision that was made rather than one that was never reached.
    """
    for rule in RULES:
        if rulelist.is_listed(rule):
            continue
        assert rule["player_expr"] == "", rule["name"]


def test_the_love_achievements_are_gated_on_being_in_the_couple():
    """The composition can only see that a Cupid is playing; the couple is the other half.

    Listed out rather than derived, because the failure is an achievement *missing* from
    this list: one that reads "your lover" and is still offered to the whole table once
    the game has said who the lovers are.
    """
    for name in (
        "Forbidden Love",
        "Speed Dating",
        "Self Loving",
        "Should've Said Something",
        "OH SHI-",
        "Deep Love",
        "Seeing between Teams",
        "My Sweetie so Strong!",
        "Affectionate",
        "Romeo and Juliet",
    ):
        assert "may_love()" in _rule(name)["player_expr"], name


def test_double_shot_is_about_the_shooter_and_is_not_gated_on_love():
    """The Hunter shoots a couple; they are not in it. The one lover achievement that
    belongs to somebody standing outside the couple, and gating it would have hidden the
    row from the only player who can earn it."""
    assert _rule("Double Shot")["player_expr"] == ""
    assert "cupid" in _rule("Double Shot")["expr"]


@pytest.mark.parametrize("rule", RULES, ids=[r["name"] for r in RULES])
def test_every_subject_names_something_real(rule):
    """A typo'd role id silently makes an achievement unearnable by anyone."""
    for token in rule["subject"].split(","):
        token = token.strip()
        if not token or token == rulelist.ANY:
            continue
        if token.startswith(rulelist.TAG_PREFIX):
            tag = token[len(rulelist.TAG_PREFIX) :]
            assert roles.with_tag(tag), "no role carries tag {!r}".format(tag)
        elif token.startswith(rulelist.TEAM_PREFIX):
            team = token[len(rulelist.TEAM_PREFIX) :]
            assert team in roles.TEAMS, team
        else:
            assert token in roles.ROLES, "unknown role id {!r}".format(token)


def test_an_opted_out_rule_is_inert_both_ways():
    """The subject is the switch, and the expression must not disagree with it.

    An empty subject already stops a rule rendering, so `False` is belt and braces — but
    a rule that opted out with an expression still able to pass would be one edit away
    from being listed by accident.
    """
    for rule in RULES:
        if not rulelist.is_listed(rule):
            assert rule["expr"] == "False", rule["name"]
            assert rule["subject"] == "", rule["name"]


def test_a_listed_rule_is_one_with_a_subject():
    """`is_listed` reads the subject and nothing else; pin that, since it is the switch."""
    for rule in RULES:
        assert rulelist.is_listed(rule) is bool(rule["subject"]), rule["name"]


# --- Subject expansion -----------------------------------------------------


def test_any_expands_to_every_role():
    assert rulelist.subject_roles("any", roles) == frozenset(roles.ROLES)


def test_tag_and_team_tokens_expand():
    assert rulelist.subject_roles("tag:pack", roles) == set(roles.with_tag(roles.PACK))
    assert rulelist.subject_roles("team:cult", roles) == {"cultist"}


def test_mixed_tokens_union():
    assert rulelist.subject_roles("tag:pack,cultist", roles) == set(roles.with_tag(roles.PACK)) | {"cultist"}


def test_empty_subject_expands_to_nothing():
    assert rulelist.subject_roles("", roles) == frozenset()


def test_every_listed_rule_has_at_least_one_subject_role():
    for rule in RULES:
        if not rulelist.is_listed(rule):
            continue
        assert rulelist.subject_roles(rule["subject"], roles), rule["name"]


# --- Spot checks on the reasoning ------------------------------------------
#
# A handful of rules encode corrections that were wrong in earlier drafts. They are pinned
# individually because each was wrong in a way that looked right.


def _rule(name):
    return next(r for r in RULES if r["name"] == name)


def test_really_bad_luck_needs_the_grave_digger():
    """ "Stumble in a grave" is the Grave Digger's — an earlier draft asked only for the angel."""
    assert "grave_digger" in _rule("Really bad luck")["expr"]
    assert "guardian_angel" in _rule("Really bad luck")["expr"]


def test_the_love_achievements_need_cupid():
    """Romeo and Juliet and Double Shot are both "in love with" conditions."""
    assert "cupid" in _rule("Romeo and Juliet")["expr"]
    assert "cupid" in _rule("Double Shot")["expr"]


def test_trustworthy_needs_a_seer_to_do_the_checking():
    assert "seer" in _rule("Trustworthy!")["expr"]


def test_population_achievements_use_reachable_ceilings():
    """A game is never dealt ten cultists or seven wolves; both are reached by converting."""
    assert _rule("Cultist Convention")["expr"] == "max_possible_cultists() >= 10"
    assert "max_live_wolves()" in _rule("Pack Hunter")["expr"]


def test_simultaneous_wolf_counts_do_not_use_the_whole_game_ceiling():
    """ "Seven living wolves at one time" and "three wolves killed in a game" are different
    questions, and only the second one may count the Traitor: they turn once every wolf is
    dead, so they are the pack's replacement rather than another member of it."""
    for name in ("Pack Hunter", "Three Little Wolves and a Big Bad Pig"):
        assert "max_live_wolves()" in _rule(name)["expr"], name
        assert "max_possible_wolves()" not in _rule(name)["expr"], name
    assert "max_possible_wolves()" in _rule("Serial Samaritan")["expr"], "killed over a game, not at once"


def test_drunk_achievements_count_the_barkeeps_drunks():
    """The bar turns lowly villagers into drunks, so the Drunk role is not the only source."""
    for name in ("Wobble Wobble", "Lucky Day", "Alcoholics Anonymous", "Thanks, Junior!"):
        assert "max_possible_drunks()" in _rule(name)["expr"], name


def test_wolf_attack_achievements_never_ask_the_team():
    """team_count('wolf') includes the Sorcerer, who cannot eat anybody.

    "Is there a wolf" is asked with `max_possible_wolves()`, which counts the ones a bite
    or a Wild Child's turn could still make; `pack_count()` is for the questions that are
    about the dealt pack itself, like being its only member.
    """
    for name in ("Hey Man, Nice Shot", "Did you guard yourself?", "S-Tier Hunter", "Forbidden Love"):
        assert "max_possible_wolves()" in _rule(name)["expr"], name
        assert "team_count" not in _rule(name)["expr"], name
    assert _rule("Lone Wolf")["expr"].startswith("pack_count() == 1"), "the only wolf is a pack count"


def test_thanks_junior_excludes_the_cursed_and_the_traitor():
    """Confirmed by the group: only a Wild Child or a Doppelganger can reach it."""
    subject = rulelist.subject_roles(_rule("Thanks, Junior!")["subject"], roles)
    assert subject == {"wild_child", "doppelganger"}


def test_liquid_business_counts_lowly_villagers_not_the_village_team():
    assert _rule("Liquid Business")["expr"] == "count('villager') >= 3"


def test_mode_gated_achievements_are_skipped_except_lone_wolf():
    """This group always plays chaos, so Lone Wolf's role condition is its whole gate."""
    for name in ("Welcome to the Asylum", "Spy vs Spy", "Naughty!", "I Have No Idea What I'm Doing"):
        assert not rulelist.is_listed(_rule(name)), name
    assert rulelist.is_listed(_rule("Lone Wolf"))


def test_the_five_achievements_earlier_drafts_missed_are_present():
    """Regression: these were absent from the first catalogue, and one was misspelled."""
    for name in (
        "Welcome to Hell",
        "Welcome to the Asylum",
        "I See a Lack of Trust",
        "Death Village",
        "Cold as Ice",
        "Demoted by the Death",
    ):
        assert _rule(name)["name"] == name
