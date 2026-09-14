"""One rule at a time, against compositions chosen to make it answer.

`test_feasibility.py` proves the machinery works and that every expression *runs*.
This file asks a different question, the one that was never asked before: does each
rule say the right thing? A rule can be syntactically perfect, evaluate without
raising, pass the kitchen sink, and still be about the wrong game — which is exactly
what several of them were, for as long as they had been shipping.

Nothing here reads an expression. Each case is a composition and the answer the
*achievement's own description* gives for it, written down independently, so a rule
edited into agreement with a test that was derived from it cannot happen. When one
of these fails, one of the two is wrong and the description decides which.

Three tables:

* `EXPR_CASES` — per rule, compositions and whether the game can produce it. Every
  rule whose expression is not the constant `True` is here, with at least one case
  each way: a rule nothing can falsify is a rule that is not being tested.
* `SUBJECT_CASES` — per rule, who the achievement is listed *under*. This is the
  half an expression cannot check, and where "As the X…" is either honoured or not.
* The coverage test below, which fails if a rule has no cases at all — the same
  silent-omission guard `test_rules.py` puts on the catalogue itself.
"""

import pytest

import feasibility
import roles
import rulelist
from rulelist import RULES

CATALOGUE = {r["name"]: r for r in RULES}
LISTED = [r for r in RULES if rulelist.is_listed(r)]

# A role with no tags, on no team that matters, and named by exactly one rule
# (Spoiled Rich Brat). Padding a composition out to a player count must not quietly
# answer some other rule's question, which a Villager would: it feeds the bar, the
# Barkeep's bar count, and Forbidden Love's couple.
FILLER = "prince"


def pad(count):
    return [FILLER] * count


def comp(role_ids):
    return feasibility.Composition([(role_id,) for role_id in role_ids])


# --- Can the game produce it at all? ---------------------------------------
#
# (composition, the answer, why). Player counts are deliberate: several of these sit
# one player either side of a threshold, which is the only place an off-by-one shows.

EXPR_CASES = {
    "Enochlophobia": [
        (pad(35), True, "a 35-player game"),
        (pad(34), False, "one short"),
    ],
    "Introvert": [
        (pad(5), True, "exactly five"),
        (pad(6), False, "a sixth player rules it out"),
        (pad(4), False, "and so does a fourth going missing"),
    ],
    "Wobble Wobble": [
        (["drunk"] + pad(9), True, "a dealt Drunk in a ten-player game"),
        (["drunk"] + pad(8), False, "nine players is not ten"),
        (["barkeep", "villager"] + pad(8), True, "the bar makes the drunk the deal did not"),
        (pad(12), False, "a big game with nothing that can make anybody drunk"),
    ],
    "Inconspicuous": [
        (pad(20), True, "twenty"),
        (pad(19), False, "nineteen"),
    ],
    "Mason Brother": [
        (["mason", "mason"] + pad(3), True, "two dealt"),
        (["mason", "doppelganger"] + pad(3), True, "a Doppelganger copying the one makes the second"),
        (["mason", "thief"] + pad(3), True, "so does a Thief stealing the role"),
        (["doppelganger", "doppelganger"] + pad(3), False, "two copiers and no Mason to copy"),
        (["mason"] + pad(4), False, "one Mason has no brother"),
    ],
    "Double Shifter": [
        (["doppelganger", "alpha_wolf"] + pad(3), True, "take an identity, then be bitten out of it"),
        (["thief", "cursed"] + pad(3), True, "steal the Cursed, then turn"),
        (["doppelganger", "barkeep", "villager"] + pad(2), True, "copy a Villager, then drink"),
        (["doppelganger", "thief"] + pad(3), False, "one identity each and nothing to change it again"),
    ],
    "Hey Man, Nice Shot": [
        (["hunter", "werewolf"] + pad(3), True, "a wolf to shoot"),
        (["hunter", "serial_killer"] + pad(3), True, "or the serial killer"),
        (["hunter", "wild_child"] + pad(3), True, "a Wild Child can be the first wolf on its own"),
        (["hunter", "sorcerer"] + pad(3), False, "wolf team, and neither a wolf nor a killer"),
    ],
    "That's Why You Don't Stay Home": [
        (["werewolf", "harlot"] + pad(3), True, "somebody to be the harlot who stayed home"),
        (["werewolf"] + pad(4), False, "no harlot"),
    ],
    "Double Vision": [
        (["seer", "apprentice_seer", "doppelganger"] + pad(2), True, "all three, which is what two live seers takes"),
        (["seer", "apprentice_seer"] + pad(3), False, "the Apprentice only promotes once the Seer is dead"),
        (["seer", "doppelganger"] + pad(3), False, "and the copy only lands then too"),
    ],
    "Double Kill": [
        (["serial_killer", "hunter"] + pad(3), True, "both halves of the ending"),
        (["serial_killer"] + pad(4), False, "no hunter"),
        (["hunter"] + pad(4), False, "no serial killer"),
    ],
    "Should Have Known": [
        (["seer", "beholder"] + pad(3), True, "a Beholder to reveal"),
        (["seer"] + pad(4), False, "nobody to reveal"),
    ],
    "Sunday Bloody Sunday": [
        (["werewolf", "serial_killer"] + pad(3), True, "two hands that kill in the dark"),
        (["arsonist"] + pad(4), True, "one arsonist can burn a street at once"),
        (["gunner", "hunter"] + pad(3), False, "two killers, both of whom fire by day"),
    ],
    "Forbidden Love": [
        (["cupid", "villager", "werewolf"] + pad(2), True, "a wolf, a lowly Villager, and Cupid to pair them"),
        (["cupid", "villager", "sorcerer"] + pad(2), False, "wolf team, but no wolf"),
        (["cupid", "seer", "werewolf"] + pad(2), False, "villager, not village team"),
    ],
    "Smart Gunner": [
        (["gunner", "werewolf", "cultist"] + pad(2), True, "two bullets, two targets that count"),
        (["gunner", "werewolf"] + pad(3), False, "one target for two bullets"),
        (["gunner", "sorcerer", "arsonist"] + pad(2), False, "bad roles, but not ones the achievement names"),
    ],
    "Streetwise": [
        (
            ["detective", "werewolf", "werewolf", "werewolf", "werewolf"] + pad(2),
            True,
            "four of one role is four players",
        ),
        (["detective", "werewolf", "werewolf", "serial_killer", "cultist"] + pad(2), True, "or four of four kinds"),
        (["detective", "werewolf", "serial_killer", "cultist"] + pad(2), False, "three nights, not four"),
    ],
    "Speed Dating": [
        (["cupid"] + pad(4), True, "the bot only steps in when Cupid failed to choose"),
        (pad(5), False, "no Cupid to fail"),
    ],
    "Cultist Convention": [
        (["cultist"] + pad(9), True, "ten bodies the cult can reach"),
        (["cultist"] + pad(8), False, "nine"),
        (
            ["cultist", "werewolf", "serial_killer", "doppelganger", "cultist_hunter"] + pad(6),
            False,
            "eleven players, four of them immune: the cult cannot get past seven",
        ),
        (pad(12), False, "a big game with nobody doing the recruiting"),
    ],
    "Should've Said Something": [
        (["werewolf", "cupid"] + pad(3), True, "the pack needs a lover to eat"),
        (["werewolf"] + pad(4), False, "no lovers in this game"),
    ],
    "Serial Samaritan": [
        (["serial_killer", "werewolf", "werewolf", "werewolf"] + pad(2), True, "three wolves dealt"),
        (["serial_killer", "werewolf", "cursed", "traitor"] + pad(2), True, "or one and two that turn"),
        (["serial_killer", "werewolf", "werewolf"] + pad(3), False, "two is not three"),
    ],
    "Cultist Fodder": [
        (["cultist", "cultist_hunter"] + pad(3), True, "somebody to be sent against"),
        (["cultist"] + pad(4), False, "no Cult Hunter"),
    ],
    "Lone Wolf": [
        (["werewolf"] + pad(9), True, "one wolf, ten players"),
        (["werewolf", "sorcerer"] + pad(8), True, "a Sorcerer is wolf team and not a second wolf"),
        (["werewolf", "wolf_cub"] + pad(8), False, "two of the pack"),
        (["werewolf"] + pad(8), False, "nine players"),
    ],
    "Pack Hunter": [
        (["werewolf"] * 7 + pad(3), True, "seven dealt"),
        (["alpha_wolf"] + pad(6), True, "the bite puts every player within reach of the pack"),
        (["werewolf"] * 6 + pad(4), False, "six, with nothing that can make a seventh"),
    ],
    "Saved by the Bull(et)": [
        (["gunner", "werewolf"] + pad(3), True, "wolves to reach parity and a bullet to hold them"),
        (["gunner", "sorcerer"] + pad(3), False, "no wolf can reach parity"),
        (["werewolf"] + pad(4), False, "no gunner"),
    ],
    "OH SHI-": [
        (["werewolf", "cupid"] + pad(3), True, "a lover to kill"),
        (["werewolf"] + pad(4), False, "no lovers"),
    ],
    "No Sorcery!": [
        (["werewolf", "sorcerer"] + pad(3), True, "a sorcerer to eat"),
        (["werewolf"] + pad(4), False, "none"),
    ],
    "Cultist Tracker": [
        (["cultist_hunter", "cultist"] + pad(3), True, "three bodies the cult can reach"),
        (["cultist_hunter", "cultist"], False, "a two-player game: the cult can only ever be one"),
        (["cultist_hunter"] + pad(4), False, "no cult at all"),
    ],
    "Wuffie-Cult": [
        (["alpha_wolf"] + pad(3), True, "three players outside the pack to bite"),
        (["alpha_wolf"] + pad(2), False, "two"),
        (["alpha_wolf", "werewolf", "werewolf"] + pad(2), False, "five players, three of them pack"),
    ],
    "Did you guard yourself?": [
        (["guardian_angel", "werewolf"] + pad(3), True, "a wolf to guard"),
        (
            ["guardian_angel", "wild_child"] + pad(3),
            True,
            "a Wild Child is a first wolf nobody has to bite, so the dealt pack is the wrong count",
        ),
        (["guardian_angel", "cursed", "traitor"] + pad(2), False, "neither of those can be a game's first wolf"),
        (["guardian_angel", "sorcerer"] + pad(3), False, "wolf team, no wolf"),
    ],
    "Three Little Wolves and a Big Bad Pig": [
        (["sorcerer", "werewolf", "werewolf", "werewolf"] + pad(2), True, "three to survive alongside"),
        (["sorcerer", "werewolf", "werewolf"] + pad(3), False, "two"),
    ],
    "I Helped!": [
        (["wolf_cub", "werewolf"] + pad(3), True, "a pack to outlive the cub"),
        (["wolf_cub", "traitor"] + pad(3), True, "the Traitor turns the moment the last wolf dies, which is the cub"),
        (["wolf_cub"] + pad(4), False, "a lone cub with nothing that can become the pack"),
    ],
    "It Was a Busy Night!": [
        (["harlot", "guardian_angel", "serial_killer"] + pad(2), True, "three different visiting roles"),
        (["harlot", "guardian_angel"] + pad(3), False, "two"),
        (["werewolf", "werewolf", "werewolf"] + pad(2), False, "three visitors, one visiting role"),
    ],
    "Strongest Alpha": [
        (["alpha_wolf", "serial_killer"] + pad(3), True, "the target"),
        (["alpha_wolf"] + pad(4), False, "nobody to infect that counts"),
    ],
    "Am I Your Seer?": [
        (["fool", "beholder"] + pad(3), True, "a Beholder to spot"),
        (["fool"] + pad(4), False, "none"),
    ],
    "Demoted by the Death": [
        (["hunter", "wise_elder"] + pad(3), True, "the shot has to land on a Wise Elder"),
        (["hunter"] + pad(4), False, "none"),
    ],
    "Wasted Silver": [
        (["blacksmith", "sandman"] + pad(3), True, "both abilities have to land on the same day"),
        (["blacksmith"] + pad(4), False, "nobody singing"),
    ],
    "Trustworthy!": [
        (["wolfman", "seer"] + pad(3), True, "a Seer to do the checking"),
        (["wolfman", "fool"] + pad(3), False, "the Fool's vision is not a check"),
    ],
    "Deep Love": [
        (["doppelganger", "cupid"] + pad(3), True, "Cupid has to have made you a lover first"),
        (["doppelganger"] + pad(4), False, "no lovers"),
    ],
    "Seeing between Teams": [
        (["seer", "sorcerer", "cupid"] + pad(2), True, "the couple and whoever pairs them"),
        (["seer", "sorcerer"] + pad(3), False, "no Cupid to pair them"),
        (["seer", "cupid"] + pad(3), False, "no sorcerer to pair with"),
    ],
    "Just a Beardy Guy..?": [
        (["wolfman", "alpha_wolf"] + pad(3), True, "only the Alpha's bite turns them"),
        (["wolfman", "werewolf"] + pad(3), False, "an ordinary wolf's eat is not an infection"),
    ],
    "That Came Unexpected!": [
        (pad(3), True, "a three-player game is already down to the last three"),
        (pad(2), False, "two is fewer than the achievement's own three"),
    ],
    "My Sweetie so Strong!": [
        (["pacifist", "cupid"] + pad(3), True, "somebody to be in love with, and somebody to pair you"),
        (["pacifist"] + pad(4), False, "no Cupid"),
        (["cupid"] + pad(4), False, "no pacifist"),
    ],
    "Thanks, Junior!": [
        (["wild_child", "drunk", "werewolf"] + pad(2), True, "a drunk for the pack to eat and a pack to eat it"),
        (["wild_child", "werewolf"] + pad(3), False, "no drunk anywhere"),
        (["doppelganger", "drunk", "sorcerer"] + pad(2), False, "a drunk, and nothing that can ever be a wolf"),
    ],
    "I Lost my Wisdom": [
        (["wise_elder", "thief"] + pad(3), True, "a theft changes the role"),
        (["wise_elder", "alpha_wolf"] + pad(3), True, "so does the bite"),
        (["wise_elder", "werewolf"] + pad(3), False, "an ordinary wolf changes nobody's role"),
    ],
    "Affectionate": [
        (["harlot", "cupid"] + pad(3), True, "a lover to visit"),
        (["harlot"] + pad(4), False, "none"),
    ],
    "Lucky Day": [
        (["alpha_wolf", "drunk"] + pad(3), True, "a drunk to infect"),
        (["alpha_wolf", "barkeep", "villager"] + pad(2), True, "the bar makes one"),
        (["alpha_wolf", "barkeep"] + pad(3), False, "a bar with no lowly villagers to drink in it"),
    ],
    "Condition Red!": [
        (["werewolf", "traitor"] + pad(3), True, "a traitor to eat"),
        (["werewolf"] + pad(4), False, "none"),
    ],
    "Indestructible": [
        (["doppelganger", "wild_child"] + pad(3), True, "the pair, each able to be pointing at the other"),
        (["thief", "doppelganger"] + pad(3), True, "a Thief who can steal the Doppelganger"),
        (["thief", "wild_child"] + pad(3), True, "or the Wild Child"),
        (["doppelganger"] + pad(4), False, "one alone: there is nobody to have become"),
        (["wild_child"] + pad(4), False, "likewise"),
        (["thief"] + pad(4), False, "and a Thief with neither to steal"),
    ],
    "Psychopath Killer": [
        (["serial_killer"] + pad(34), True, "thirty-five"),
        (["serial_killer"] + pad(33), False, "thirty-four"),
    ],
    "Romeo and Juliet": [
        (["tanner", "cupid"] + pad(3), True, "the Tanner to love and Cupid to arrange it"),
        (["tanner"] + pad(4), False, "no Cupid"),
        (["cupid"] + pad(4), False, "no Tanner"),
    ],
    "Really bad luck": [
        (
            ["serial_killer", "grave_digger", "guardian_angel"] + pad(2),
            True,
            "a grave to stumble in and an angel to be fought off by",
        ),
        (["serial_killer", "guardian_angel"] + pad(3), False, "no grave"),
        (["serial_killer", "grave_digger"] + pad(3), False, "nobody to fight them off"),
    ],
    "Domino": [
        (["hunter", "hunter"] + pad(3), True, "two dealt"),
        (["hunter", "doppelganger"] + pad(3), True, "or one and a Doppelganger to copy them"),
        (["hunter", "thief"] + pad(3), True, "or a Thief to steal the role"),
        (["doppelganger", "thief"] + pad(3), False, "two copiers and no Hunter to copy"),
        (["hunter"] + pad(4), False, "nobody to shoot who would shoot back"),
    ],
    "Double Shot": [
        (["hunter", "cupid", "werewolf", "cultist"] + pad(2), True, "two bad roles and Cupid to pair them"),
        (["hunter", "werewolf", "cultist"] + pad(2), False, "no Cupid, so no couple"),
        (["hunter", "cupid", "werewolf"] + pad(2), False, "one bad role has nobody bad to love"),
    ],
    "Playing with the Fire": [
        (["arsonist"] + pad(5), True, "five houses that are not the arsonist's own"),
        (["arsonist"] + pad(4), False, "four"),
        (["arsonist", "serial_killer"] + pad(4), False, "six players, and the serial killer's house cannot be doused"),
    ],
    "Firework": [
        (["arsonist"] + pad(10), True, "ten"),
        (["arsonist"] + pad(9), False, "nine"),
        (["arsonist", "serial_killer"] + pad(9), False, "eleven players is exactly one house short with an SK in them"),
    ],
    "Cold as Ice": [
        (["snow_wolf", "harlot"] + pad(3), True, "the harlot is the one who has to be frozen"),
        (["snow_wolf"] + pad(4), False, "nobody to freeze"),
    ],
    "Good Choice... For You": [
        (["chemist"] + pad(3), True, "three visits needs somebody to visit"),
        (["chemist"] + pad(2), False, "too few to visit three times"),
    ],
    "Increase the Pack!": [
        (["alpha_wolf", "wolf_cub"] + pad(2), True, "a cub to die and two outside the pack to infect"),
        (["alpha_wolf", "wolf_cub"] + pad(1), False, "only one player left to infect"),
        (["alpha_wolf"] + pad(4), False, "no cub"),
    ],
    "Firefighter": [
        (["guardian_angel", "arsonist"] + pad(2), True, "three houses that could have been doused"),
        (["guardian_angel", "arsonist"] + pad(1), False, "only two"),
        (["guardian_angel"] + pad(4), False, "no kerosene anywhere"),
    ],
    "Helpful Paranoia": [
        (["hunter", "werewolf", "cultist"] + pad(2), True, "two things that come for you in the night"),
        (["hunter", "cursed", "cultist"] + pad(2), True, "the Cursed counts: they may be coming later"),
        (["hunter", "werewolf", "sorcerer"] + pad(2), False, "the Sorcerer attacks nobody"),
    ],
    "S-Tier Hunter": [
        (["hunter", "werewolf", "cultist"] + pad(2), True, "one of each, in one night"),
        (["hunter", "werewolf"] + pad(3), False, "no cultist"),
        (["hunter", "cultist"] + pad(3), False, "no wolf"),
    ],
    "Triple Kill": [
        (["serial_killer", "harlot", "guardian_angel"] + pad(2), True, "the target, and two who called on the killer"),
        (["serial_killer", "harlot"] + pad(3), False, "one caller only, which is two deaths"),
        (
            ["werewolf", "harlot", "guardian_angel"] + pad(2),
            True,
            "the victim, and the two who die for visiting a wolf",
        ),
        (["werewolf", "harlot"] + pad(3), False, "the Harlot alone is the second death, not the third"),
        (
            ["werewolf", "wolf_cub", "harlot"] + pad(2),
            True,
            "the cub's death buys a second eat, so one caller is enough",
        ),
        (["wolf_cub", "harlot"] + pad(3), False, "a cub with no pack to make the second eat"),
    ],
    "Resist the Beast": [
        (["wild_child", "traitor", "cursed"] + pad(2), True, "the achievement names the trio outright"),
        (["wild_child", "traitor"] + pad(3), False, "no Cursed"),
        (["traitor", "cursed"] + pad(3), False, "no Wild Child"),
    ],
    "At least you tried...": [
        (["guardian_angel", "chemist"] + pad(3), True, "poison for the saved player to die to"),
        (["guardian_angel"] + pad(4), False, "no chemist"),
    ],
    "Lucky Night": [
        (["chemist", "harlot"] + pad(3), True, "both visits, in one night"),
        (["chemist"] + pad(4), False, "no harlot"),
        (["harlot"] + pad(4), False, "no chemist"),
    ],
    "In the Middle of the Trouble": [
        (["guardian_angel", "werewolf", "serial_killer"] + pad(2), True, "something attacking a wolf"),
        (["guardian_angel", "werewolf", "chemist"] + pad(2), False, "the guard does not stop the poison"),
        (["guardian_angel", "serial_killer"] + pad(3), False, "no wolf to save"),
    ],
    "Am I hallucinating?!": [
        (["fool", "wolfman"] + pad(3), True, "the Seer reads a Wolf Man as a wolf, never as itself"),
        (["fool", "lycan"] + pad(3), True, "and a Lycan as a villager"),
        (["fool", "traitor"] + pad(3), True, "and a Traitor as a villager"),
        (["fool", "werewolf"] + pad(3), False, "an ordinary wolf is exactly what the Seer does see"),
    ],
    "Going Down with my Beer": [
        (["villager", "barkeep", "arsonist"] + pad(2), True, "a bar to drink in and a fire to die in"),
        (["villager", "barkeep"] + pad(3), False, "nobody to set it alight"),
        (["villager", "arsonist"] + pad(3), False, "no bar to be in"),
    ],
    "Alcoholics Anonymous": [
        (["drunk", "drunk", "drunk"] + pad(2), True, "three dealt"),
        (["barkeep", "villager", "villager", "villager"] + pad(1), True, "the bar manufactures them"),
        (["drunk", "drunk"] + pad(3), False, "two, and nothing that makes a third"),
    ],
    "Liquid Business": [
        (["barkeep", "villager", "villager", "villager"], True, "three lowly villagers"),
        (["barkeep", "villager", "villager", "seer"], False, "the village *team* does not drink here"),
    ],
    "Traffic Control": [
        (["chef", "werewolf", "werewolf", "werewolf"] + pad(1), True, "three people, even of one role"),
        (["chef", "harlot", "guardian_angel"] + pad(2), False, "two"),
    ],
    "Definitely Dead": [
        (["chef", "werewolf"] + pad(3), True, "somebody who can be murdered that night"),
        (["chef", "gunner", "hunter"] + pad(2), False, "two killers, and both of them fire by day"),
    ],
    "Going Out Of Business": [
        (["barkeep"] + pad(9), True, "ten players"),
        (["barkeep"] + pad(8), False, "nine"),
    ],
    "Food Waste": [
        (["chef"] + pad(4), True, "the chef and three who could stay home"),
        (["chef"] + pad(3), False, "too few"),
    ],
}


@pytest.mark.parametrize(
    "name,role_ids,expected,why",
    [(name, role_ids, expected, why) for name, cases in EXPR_CASES.items() for role_ids, expected, why in cases],
    ids=["{}-{}".format(name, index) for name, cases in EXPR_CASES.items() for index in range(len(cases))],
)
def test_the_composition_gets_the_answer_the_description_gives(name, role_ids, expected, why):
    got = feasibility.evaluate(CATALOGUE[name]["expr"], comp(role_ids))
    assert got is expected, "{}: expected {} because {} -- {}".format(name, expected, why, CATALOGUE[name]["expr"])


# --- Who is it listed under? -----------------------------------------------
#
# (game, the players it belongs to, whether it is a roleless one, why). The game is
# keyed the way a real one is — a key per player — and the expected set is the whole
# answer, so a rule that starts listing an achievement under one more player fails
# here rather than quietly widening in production.
#
# Every rule whose expression is the constant `True` is in this table, because the
# subject is the only thing it has to say. So is every rule whose subject was itself
# the correction.

_SF = roles.SEER_FOOL

SUBJECT_CASES = {
    "Welcome to Hell": [
        (
            {"a": ("villager",), "b": ("seer",)},
            set(),
            True,
            "playing is the whole condition, so it belongs to nobody in particular",
        ),
    ],
    "The First Stone": [
        ({"a": ("villager",), "b": ("seer",)}, set(), True, "voting behaviour, no role gate"),
    ],
    "In for the Long Haul": [
        ({"a": ("villager",), "b": ("seer",)}, set(), True, "an hour of wall clock"),
    ],
    "Death Village": [
        ({"a": ("villager",), "b": ("seer",)}, set(), True, "a game nobody wins is a fact about the game"),
    ],
    "Masochist": [
        ({"t": ("tanner",), "v": ("villager",)}, {"t"}, False, "the Tanner's alone"),
    ],
    "So Close!": [
        ({"t": ("tanner",), "v": ("villager",)}, {"t"}, False, "likewise"),
    ],
    "Tanner Overkill": [
        ({"t": ("tanner",), "v": ("villager",)}, {"t"}, False, "likewise"),
    ],
    "Promiscuous": [
        ({"h": ("harlot",), "v": ("villager",)}, {"h"}, False, "the Harlot's"),
    ],
    "I See a Lack of Trust": [
        ({"s": ("seer",), "v": ("villager",)}, {"s"}, False, "the Seer's"),
        (
            {"u": _SF, "v": ("villager",)},
            {"u"},
            False,
            "a player told they are the Seer cannot know they are not the Fool, and is eligible for both",
        ),
    ],
    "Even a Stopped Clock is Right Twice a Day": [
        ({"f": ("fool",), "v": ("villager",)}, {"f"}, False, "the Fool's"),
        ({"u": _SF, "v": ("villager",)}, {"u"}, False, "and so the unresolved pair's"),
    ],
    "Self Loving": [
        ({"c": ("cupid",), "v": ("villager",)}, {"c"}, False, "Cupid's own choice"),
    ],
    "I'M NOT DRUN-- *BURPPP*": [
        ({"c": ("clumsy",), "v": ("villager",)}, {"c"}, False, "the Clumsy Guy's"),
    ],
    "Spoiled Rich Brat": [
        ({"p": ("prince",), "v": ("villager",)}, {"p"}, False, "the Prince's"),
    ],
    "President": [
        ({"m": ("mayor",), "v": ("villager",)}, {"m"}, False, "the Mayor's"),
    ],
    "Time to retire...": [
        ({"s": ("sorcerer",), "v": ("villager",)}, {"s"}, False, "the Sorcerer's"),
    ],
    "Now I'm Blind": [
        ({"o": ("oracle",), "v": ("villager",)}, {"o"}, False, "the Oracle's"),
    ],
    "Every Man for Himself!": [
        ({"p": ("pacifist",), "v": ("villager",)}, {"p"}, False, "the Pacifist's"),
    ],
    "Change Sides Works": [
        (
            {"c": ("cursed",), "w": ("werewolf",), "v": ("villager",)},
            {"c"},
            False,
            "the roles that swing, and not the wolf who was dealt one",
        ),
    ],
    "Cult Leader": [
        ({"c": ("cultist",), "v": ("villager",)}, {"c"}, False, "an original cultist"),
        (
            {"c": ("cultist",), "d": ("doppelganger",)},
            {"c", "d"},
            False,
            "the Doppelganger is offered it as a role change and cannot really earn it -- "
            '"from the beginning" is a fact about the deal the subject cannot express',
        ),
    ],
    # --- The subjects that were themselves the correction -------------------
    "OH SHI-": [
        (
            {"g": ("gunner",), "h": ("hunter",), "w": ("werewolf",), "c": ("cupid",)},
            {"w"},
            False,
            "killing your lover on night one is for the roles that kill at night; the Gunner and "
            "the Hunter carry the killer tag and fire only by day",
        ),
    ],
    "Indestructible": [
        ({"d": ("doppelganger",), "w": ("wild_child",), "v": ("villager",)}, {"d", "w"}, False, "the pair"),
        (
            {"t": ("thief",), "d": ("doppelganger",), "v": ("villager",)},
            {"t", "d"},
            False,
            "or a Thief and either of them",
        ),
    ],
    "Triple Kill": [
        (
            {"sk": ("serial_killer",), "h": ("harlot",), "ga": ("guardian_angel",), "v": ("villager",)},
            {"sk"},
            False,
            "the killer's, not the callers'",
        ),
        (
            {"w": ("werewolf",), "h": ("harlot",), "ga": ("guardian_angel",)},
            {"w"},
            False,
            "and the wolf's",
        ),
    ],
    "Thanks, Junior!": [
        (
            {"wc": ("wild_child",), "cu": ("cursed",), "tr": ("traitor",), "d": ("drunk",), "w": ("werewolf",)},
            {"wc"},
            False,
            "confirmed by the group: never via the Cursed or the Traitor, who turn by another route",
        ),
    ],
    "Double Shifter": [
        (
            {"d": ("doppelganger",), "t": ("thief",), "c": ("cursed",), "a": ("alpha_wolf",)},
            {"d", "t"},
            False,
            "the Cursed turns once and is then a wolf; carrying the role_swing tag is not swinging twice",
        ),
    ],
    "Cold as Ice": [
        (
            {"sw": ("snow_wolf",), "w": ("werewolf",), "h": ("harlot",), "v": ("villager",)},
            {"sw"},
            False,
            "the Snow Wolf's alone -- not the harlot who gets frozen, and not the rest of the pack, "
            "who have no freeze to do it with",
        ),
    ],
    "Mason Brother": [
        (
            {"m": ("mason",), "d": ("doppelganger",), "v": ("villager",)},
            {"m", "d"},
            False,
            "the Mason, and the Doppelganger who can become the second one",
        ),
    ],
    "Forbidden Love": [
        (
            {"v": ("villager",), "w": ("werewolf",), "c": ("cupid",), "s": ("seer",)},
            {"v", "w"},
            False,
            "the couple the achievement names -- villager, not village team, so not the Seer",
        ),
    ],
    "Saved by the Bull(et)": [
        (
            {"v": ("villager",), "g": ("gunner",), "w": ("werewolf",), "sk": ("serial_killer",)},
            {"v", "g"},
            False,
            "the village team it saves, and neither the wolves nor the loner",
        ),
    ],
    "Liquid Business": [
        (
            {"b": ("barkeep",), "v1": ("villager",), "v2": ("villager",), "v3": ("villager",)},
            {"b"},
            False,
            "the Barkeep's, however many villagers are drinking",
        ),
    ],
    "Wobble Wobble": [
        (
            dict(
                {"b": ("barkeep",), "v": ("villager",)},
                **{str(i): (FILLER,) for i in range(8)},
            ),
            {"v"},
            False,
            "the lowly Villager the bar can turn into the drunk -- the Barkeep never drinks there",
        ),
    ],
    "Hey Man, Nice Shot": [
        ({"h": ("hunter",), "w": ("werewolf",), "v": ("villager",)}, {"h"}, False, "the Hunter's dying shot"),
    ],
}


def _listed_under(game, name):
    per_player, shared = feasibility.feasible(game, CATALOGUE)
    under = {key for key, entries in per_player.items() if any(e["name"] == name for e in entries)}
    return under, name in {entry["name"] for entry in shared}


@pytest.mark.parametrize(
    "name,game,expected,expect_shared,why",
    [
        (name, game, expected, is_shared, why)
        for name, cases in SUBJECT_CASES.items()
        for game, expected, is_shared, why in cases
    ],
    ids=["{}-{}".format(name, index) for name, cases in SUBJECT_CASES.items() for index in range(len(cases))],
)
def test_the_achievement_is_listed_under_exactly_its_subject(name, game, expected, expect_shared, why):
    under, is_shared = _listed_under(game, name)
    assert under == expected, "{}: {}".format(name, why)
    assert is_shared is expect_shared, "{}: {}".format(name, why)


# --- Coverage --------------------------------------------------------------
#
# The same guard test_rules.py puts on the catalogue, one level up: a rule with no
# cases is one nothing here is checking, and nothing would say so.


def test_every_listed_rule_has_cases():
    """A rule with no cases is untested, and untested is how all of these shipped."""
    missing = [rule["name"] for rule in LISTED if rule["name"] not in EXPR_CASES and rule["name"] not in SUBJECT_CASES]
    assert not missing, "rules with no cases at all: {}".format(sorted(missing))


def test_a_rule_with_a_real_expression_is_exercised_both_ways():
    """One case each way, or the case is not testing the expression.

    Four `True` cases prove only that a rule can pass, which `True` also does.
    """
    for rule in LISTED:
        if rule["expr"] == "True":
            continue
        cases = EXPR_CASES.get(rule["name"])
        assert cases, "{}: an expression with no cases".format(rule["name"])
        answers = {expected for _roles, expected, _why in cases}
        assert answers == {True, False}, "{}: cases only ever answer {}".format(rule["name"], answers)


def test_a_rule_with_no_expression_is_covered_by_its_subject():
    """`True` cannot be falsified, so the subject is the only thing left to check."""
    for rule in LISTED:
        if rule["expr"] != "True":
            continue
        assert rule["name"] in SUBJECT_CASES, "{}: nothing but a subject, and no subject case".format(rule["name"])
        assert rule["name"] not in EXPR_CASES, "{}: a case against `True` proves nothing".format(rule["name"])


def test_every_name_in_the_tables_is_a_rule_that_can_be_listed():
    """A case for an opted-out or misspelled achievement passes while testing nothing."""
    listed = {rule["name"] for rule in LISTED}
    for table, label in ((EXPR_CASES, "EXPR_CASES"), (SUBJECT_CASES, "SUBJECT_CASES")):
        unknown = set(table) - listed
        assert not unknown, "{}: {}".format(label, sorted(unknown))
