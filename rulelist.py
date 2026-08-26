"""Canonical feasibility rules — the seed source for the `achievement_rules` table.

This file plays exactly the role `achvlist.py` plays for achievements: it is what a fresh
database is populated *from*, not what a running bot reads. The live rules are the table,
so a rule can be corrected mid-game with `/setrule` and take effect on the next render
without a deploy. What makes both true at once is the seed's upsert condition — a deploy
refreshes every rule nobody has edited and never overwrites one that has been (see
`db.seed_rules`).

Each rule answers two different questions, and conflating them is the mistake this shape
exists to prevent:

* **subject** — *who can earn this*, taken from the "As the X…" in the achievement's own
  description. The output is per player, so an achievement is listed under a player only
  when their role is in the subject.
* **expr** — *is the game capable of it*, evaluated against the whole role composition.

"Cold as Ice" is subject `snow_wolf`, expr "a harlot is present": it belongs under the
Snow Wolf and nobody else, and only when there is a harlot to freeze. A single boolean per
achievement cannot express that, and a per-player list built from one would either show
every achievement to everyone or silently pick a subject.

Tiers say how much the game still has to cooperate:

* **check**  — the composition genuinely makes it reachable; listed plainly.
* **maybe**  — precondition met, but a choice, a roll or an outcome is still needed.
* **always** — no role gate at all; possible in any game, so summarised rather than
  repeated under all twenty players.
* **skip**   — never listed: cross-game totals, non-gameplay, or gated on a game mode we
  cannot observe (chaos, secret, amnesia, NSFW, event roles).

Subject syntax is a comma-separated list of `any`, a role id, `tag:<tag>` or
`team:<team>`; `subject_roles()` below is the only thing that parses it.

Two systemic corrections are baked into the expressions and worth stating once, because
both look like bugs until you know why:

* **team is not capability.** `team_count('wolf')` includes the Sorcerer, who cannot eat.
  Anything meaning "a wolf could kill tonight" uses `pack_count()`.
* **starting counts understate the game.** Roles convert, so a game never *starts* with
  ten cultists — the cult recruits to ten. Achievements counting a population use a
  reachable ceiling (`max_possible_cultists()`, `max_possible_wolves()`,
  `max_possible_drunks()`) rather than a starting `count()`.
"""

CHECK = "check"
MAYBE = "maybe"
ALWAYS = "always"
SKIP = "skip"

TIERS = (CHECK, MAYBE, ALWAYS, SKIP)

# Subject prefixes. Bare tokens are role ids.
ANY = "any"
TAG_PREFIX = "tag:"
TEAM_PREFIX = "team:"


def _rule(name, tier, subject, expr, note):
    return {"name": name, "tier": tier, "subject": subject, "expr": expr, "note": note}


def _skip(name, note):
    """A rule that exists only so the achievement is accounted for.

    Every achievement must appear here — tests/test_rules.py asserts it both ways — so
    that a new one added to achvlist.py fails the suite instead of quietly never being
    listed. Skipped rows carry the reason, since "why is this never offered" is the
    question someone will eventually ask.
    """
    return _rule(name, SKIP, "", "False", note)


# In achvlist.ACHV order, so the seeded table reads the same way the achievement list does.
RULES = [
    _rule("Welcome to Hell", ALWAYS, ANY, "True", "Playing is the whole condition."),
    _skip("Welcome to the Asylum", "Chaos mode, which we cannot observe from a role list."),
    _skip("Alzheimer's Patient", "Amnesia language pack — a game setting, not a role."),
    _skip("O HAI DER!", "Requires a specific account to be in the game."),
    _skip("Spy vs Spy", "Secret mode — a game setting, not a role."),
    _skip("Explorer", "Inactive, and counts groups across games."),
    _skip("Linguist", "Inactive, and counts language packs across games."),
    _skip("I Have No Idea What I'm Doing", "Secret amnesia mode — a game setting."),
    _rule("Enochlophobia", CHECK, ANY, "players >= 35", "Purely a player count."),
    _rule("Introvert", CHECK, ANY, "players == 5", "Exactly five, so a sixth player rules it out."),
    _skip("Naughty!", "NSFW language pack — a game setting."),
    _skip("Dedicated", "100 games, cumulative across games."),
    _skip("Obsessed", "1000 games, cumulative across games."),
    _skip("Here's Johnny!", "50 kills across games (not_via_playing)."),
    _skip("I've Got Your Back", "50 saves across games (not_via_playing)."),
    _rule("Masochist", MAYBE, "tanner", "True", "Needs the win as well as the role."),
    _rule(
        "Wobble Wobble",
        MAYBE,
        "drunk",
        "max_possible_drunks() >= 1 and players >= 10",
        "A Barkeep turns a lowly villager into a drunk, so the Drunk role is not the only source.",
    ),
    _rule("Inconspicuous", MAYBE, ANY, "players >= 20", "Player count gates it; the rest is behaviour."),
    _skip("Survivalist", "Survive 100 games, cumulative."),
    _skip("Black Sheep", "Inactive, and a streak across games."),
    _rule("Promiscuous", MAYBE, "harlot", "True", "Also needs a 5+ night game, which roles cannot predict."),
    _rule(
        "Mason Brother",
        CHECK,
        "mason",
        "count('mason') + count('doppelganger') >= 2",
        "Two surviving masons, and a Doppelganger copying one makes the second.",
    ),
    _rule("Double Shifter", MAYBE, "tag:role_swing", "True", "Two role changes in one game."),
    _rule(
        "Hey Man, Nice Shot",
        MAYBE,
        "hunter",
        "pack_count() > 0 or ispresent('serial_killer')",
        "The dying shot must land on a wolf or the serial killer — a Sorcerer is neither.",
    ),
    _rule(
        "That's Why You Don't Stay Home",
        MAYBE,
        "tag:pack,cultist",
        "ispresent('harlot')",
        "Someone has to be the harlot who stayed home.",
    ),
    _rule(
        "Double Vision",
        MAYBE,
        "apprentice_seer,doppelganger",
        "all_present('seer','apprentice_seer','doppelganger')",
        "Two live seers at once: the Seer's death promotes the Apprentice and turns a Doppelganger "
        "who copied them into a second Seer. Without all three there is never more than one.",
    ),
    _rule(
        "Double Kill",
        MAYBE,
        "serial_killer,hunter",
        "all_present('serial_killer','hunter')",
        "The ending needs both halves in play.",
    ),
    _rule("Should Have Known", MAYBE, "seer", "ispresent('beholder')", "There must be a Beholder to reveal."),
    _rule("I See a Lack of Trust", MAYBE, "seer", "True", "Day-one lynch; nothing else in the composition gates it."),
    _rule(
        "Sunday Bloody Sunday",
        MAYBE,
        ANY,
        "killers() >= 2 or ispresent('arsonist')",
        "Four deaths in one night needs several killers, or an arsonist who can burn a street at once.",
    ),
    _rule("Change Sides Works", MAYBE, "tag:role_swing", "True", "A role change plus a win."),
    _rule(
        "Forbidden Love",
        MAYBE,
        "villager,tag:pack",
        "all_present('cupid','villager') and pack_count() > 0",
        "'villager, not village team' is literal: the couple must be a wolf and a plain Villager, "
        "and Cupid has to pair them.",
    ),
    _skip("Developer", "A merged pull request — not gameplay."),
    _rule("The First Stone", ALWAYS, ANY, "True", "Voting behaviour, no role gate."),
    _rule(
        "Smart Gunner",
        MAYBE,
        "gunner",
        "bad_count() >= 2",
        "Both bullets must hit a wolf, serial killer or cultist, so two bad roles have to exist.",
    ),
    _rule(
        "Streetwise",
        MAYBE,
        "detective",
        "distinct_bad_roles() >= 4",
        "Four nights in a row finding a *different* one, so four distinct bad roles are needed.",
    ),
    _rule("Speed Dating", MAYBE, ANY, "ispresent('cupid')", "The bot only picks lovers when Cupid failed to."),
    _rule("Even a Stopped Clock is Right Twice a Day", MAYBE, "fool", "True", "Two correct visions, by luck."),
    _rule("So Close!", MAYBE, "tanner", "True", "A vote tie, which no composition can predict."),
    _rule(
        "Cultist Convention",
        MAYBE,
        "cultist",
        "max_possible_cultists() >= 10",
        "Ten living cultists is reached by recruiting, never by dealing — and the cult-immune roles "
        "cap how large it can get.",
    ),
    _rule("Self Loving", MAYBE, "cupid", "True", "Cupid's own choice."),
    _rule("Should've Said Something", MAYBE, "tag:pack", "ispresent('cupid')", "The pack must have a lover to eat."),
    _rule("Tanner Overkill", MAYBE, "tanner", "True", "A unanimous lynch, which is behaviour."),
    _rule(
        "Serial Samaritan",
        MAYBE,
        "serial_killer",
        "max_possible_wolves() >= 3",
        "Three wolves to kill — counting the ones the Alpha or a conversion could still create.",
    ),
    _rule(
        "Cultist Fodder",
        CHECK,
        "cultist",
        "ispresent('cultist_hunter')",
        "The cult has to have a Cult Hunter to send someone against.",
    ),
    _rule(
        "Lone Wolf",
        MAYBE,
        "tag:pack",
        "team_count('wolf') == 1 and players >= 10",
        "Chaos-mode only per its description, which is safe to ignore here: this group always plays "
        "chaos, so the role condition is the whole gate.",
    ),
    _rule(
        "Pack Hunter",
        MAYBE,
        "tag:pack,tag:potential_wolf",
        "max_possible_wolves() >= 7",
        "Seven living wolves at once, counting conversions — a game is never dealt seven.",
    ),
    _rule(
        "Saved by the Bull(et)",
        MAYBE,
        "team:village",
        "ispresent('gunner') and pack_count() > 0",
        "Wolves must reach parity with the village while the Gunner still holds a bullet.",
    ),
    _rule("In for the Long Haul", ALWAYS, ANY, "True", "An hour of wall clock, no role gate."),
    _rule("OH SHI-", MAYBE, "tag:killer", "ispresent('cupid')", "You must have a lover to kill on night one."),
    _skip("Veteran", "500 games, cumulative."),
    _rule("No Sorcery!", MAYBE, "tag:pack", "ispresent('sorcerer')", "There has to be a sorcerer to eat."),
    _rule(
        "Cultist Tracker",
        MAYBE,
        "cultist_hunter",
        "ispresent('cultist') and cultable_count() >= 3",
        "Three cultists to kill, which the cult reaches by recruiting from the convertible players.",
    ),
    _rule("I'M NOT DRUN-- *BURPPP*", MAYBE, "clumsy", "True", "Three correct lynches, half of them by coin flip."),
    _rule("Wuffie-Cult", MAYBE, "alpha_wolf", "players >= 5", "Three successful bites needs bodies to bite."),
    _rule(
        "Did you guard yourself?",
        MAYBE,
        "guardian_angel",
        "pack_count() > 0",
        "There must be a wolf to guard, three times, and survive it.",
    ),
    _rule("Spoiled Rich Brat", MAYBE, "prince", "True", "The village has to lynch them twice."),
    _rule(
        "Three Little Wolves and a Big Bad Pig",
        MAYBE,
        "sorcerer",
        "max_possible_wolves() >= 3",
        "Three living wolves alongside a surviving sorcerer.",
    ),
    _rule("President", MAYBE, "mayor", "True", "Three votes after revealing — behaviour."),
    _rule(
        "I Helped!",
        MAYBE,
        "wolf_cub",
        "max_possible_wolves() >= 2",
        "The pack has to outlive the cub to make the two eats — but the wolf that outlives "
        "it need not be one at the start. A game dealt a lone Wolf Cub reaches a second wolf "
        "the moment the cub dies and the Traitor turns, which is exactly when this fires. "
        "Counting the pack as dealt missed that and never offered it.",
    ),
    _rule(
        "It Was a Busy Night!",
        MAYBE,
        ANY,
        "tag_count('visitor') >= 3",
        "Three *different* visiting roles in one night, so three must be in the game.",
    ),
    _rule("Strongest Alpha", CHECK, "alpha_wolf", "ispresent('serial_killer')", "The serial killer is the target."),
    _rule("Am I Your Seer?", MAYBE, "fool", "ispresent('beholder')", "There must be a Beholder to spot."),
    _rule("Demoted by the Death", MAYBE, "hunter", "ispresent('wise_elder')", "The final shot must hit a Wise Elder."),
    _rule("Wasted Silver", MAYBE, "blacksmith", "ispresent('sandman')", "Both abilities must land on the same day."),
    _rule(
        "Trustworthy!",
        MAYBE,
        "wolfman",
        "ispresent('seer')",
        "The point is surviving *after being checked*, so a Seer has to exist to check them.",
    ),
    _rule(
        "Deep Love",
        MAYBE,
        "doppelganger",
        "ispresent('cupid')",
        "Choosing your lover as your role model needs Cupid to have made you a lover first.",
    ),
    _rule("Time to retire...", MAYBE, "sorcerer", "True", "Last alive and losing — an outcome, not a composition."),
    _rule(
        "Seeing between Teams",
        CHECK,
        "seer,sorcerer",
        "all_present('seer','sorcerer','cupid')",
        "A seer/sorcerer couple needs Cupid to pair them.",
    ),
    _rule("Just a Beardy Guy..?", CHECK, "wolfman", "ispresent('alpha_wolf')", "Only the Alpha's bite can turn them."),
    _rule("That Came Unexpected!", MAYBE, "tanner", "players >= 4", "Being lynched down to the last three."),
    _rule(
        "Now I'm Blind",
        CHECK,
        "oracle",
        "ispresent('cultist') or distinct_roles() <= 2",
        "The vision fails when everyone else shares one role. Conventionally the cult converts the "
        "rest of the village into that one role; a game dealt only one other role gets there directly.",
    ),
    _rule("Every Man for Himself!", MAYBE, "pacifist", "True", "Saving yourself from a lynch in progress."),
    _rule(
        "My Sweetie so Strong!",
        MAYBE,
        ANY,
        "all_present('pacifist','cupid')",
        "You must be in love with the pacifist, so Cupid has to pair you.",
    ),
    _rule("Cult Leader", MAYBE, "cultist", "True", "Survive and win as an original cultist."),
    _rule(
        "Thanks, Junior!",
        MAYBE,
        "wild_child,doppelganger",
        "max_possible_drunks() >= 1 and pack_count() > 0",
        "You turn wolf the night the pack eats the Drunk — reachable via a Wild Child whose role "
        "model was eaten or a Doppelganger's copy, but never via the Cursed or the Traitor.",
    ),
    _rule("Death Village", ALWAYS, ANY, "True", "A game that ends with nobody winning."),
    _rule(
        "I Lost my Wisdom",
        MAYBE,
        "wise_elder",
        "ispresent('thief','alpha_wolf')",
        "Something must be able to change their role: a theft or the Alpha's bite.",
    ),
    _rule("Affectionate", MAYBE, "harlot", "ispresent('cupid')", "You need a lover to visit."),
    _rule(
        "Lucky Day",
        CHECK,
        "alpha_wolf",
        "max_possible_drunks() >= 1",
        "Infecting the drunk and staying sober needs a drunk to infect.",
    ),
    _rule("Condition Red!", MAYBE, "tag:pack", "ispresent('traitor')", "The last wolf must have a traitor to eat."),
    _rule(
        "Indestructible",
        MAYBE,
        "doppelganger,wild_child,thief",
        "True",
        "Ending up as your own role model — reachable by any role that takes on another's identity.",
    ),
    _rule("Psychopath Killer", MAYBE, "serial_killer", "players >= 35", "A 35-player win."),
    _skip("Today's Special!", "An event-only role, absent from the standard /rolelist."),
    _rule(
        "Romeo and Juliet",
        MAYBE,
        ANY,
        "all_present('tanner','cupid')",
        "Being in love with the tanner requires Cupid as well as the Tanner.",
    ),
    _rule(
        "Really bad luck",
        CHECK,
        "serial_killer",
        "all_present('grave_digger','guardian_angel')",
        "Stumbling into *a grave* needs the Grave Digger, and being fought off needs the angel. "
        "Both, or the sequence cannot happen.",
    ),
    _rule(
        "Domino",
        CHECK,
        "hunter",
        "count('hunter') >= 2 or ispresent('doppelganger')",
        "A second hunter to shoot — dealt, or made by a Doppelganger copying the first.",
    ),
    _rule(
        "Double Shot",
        MAYBE,
        "hunter,gunner",
        "ispresent('cupid') and bad_count() >= 2",
        "The target must be a bad role *in love with another bad role*, so Cupid and two bad roles.",
    ),
    _rule("Playing with the Fire", CHECK, "arsonist", "max_burnable_houses() >= 5", "Five houses that can be doused."),
    _rule("Firework", CHECK, "arsonist", "max_burnable_houses() >= 10", "Ten houses that can be doused."),
    _rule("Cold as Ice", CHECK, "snow_wolf", "ispresent('harlot')", "The harlot is the one who has to be frozen."),
    _rule("Good Choice... For You", MAYBE, "chemist", "players >= 4", "Three surviving visits needs targets."),
    _rule("Increase the Pack!", CHECK, "alpha_wolf", "ispresent('wolf_cub')", "The cub has to die first."),
    _rule("Firefighter", CHECK, "guardian_angel", "ispresent('arsonist')", "Three houses of kerosene to clean."),
    _rule(
        "Helpful Paranoia",
        MAYBE,
        "hunter",
        "attackers() >= 2",
        "Two attackers to shoot: wolves, the wolves they could still turn, and cultists. "
        "The Sorcerer never attacks anyone.",
    ),
    _rule(
        "S-Tier Hunter",
        CHECK,
        "hunter",
        "pack_count() > 0 and ispresent('cultist')",
        "One of each, in the same night.",
    ),
    _rule("Triple Kill", MAYBE, "serial_killer,tag:pack", "players >= 4", "Three deaths by one hand in one night."),
    _rule(
        "Resist the Beast",
        CHECK,
        "wild_child,traitor,cursed",
        "all_present('wild_child','traitor','cursed')",
        "The achievement names the trio outright; all three must be dealt.",
    ),
    _rule(
        "At least you tried...",
        CHECK,
        "guardian_angel",
        "ispresent('chemist')",
        "The saved player has to die to the chemist's poison.",
    ),
    _rule(
        "Lucky Night",
        MAYBE,
        ANY,
        "all_present('chemist','harlot')",
        "Both visits, in the same night, to the same player.",
    ),
    _rule(
        "In the Middle of the Trouble",
        MAYBE,
        "guardian_angel",
        "pack_count() > 0 and ispresent('serial_killer','arsonist')",
        "Saving a werewolf means something else has to be attacking one.",
    ),
    _rule(
        "Am I hallucinating?!",
        MAYBE,
        "fool",
        "ispresent('traitor','wolfman')",
        "The Seer reads a WolfMan as a wolf and a Traitor as a villager, so those two are the roles "
        "a real vision can never report — seeing one proves you are the Fool.",
    ),
    _rule(
        "Going Down with my Beer",
        CHECK,
        "villager",
        "all_present('barkeep','arsonist')",
        "A bar to drink in and a fire to die in.",
    ),
    _rule(
        "Alcoholics Anonymous",
        CHECK,
        "drunk",
        "max_possible_drunks() >= 3",
        "Three drunks alive at the end. The Barkeep manufactures them from lowly villagers, so this "
        "is reachable far below three dealt Drunks.",
    ),
    _rule(
        "Liquid Business",
        MAYBE,
        "barkeep",
        "count('villager') >= 3",
        "'Visited by 3 or more villagers' is literal — the bar opens for lowly villagers, not the village team.",
    ),
    _rule("Traffic Control", MAYBE, "chef", "tag_count('visitor') >= 3", "Three visits to one player in one night."),
    _rule("Definitely Dead", MAYBE, "chef", "killers() >= 1", "Somebody has to be murdered that night."),
    _rule("Going Out Of Business", MAYBE, "barkeep", "players >= 10", "Ten players, and three empty nights."),
    _rule("Food Waste", MAYBE, "chef", "players >= 5", "Three players who stayed home and had no visitors."),
]


def subject_roles(subject, roles_module):
    """Expand a rule's `subject` field into the set of role ids that can earn it.

    `roles_module` is passed in rather than imported so this stays a pure function of the
    registry it is given — the tests exercise the parser without standing up anything else.

    Returns a frozenset. `any` expands to every role, because "any player can earn this"
    and "every role is a subject" are the same statement once the output is per player.
    """
    if not subject:
        return frozenset()
    expanded = set()
    for token in subject.split(","):
        token = token.strip()
        if not token:
            continue
        if token == ANY:
            expanded.update(roles_module.ROLES)
        elif token.startswith(TAG_PREFIX):
            expanded.update(roles_module.with_tag(token[len(TAG_PREFIX) :]))
        elif token.startswith(TEAM_PREFIX):
            expanded.update(roles_module.in_team(token[len(TEAM_PREFIX) :]))
        else:
            expanded.add(token)
    return frozenset(expanded)
