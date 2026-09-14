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

A rule says only what the *roles* make possible. It deliberately does not grade how much
the game still has to cooperate — whether the Snow Wolf will actually choose the harlot,
whether the bite lands — because the people at the table judge that far better than a
stored word can, and a list that hedged every row read as though nothing was really on.
Two rules follow from that and are worth stating outright:

* **the subject is the on switch.** An achievement no role can be the subject of is never
  listed, which is what `_skip` writes: an empty subject and `False`. Everything else is
  listed whenever its expression holds.
* **subject `any` is the whole of "no role gate".** Those achievements are summarised
  once at the foot of the post rather than repeated under all twenty players.

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

# Subject prefixes. Bare tokens are role ids.
ANY = "any"
TAG_PREFIX = "tag:"
TEAM_PREFIX = "team:"


def _rule(name, subject, expr, note):
    return {"name": name, "subject": subject, "expr": expr, "note": note}


def is_listed(rule):
    """Whether a rule can put an achievement under anybody.

    The **subject is the switch**: an achievement nobody can be the subject of is one
    nothing will ever render, so a rule opted out by `_skip` carries an empty subject and
    needs no second field agreeing with it. There used to be a `tier` alongside
    (`check`/`maybe`/`always`/`skip`) and it answered a question this catalogue is not
    for — how much the game still has to cooperate — which is a judgement the players at
    the table make far better than a stored string can. What is left is the half that is
    a fact about the roles: can this composition produce it, and for whom.
    """
    return bool(rule["subject"].strip())


def _skip(name, note):
    """A rule that exists only so the achievement is accounted for.

    Every achievement must appear here — tests/test_rules.py asserts it both ways — so
    that a new one added to achvlist.py fails the suite instead of quietly never being
    listed. Skipped rows carry the reason, since "why is this never offered" is the
    question someone will eventually ask.
    """
    return _rule(name, "", "False", note)


# In achvlist.ACHV order, so the seeded table reads the same way the achievement list does.
RULES = [
    _rule("Welcome to Hell", ANY, "True", "Playing is the whole condition."),
    _skip("Welcome to the Asylum", "Chaos mode, which we cannot observe from a role list."),
    _skip("Alzheimer's Patient", "Amnesia language pack — a game setting, not a role."),
    _skip("O HAI DER!", "Requires a specific account to be in the game."),
    _skip("Spy vs Spy", "Secret mode — a game setting, not a role."),
    _skip("Explorer", "Inactive, and counts groups across games."),
    _skip("Linguist", "Inactive, and counts language packs across games."),
    _skip("I Have No Idea What I'm Doing", "Secret amnesia mode — a game setting."),
    _rule("Enochlophobia", ANY, "players >= 35", "Purely a player count."),
    _rule("Introvert", ANY, "players == 5", "Exactly five, so a sixth player rules it out."),
    _skip("Naughty!", "NSFW language pack — a game setting."),
    _skip("Dedicated", "100 games, cumulative across games."),
    _skip("Obsessed", "1000 games, cumulative across games."),
    _skip("Here's Johnny!", "50 kills across games (not_via_playing)."),
    _skip("I've Got Your Back", "50 saves across games (not_via_playing)."),
    _rule("Masochist", "tanner", "True", "Needs the win as well as the role."),
    _rule(
        "Wobble Wobble",
        "drunk",
        "max_possible_drunks() >= 1 and players >= 10",
        "A Barkeep turns a lowly villager into a drunk, so the Drunk role is not the only source.",
    ),
    _rule("Inconspicuous", ANY, "players >= 20", "Player count gates it; the rest is behaviour."),
    _skip("Survivalist", "Survive 100 games, cumulative."),
    _skip("Black Sheep", "Inactive, and a streak across games."),
    _rule("Promiscuous", "harlot", "True", "Also needs a 5+ night game, which roles cannot predict."),
    _rule(
        "Mason Brother",
        "mason",
        "ispresent('mason') and count('mason','doppelganger','thief') >= 2",
        "Two surviving masons — dealt, or one plus a Doppelganger who copied them or a Thief who stole "
        "the role. The Mason has to be there either way: adding the copiers to the count without "
        "requiring one said two Doppelgangers and no Mason could make a pair of masons.",
    ),
    _rule(
        "Double Shifter",
        "doppelganger,thief",
        "ispresent('alpha_wolf') or tag_count('potential_wolf') >= 1 or (ispresent('barkeep') and count('villager') >= 1)",
        "Two role changes in one game, which needs a role that takes an identity *and* a second "
        "thing to change it again. The Cursed, the Traitor and the Wild Child each turn once and "
        "then they are wolves — carrying the role_swing tag is not the same as swinging twice. The "
        "Doppelgänger and the Thief take one identity and can then be bitten by the Alpha, turn "
        "because what they took was a Cursed or a Traitor, or drink their way from Villager to Drunk.",
    ),
    _rule(
        "Hey Man, Nice Shot",
        "hunter",
        "max_possible_wolves() > 0 or ispresent('serial_killer')",
        "The dying shot must land on a wolf or the serial killer — a Sorcerer is neither, and a "
        "wolf that only arrives by conversion is still a wolf to shoot.",
    ),
    _rule(
        "That's Why You Don't Stay Home",
        "tag:pack,cultist",
        "ispresent('harlot')",
        "Someone has to be the harlot who stayed home.",
    ),
    _rule(
        "Double Vision",
        "apprentice_seer,doppelganger",
        "all_present('seer','apprentice_seer','doppelganger')",
        "Two live seers at once: the Seer's death promotes the Apprentice and turns a Doppelganger "
        "who copied them into a second Seer. Without all three there is never more than one.",
    ),
    _rule(
        "Double Kill",
        "serial_killer,hunter",
        "all_present('serial_killer','hunter')",
        "The ending needs both halves in play.",
    ),
    _rule("Should Have Known", "seer", "ispresent('beholder')", "There must be a Beholder to reveal."),
    _rule("I See a Lack of Trust", "seer", "True", "Day-one lynch; nothing else in the composition gates it."),
    _rule(
        "Sunday Bloody Sunday",
        ANY,
        "ispresent('arsonist') "
        "or night_killers() >= 2 "
        "or (night_killers() >= 1 and count('harlot','guardian_angel') >= 2)",
        "Four deaths in one night: an arsonist, who can burn a street at once; or two hands that kill "
        "after dark; or one hand and the two callers who die alongside its victim, the Harlot and the "
        "Guardian Angel — the same two Triple Kill counts, because they die by the same rule. "
        "`killers()` was the wrong count twice over: it carries the Gunner and the Hunter, neither of "
        "whom has ever killed anybody after dark, and two of them is not the only way four people "
        "fall in one night.",
    ),
    _rule(
        "Change Sides Works",
        "tag:role_swing",
        "True",
        "A role change plus a win. Anybody the Alpha bites has also changed roles and is not listed: "
        'a subject cannot say "only if you got here by turning", and naming the Werewolf would put '
        "the row under every wolf that was dealt one.",
    ),
    _rule(
        "Forbidden Love",
        "villager,tag:pack",
        "all_present('cupid','villager') and max_possible_wolves() > 0",
        "'villager, not village team' is literal: the couple must be a wolf and a plain Villager, "
        "and Cupid has to pair them.",
    ),
    _skip("Developer", "A merged pull request — not gameplay."),
    _rule("The First Stone", ANY, "True", "Voting behaviour, no role gate."),
    _rule(
        "Smart Gunner",
        "gunner",
        "pack_count() + count('serial_killer','cultist') >= 2",
        "Both bullets must hit a wolf, serial killer or cultist, so two of those have to exist. "
        "`bad_count()` is the wrong count for it: the tag also carries the Sorcerer and the Arsonist, "
        "and a bullet in either of them earns nothing.",
    ),
    _rule(
        "Streetwise",
        "detective",
        "pack_count() + count('serial_killer','cultist') >= 4",
        "Four nights in a row finding a *different* one — a different *player*, not a different role, "
        "so four of them have to exist. Counting distinct roles instead let a game with four wolves "
        "fail the test and a game with one of each of four kinds pass it. The trio is the one the "
        "achievement names: the Sorcerer and the Arsonist are bad roles the Detective's find is not "
        "about.",
    ),
    _rule("Speed Dating", ANY, "ispresent('cupid')", "The bot only picks lovers when Cupid failed to."),
    _rule("Even a Stopped Clock is Right Twice a Day", "fool", "True", "Two correct visions, by luck."),
    _rule("So Close!", "tanner", "True", "A vote tie, which no composition can predict."),
    _rule(
        "Cultist Convention",
        "cultist",
        "max_possible_cultists() >= 10",
        "Ten living cultists is reached by recruiting, never by dealing — and the cult-immune roles "
        "cap how large it can get.",
    ),
    _rule("Self Loving", "cupid", "True", "Cupid's own choice."),
    _rule("Should've Said Something", "tag:pack", "ispresent('cupid')", "The pack must have a lover to eat."),
    _rule("Tanner Overkill", "tanner", "True", "A unanimous lynch, which is behaviour."),
    _rule(
        "Serial Samaritan",
        "serial_killer",
        "max_possible_wolves() >= 3",
        "Three wolves to kill — counting the ones the Alpha or a conversion could still create.",
    ),
    _rule(
        "Cultist Fodder",
        "cultist",
        "ispresent('cultist_hunter')",
        "The cult has to have a Cult Hunter to send someone against.",
    ),
    _rule(
        "Lone Wolf",
        "tag:pack",
        "pack_count() == 1 and players >= 10",
        "Chaos-mode only per its description, which is safe to ignore here: this group always plays "
        "chaos, so the role condition is the whole gate. The count is of the pack, not the team — a "
        "lone Werewolf with a Sorcerer beside them is still the only wolf, and asking the team "
        "disqualified exactly that game.",
    ),
    _rule(
        "Pack Hunter",
        "tag:pack,tag:potential_wolf",
        "max_possible_wolves() >= 7",
        "Seven living wolves at once, counting conversions — a game is never dealt seven.",
    ),
    _rule(
        "Saved by the Bull(et)",
        "team:village",
        "ispresent('gunner') and max_possible_wolves() > 0",
        "Wolves must reach parity with the village while the Gunner still holds a bullet.",
    ),
    _rule("In for the Long Haul", ANY, "True", "An hour of wall clock, no role gate."),
    _rule(
        "OH SHI-",
        "tag:pack,tag:night_killer",
        "ispresent('cupid')",
        "You must have a lover to kill on night one — and be able to kill on a night at all. The "
        "Gunner and the Hunter carry the killer tag and fire only by day, so both were being offered "
        "an achievement neither can reach.",
    ),
    _skip("Veteran", "500 games, cumulative."),
    _rule("No Sorcery!", "tag:pack", "ispresent('sorcerer')", "There has to be a sorcerer to eat."),
    _rule(
        "Cultist Tracker",
        "cultist_hunter",
        "max_possible_cultists() >= 3",
        "Three cultists to kill, which the cult reaches by recruiting from the convertible players. "
        "Said in the one helper that already means both halves — no cult, no ceiling.",
    ),
    _rule("I'M NOT DRUN-- *BURPPP*", "clumsy", "True", "Three correct lynches, half of them by coin flip."),
    _rule(
        "Wuffie-Cult",
        "alpha_wolf",
        "players - pack_count() >= 3",
        "Three successful bites needs three bodies to bite, and the pack is not among them — a "
        "five-player game that is three-quarters wolves has one candidate, not five.",
    ),
    _rule(
        "Did you guard yourself?",
        "guardian_angel",
        "max_possible_wolves() > 0",
        "There must be a wolf to guard, three times, and survive it — counting the ones a bite or a "
        "Wild Child's turn could still make.",
    ),
    _rule("Spoiled Rich Brat", "prince", "True", "The village has to lynch them twice."),
    _rule(
        "Three Little Wolves and a Big Bad Pig",
        "sorcerer",
        "max_possible_wolves() >= 3",
        "Three living wolves alongside a surviving sorcerer.",
    ),
    _rule("President", "mayor", "True", "Three votes after revealing — behaviour."),
    _rule(
        "I Helped!",
        "wolf_cub",
        "max_possible_wolves() >= 2",
        "The pack has to outlive the cub to make the two eats — but the wolf that outlives "
        "it need not be one at the start. A game dealt a lone Wolf Cub reaches a second wolf "
        "the moment the cub dies and the Traitor turns, which is exactly when this fires. "
        "Counting the pack as dealt missed that and never offered it.",
    ),
    _rule(
        "It Was a Busy Night!",
        ANY,
        "distinct_visiting_roles() >= 3",
        "Three *different* visiting roles in one night, so three must be in the game. Counting "
        "visiting players instead passed a game whose only visitors were three werewolves, which is "
        "one role three times over. Contrast Traffic Control, which counts people and so counts "
        "players.",
    ),
    _rule("Strongest Alpha", "alpha_wolf", "ispresent('serial_killer')", "The serial killer is the target."),
    _rule("Am I Your Seer?", "fool", "ispresent('beholder')", "There must be a Beholder to spot."),
    _rule("Demoted by the Death", "hunter", "ispresent('wise_elder')", "The final shot must hit a Wise Elder."),
    _rule("Wasted Silver", "blacksmith", "ispresent('sandman')", "Both abilities must land on the same day."),
    _rule(
        "Trustworthy!",
        "wolfman",
        "ispresent('seer')",
        "The point is surviving *after being checked*, so a Seer has to exist to check them.",
    ),
    _rule(
        "Deep Love",
        "doppelganger",
        "ispresent('cupid')",
        "Choosing your lover as your role model needs Cupid to have made you a lover first.",
    ),
    _rule("Time to retire...", "sorcerer", "True", "Last alive and losing — an outcome, not a composition."),
    _rule(
        "Seeing between Teams",
        "seer,sorcerer",
        "all_present('seer','sorcerer','cupid')",
        "A seer/sorcerer couple needs Cupid to pair them.",
    ),
    _rule("Just a Beardy Guy..?", "wolfman", "ispresent('alpha_wolf')", "Only the Alpha's bite can turn them."),
    _rule(
        "That Came Unexpected!",
        "tanner",
        "players >= 3",
        "Lynched with only three left, which a three-player game is already at.",
    ),
    _rule(
        "Now I'm Blind",
        "oracle",
        "True",
        "The vision fails when everyone else shares one role, and every game reaches that: with two "
        'players left "everyone else" is one person, who trivially all have the same role. The '
        "achievement's own note describes it — three players, the Oracle checks the killer while the "
        "killer takes the third. Asking for a cult or a two-role game withheld it from the games it "
        "is easiest in.",
    ),
    _rule("Every Man for Himself!", "pacifist", "True", "Saving yourself from a lynch in progress."),
    _rule(
        "My Sweetie so Strong!",
        ANY,
        "all_present('pacifist','cupid')",
        "You must be in love with the pacifist, so Cupid has to pair you.",
    ),
    _rule(
        "Cult Leader",
        "cultist",
        "True",
        "Survive and win as an original cultist. A Doppelgänger who copied one is offered it as a "
        'role change and cannot really earn it — "from the beginning" is a fact about the deal, and '
        "the subject has no way to say so.",
    ),
    _rule(
        "Thanks, Junior!",
        "wild_child,doppelganger",
        "max_possible_drunks() >= 1 and max_possible_wolves() > 0",
        "You turn wolf the night the pack eats the Drunk — reachable via a Wild Child whose role "
        "model was eaten or a Doppelganger's copy, but never via the Cursed or the Traitor.",
    ),
    _rule("Death Village", ANY, "True", "A game that ends with nobody winning."),
    _rule(
        "I Lost my Wisdom",
        "wise_elder",
        "ispresent('thief','alpha_wolf')",
        "Something must be able to change their role: a theft or the Alpha's bite.",
    ),
    _rule("Affectionate", "harlot", "ispresent('cupid')", "You need a lover to visit."),
    _rule(
        "Lucky Day",
        "alpha_wolf",
        "max_possible_drunks() >= 1",
        "Infecting the drunk and staying sober needs a drunk to infect.",
    ),
    _rule("Condition Red!", "tag:pack", "ispresent('traitor')", "The last wolf must have a traitor to eat."),
    _rule(
        "Indestructible",
        "doppelganger,wild_child,thief",
        "all_present('doppelganger','wild_child') or (ispresent('thief') and ispresent('doppelganger','wild_child'))",
        '"*Become* Doppelgänger or Wild Child with your role model being yourself" needs two of '
        "these roles, never one: one of the pair has to already be pointing at you when you take the "
        "other's identity. So the Doppelgänger and the Wild Child together, or a Thief who steals "
        "either of them. A game with a lone Doppelgänger was being offered it and there is no route.",
    ),
    _rule("Psychopath Killer", "serial_killer", "players >= 35", "A 35-player win."),
    _skip("Today's Special!", "An event-only role, absent from the standard /rolelist."),
    _rule(
        "Romeo and Juliet",
        ANY,
        "all_present('tanner','cupid')",
        "Being in love with the tanner requires Cupid as well as the Tanner.",
    ),
    _rule(
        "Really bad luck",
        "serial_killer",
        "all_present('grave_digger','guardian_angel')",
        "Stumbling into *a grave* needs the Grave Digger, and being fought off needs the angel. "
        "Both, or the sequence cannot happen.",
    ),
    _rule(
        "Domino",
        "hunter",
        "count('hunter') >= 2 or (ispresent('hunter') and ispresent('doppelganger','thief'))",
        "A second hunter to shoot — dealt, or made by a Doppelganger copying the first or a Thief "
        "stealing the role. Either copier needs a Hunter to copy, which the expression now says.",
    ),
    _rule(
        "Double Shot",
        "hunter,gunner",
        "ispresent('cupid') and bad_count() >= 2",
        "The target must be a bad role *in love with another bad role*, so Cupid and two bad roles.",
    ),
    _rule("Playing with the Fire", "arsonist", "max_burnable_houses() >= 5", "Five houses that can be doused."),
    _rule("Firework", "arsonist", "max_burnable_houses() >= 10", "Ten houses that can be doused."),
    _rule("Cold as Ice", "snow_wolf", "ispresent('harlot')", "The harlot is the one who has to be frozen."),
    _rule("Good Choice... For You", "chemist", "players >= 4", "Three surviving visits needs targets."),
    _rule(
        "Increase the Pack!",
        "alpha_wolf",
        "ispresent('wolf_cub') and players - pack_count() >= 2",
        "The cub has to die first, and there have to be two players outside the pack left to infect.",
    ),
    _rule(
        "Firefighter",
        "guardian_angel",
        "ispresent('arsonist') and max_burnable_houses() >= 3",
        "Three houses of kerosene to clean, so three the arsonist could have doused in the first "
        "place — their own and the serial killer's are not among them.",
    ),
    _rule(
        "Helpful Paranoia",
        "hunter",
        "ispresent('cultist') or (pack_count() > 0 and ispresent('wild_child','traitor','doppelganger'))",
        "Two attackers shot, which the game reaches two ways. A cult sends somebody against the "
        "Hunter, loses them, and sends the next — so a cult on its own is enough. Or a wolf attacks, "
        "dies to the shot, and *something turns in the same moment it dies* to come again: the "
        "Traitor, a Wild Child whose role model has gone, or a Doppelganger who copied the wolf. "
        "Confirmed at the table, and the exclusion is the point — the **Cursed cannot be the second "
        "attacker**, because a living wolf has to bite them and the only wolf has just been shot. "
        "A count of everything that could ever attack said otherwise: it made a lone cult "
        "insufficient and a wolf-plus-Cursed enough, and both were backwards.",
    ),
    _rule(
        "S-Tier Hunter",
        "hunter",
        "max_possible_wolves() > 0 and ispresent('cultist')",
        "One of each, in the same night.",
    ),
    _rule(
        "Triple Kill",
        "serial_killer,tag:pack",
        "(ispresent('serial_killer') and tag_count('visitor') - count('serial_killer') >= 2) "
        "or (max_possible_wolves() > 0 and count('harlot','guardian_angel') >= 2) "
        "or (ispresent('wolf_cub') and max_possible_wolves() >= 2 and count('harlot','guardian_angel') >= 1)",
        "Three deaths by one hand in one night, and the two hands get there differently — which is "
        "why a player count was never the gate. The serial killer kills their target and everybody "
        "who called on them that night, so any two other visiting roles will do. The wolves take "
        "only their victim and the two callers who die for visiting a wolf, the Harlot and the "
        "Guardian Angel, so both must be dealt — *unless* there is a Wolf Cub, whose death buys the "
        "pack a second eat and leaves one visitor enough. That last route is the one route the Cub "
        "itself can never take: it is the one who had to die for it.",
    ),
    _rule(
        "Resist the Beast",
        "wild_child,traitor,cursed",
        "all_present('wild_child','traitor','cursed')",
        "The achievement names the trio outright; all three must be dealt.",
    ),
    _rule(
        "At least you tried...",
        "guardian_angel",
        "ispresent('chemist')",
        "The saved player has to die to the chemist's poison.",
    ),
    _rule(
        "Lucky Night",
        ANY,
        "all_present('chemist','harlot')",
        "Both visits, in the same night, to the same player.",
    ),
    _rule(
        "In the Middle of the Trouble",
        "guardian_angel",
        "max_possible_wolves() > 0 and ispresent('serial_killer','arsonist')",
        "Saving a werewolf means something else has to be attacking one. Not the Chemist: the angel's "
        'guard does not stop the poison, which is the whole of "At least you tried...".',
    ),
    _rule(
        "Am I hallucinating?!",
        "fool",
        "ispresent('traitor','wolfman','lycan')",
        "The Seer reads a Wolf Man as a wolf, a Lycan as a villager and a Traitor as a villager, so "
        "those three are the roles a real vision can never report — seeing one proves you are the "
        "Fool. The Lycan is the Wolf Man's mirror and was missed for exactly that reason.",
    ),
    _rule(
        "Going Down with my Beer",
        "villager",
        "all_present('barkeep','arsonist')",
        "A bar to drink in and a fire to die in.",
    ),
    _rule(
        "Alcoholics Anonymous",
        "drunk",
        "max_possible_drunks() >= 3",
        "Three drunks alive at the end. The Barkeep manufactures them from lowly villagers, so this "
        "is reachable far below three dealt Drunks.",
    ),
    _rule(
        "Liquid Business",
        "barkeep",
        "count('villager') >= 3",
        "'Visited by 3 or more villagers' is literal — the bar opens for lowly villagers, not the village team.",
    ),
    _rule("Traffic Control", "chef", "tag_count('visitor') >= 3", "Three visits to one player in one night."),
    _rule(
        "Definitely Dead",
        "chef",
        "night_killers() >= 1",
        "Somebody has to be murdered *that night*, so a game whose only killers fire by day cannot produce one.",
    ),
    _rule("Going Out Of Business", "barkeep", "players >= 10", "Ten players, and three empty nights."),
    _rule("Food Waste", "chef", "players >= 5", "Three players who stayed home and had no visitors."),
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
