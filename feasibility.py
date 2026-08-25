"""Which achievements a role composition can still produce, and for whom.

Takes the roles players have revealed and answers, per player, "what is still on the table
for you". Two halves, kept apart on purpose (see rulelist.py):

* the rule's **expr** is evaluated once per composition — it asks about the game, not the
  player, so evaluating it per player would be the same answer computed twenty times;
* the rule's **subject** is matched per player, against the roles they could still end up
  as rather than only the one they reported.

That second point is most of the value. A Cursed player's own achievements are thin, but
the wolf they may become has plenty, and a list that hid those would be wrong in the
direction that matters — it would tell someone nothing is possible when a great deal is.
`reachable_roles()` is the single place that decides what a player could become, so the
renderer and the rule matcher can never disagree about it.

**Counting is optimistic, twice over.** A player who answered `/role sf` is counted as both
a Seer and a Fool, and conversions are counted at their ceiling rather than their start —
the cult recruits, the Alpha bites, the bar makes drunks. Both follow from what this list is
for: it says what *could* happen, so the honest failure is to overstate rather than to hide
something that turns out to be reachable. The tier (`check` vs `maybe`) is what carries the
difference between "the game can do this" and "the game can do this if it cooperates".

The expressions themselves come from the database and are editable at runtime, so they are
evaluated in a sandbox: no builtins, no attribute access, and only the vocabulary registered
in `_functions()` below. A rule that raises is dropped with a log line rather than taking
the whole post down — one broken expression must not cost the other 108 rules.
"""

import structlog
from simpleeval import EvalWithCompoundTypes

import roles as roles_registry
import rulelist

logger = structlog.get_logger(__name__)


class Composition:
    """A game's roles, with the counts every rule expression is asked about.

    Built from an iterable of per-player role candidates: one role id each, or two for the
    unresolved Seer/Fool pair. Everything here counts *players*, never candidate roles, so
    an `sf` player is one player who happens to satisfy two role questions rather than two
    players.
    """

    __slots__ = ("candidates", "players", "roles")

    def __init__(self, player_roles):
        # Normalised to tuples so a caller passing lists (as a persistence round-trip
        # produces) cannot change behaviour.
        self.candidates = tuple(tuple(candidate) for candidate in player_roles)
        self.players = len(self.candidates)
        self.roles = frozenset(role for candidate in self.candidates for role in candidate)

    # --- Counting ----------------------------------------------------------

    def count(self, *names):
        """Players who could be any of `names`."""
        wanted = set(names)
        return sum(1 for candidate in self.candidates if wanted.intersection(candidate))

    def count_tag(self, tag):
        """Players carrying `tag` on at least one of their candidate roles."""
        return sum(1 for candidate in self.candidates if any(roles_registry.has_tag(role, tag) for role in candidate))

    def count_team(self, team):
        return sum(
            1 for candidate in self.candidates if any(roles_registry.team_of(role) == team for role in candidate)
        )

    def present(self, *names):
        return bool(self.roles.intersection(names))

    def all_present(self, *names):
        return self.roles.issuperset(names)

    def distinct_roles(self):
        return len(self.roles)

    def distinct_tagged_roles(self, tag):
        return sum(1 for role in self.roles if roles_registry.has_tag(role, tag))

    # --- Reachable ceilings ------------------------------------------------
    #
    # Roles convert, so a starting count is a floor and several achievements ask about a
    # population that is only ever reached by converting. Counting what was dealt would
    # make "Cultist Convention" (ten living cultists) permanently unreachable, since no
    # game is ever dealt ten.

    def max_possible_wolves(self):
        """The most wolves that could ever be alive at once.

        An Alpha Wolf lifts this to everybody: its bite turns whoever the pack eats, so
        with one in play no other role caps the count. Without one, the ceiling is the
        pack plus the roles that turn on their own (Cursed, Wild Child, Traitor) plus a
        Doppelgänger copying any of them.
        """
        if self.present("alpha_wolf"):
            return self.players
        reachable = self.count_tag(roles_registry.PACK)
        reachable += self.count_tag(roles_registry.POTENTIAL_WOLF)
        reachable += self.count("doppelganger")
        return min(reachable, self.players)

    def cultable_count(self):
        """Players the cult could convert. The immune roles are what cap the cult's size."""
        return sum(
            1
            for candidate in self.candidates
            if any(not roles_registry.has_tag(role, roles_registry.CULT_IMMUNE) for role in candidate)
        )

    def max_possible_cultists(self):
        """Zero without a cultist to do the recruiting; otherwise everyone convertible.

        The starting cultists are themselves convertible-by-definition and so are already
        counted — they are not cult-immune.
        """
        if not self.present("cultist"):
            return 0
        return self.cultable_count()

    def max_possible_drunks(self):
        """Dealt Drunks, plus the ones the bar creates.

        "Every night you open your bar for all the lowly villagers", and three nights of it
        makes a drunk — so a Barkeep plus plain Villagers is a source of drunks, and
        "Alcoholics Anonymous" (three drunks alive at the end) is reachable well below
        three dealt Drunks. Villagers specifically: the village *team* does not drink here.
        """
        drunks = self.count("drunk") + self.count("doppelganger") + self.count("thief")
        if self.present("barkeep"):
            drunks += self.count("villager")
        return min(drunks, self.players)

    def attackers(self):
        """Roles that come for you in the night, in the sense "Helpful Paranoia" means.

        Wolves, the players who could still become wolves, and cultists. Explicitly not the
        Sorcerer, who is wolf-team and attacks nobody.
        """
        return (
            self.count_tag(roles_registry.PACK) + self.count_tag(roles_registry.POTENTIAL_WOLF) + self.count("cultist")
        )

    def max_burnable_houses(self):
        """Houses the Arsonist could douse: everyone's but their own, and not the SK's.

        Per the group's ruling — the serial killer's house cannot be doused. Both
        subtractions matter at the thresholds: "Firework" wants ten, so a game of eleven
        with a serial killer in it is exactly one house short.
        """
        burnable = self.players - 1
        if self.present("serial_killer"):
            burnable -= 1
        return max(burnable, 0)


def reachable_roles(candidates, composition):
    """Every role a player could still end up as, including the one they reported.

    This is what stops a Cursed player's list from being nearly empty. Conversions are
    listed as reachable, not certain — the renderer marks them apart from ordinary rows,
    because "you can earn this" and "you can earn this if the wolves eat you" are different
    promises.

    Deliberately narrow in two places, both from the game's own rules: a Cursed or Wild
    Child becomes a plain Werewolf and never an Alpha, and the Thief cannot rob a wolf, the
    serial killer or a cultist — but *can* rob the Arsonist or the Sorcerer.
    """
    reachable = set(candidates)

    # Turning into a wolf: by your own role, or by anyone's bad luck when an Alpha is in
    # play. Plain Werewolf either way — the bite does not make Alphas.
    turns = any(roles_registry.has_tag(role, roles_registry.POTENTIAL_WOLF) for role in candidates)
    if turns or composition.present("alpha_wolf"):
        reachable.add("werewolf")

    # The Doppelgänger copies whoever it shadowed; the Thief steals what it can reach.
    if "doppelganger" in candidates:
        reachable.update(composition.roles)
    if "thief" in candidates:
        reachable.update(
            role for role in composition.roles if not roles_registry.has_tag(role, roles_registry.STEAL_IMMUNE)
        )

    # The bar turns lowly villagers into drunks.
    if "villager" in candidates and composition.present("barkeep"):
        reachable.add("drunk")

    return frozenset(reachable)


def _functions(composition):
    """The vocabulary a rule expression may use, bound to one composition.

    A whitelist, not a convenience: these expressions come out of the database and can be
    edited at runtime, so the sandbox's guarantee is only as good as what is put in it.
    Everything here is a plain read over the composition — nothing writes, does I/O, or
    reaches the bot.

    Deliberately broader than the canonical catalogue uses. Rules are meant to be edited in
    place, and an admin writing a new one needs vocabulary to write it *with*; a function
    nobody calls yet costs nothing.
    """
    return {
        "ispresent": composition.present,
        "all_present": composition.all_present,
        "count": composition.count,
        "team_count": composition.count_team,
        "tag_count": composition.count_tag,
        "pack_count": lambda: composition.count_tag(roles_registry.PACK),
        "killers": lambda: composition.count_tag(roles_registry.KILLER),
        "bad_count": lambda: composition.count_tag(roles_registry.BAD),
        "village_count": lambda: composition.count_team(roles_registry.VILLAGE),
        "visitor_count": lambda: composition.count_tag(roles_registry.VISITOR),
        "distinct_roles": composition.distinct_roles,
        "distinct_bad_roles": lambda: composition.distinct_tagged_roles(roles_registry.BAD),
        "max_possible_wolves": composition.max_possible_wolves,
        "max_possible_cultists": composition.max_possible_cultists,
        "max_possible_drunks": composition.max_possible_drunks,
        "cultable_count": composition.cultable_count,
        "attackers": composition.attackers,
        "max_burnable_houses": composition.max_burnable_houses,
    }


def evaluate(expr, composition):
    """Evaluate one rule expression against a composition. Never raises.

    A rule that blows up is dropped and logged rather than propagated: these are editable
    at runtime, so one bad expression is a normal operational event, and taking the whole
    Possible Achievements post down over it would be a poor trade for the other 108 rules.
    """
    evaluator = EvalWithCompoundTypes(
        names={"players": composition.players, "roles": composition.roles},
        functions=_functions(composition),
    )
    try:
        return bool(evaluator.eval(expr))
    except Exception as exc:
        # Deliberately everything. An enumerated list of exception types looks tidier and
        # is wrong: simpleeval raises InvalidExpression for its own refusals, but the
        # *parse* happens first and a malformed expression comes out as a plain
        # SyntaxError, which no amount of guessing at simpleeval's hierarchy would have
        # caught. These strings are admin-editable, so "some exception nobody predicted"
        # is a normal input, not an impossible state.
        logger.warning("rule_eval_failed", expr=expr, error=str(exc))
        return False


def validate(expr):
    """Check an expression before storing it. Returns (ok, error_message).

    /setrule validates on write rather than discovering the problem at render time, when
    the only signal would be an achievement quietly missing from a list. The probe
    compositions are deliberately awkward — an empty game divides the ground out from under
    anything assuming players exist.
    """
    for probe in (Composition(()), Composition([("villager",)] * 3), _KITCHEN_SINK):
        evaluator = EvalWithCompoundTypes(
            names={"players": probe.players, "roles": probe.roles},
            functions=_functions(probe),
        )
        try:
            evaluator.eval(expr)
        except Exception as exc:  # noqa: BLE001 - the caller wants the message, whatever it is
            return False, "{}: {}".format(type(exc).__name__, exc)
    return True, ""


def passing_rules(composition, rules):
    """The rules whose expression holds for this composition: name -> rule.

    Skipped rules are dropped here rather than filtered by every caller, so "in this dict"
    means "listable".
    """
    passing = {}
    for name, rule in rules.items():
        if rule["tier"] == rulelist.SKIP:
            continue
        if evaluate(rule["expr"], composition):
            passing[name] = rule
    return passing


def feasible(player_roles, rules):
    """What each player could still earn.

    `player_roles` maps a caller's own key (a Telegram user id, in practice) to that
    player's revealed role candidates. Returns `(per_player, universal)`:

    * `per_player` — key -> list of {name, tier, swing} in the rules' own order, where
      `swing` marks a row reachable only through a role change;
    * `shared` — the achievements whose subject is *anyone*, as {name, tier}.

    The split exists because "anyone can earn this" and "you can earn this" look identical
    once printed under a name. A rule like Sunday Bloody Sunday belongs to no role at all,
    so repeating it under each of sixteen players says the same thing sixteen times and
    crowds out the rows that are actually about that player. Said once, it reads as what it
    is: a fact about the game.
    """
    composition = Composition(player_roles.values())
    passing = passing_rules(composition, rules)

    # Subject "any" is the whole test for shared, which also covers every `always` rule —
    # those are written with subject `any` too, because "no role gate" and "every role is
    # a subject" are the same statement.
    shared = [
        {"name": name, "tier": rule["tier"]}
        for name, rule in passing.items()
        if rule["subject"].strip() == rulelist.ANY
    ]
    shared_names = {entry["name"] for entry in shared}

    per_player = {}
    for key, candidates in player_roles.items():
        candidates = tuple(candidates)
        reachable = reachable_roles(candidates, composition)
        own = set(candidates)
        entries = []
        for name, rule in passing.items():
            if name in shared_names:
                continue
            subject = rulelist.subject_roles(rule["subject"], roles_registry)
            if not subject.intersection(reachable):
                continue
            entries.append(
                {
                    "name": name,
                    "tier": rule["tier"],
                    # True when only a role change gets them there, so the renderer can
                    # say "if you turn" rather than implying it is available now.
                    "swing": not subject.intersection(own),
                }
            )
        per_player[key] = entries
    return per_player, shared


# Every role at once. Used by validate() as a probe, and by the tests as the composition in
# which nothing should be gated out.
_KITCHEN_SINK = Composition([(role_id,) for role_id in roles_registry.ROLES])
