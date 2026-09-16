"""Which achievements a role composition can still produce, and for whom.

Takes the roles players have revealed and answers, per player, "what is still on the table
for you". Three parts, kept apart on purpose (see rulelist.py):

* the rule's **expr** is evaluated once per composition — it asks about the game, not the
  player, so evaluating it per player would be the same answer computed twenty times;
* the rule's **subject** is matched per player, against the roles they could still end up
  as rather than only the one they reported;
* the rule's **player_expr**, where it has one, asks about the player themselves — not
  their role, but what the game has already decided about them. Most rules have none, and
  a rule without one behaves exactly as it always did.

That third part exists because roles are not the only thing a game settles. Cupid picks a
couple and the Wild Child picks a role model, and from that moment a dozen achievements are
closed to everybody those choices did not name — while the composition, which can only see
that a Cupid is in play, goes on offering them to all twenty players. `Facts` below is the
reading of those choices, and it is asked only about the rules that say they care.

A choice can also close an achievement no rule mentions, and then there is nothing to gate:
a Doppelgänger reached the Alpha's own achievements by being able to *copy* the Alpha, so
once they have named a Villager as their model the row goes away by itself —
`reachable_roles()` reads the same facts to say what a copy can still turn them into.

That second point is most of the value. A Cursed player's own achievements are thin, but
the wolf they may become has plenty, and a list that hid those would be wrong in the
direction that matters — it would tell someone nothing is possible when a great deal is.
`reachable_roles()` is the single place that decides what a player could become, so the
renderer and the rule matcher can never disagree about it.

**Counting is optimistic, twice over.** A player who answered `/role sf` is counted as both
a Seer and a Fool, and conversions are counted at their ceiling rather than their start —
the cult recruits, the Alpha bites, the bar makes drunks. Both follow from what this list is
for: it says what *could* happen, so the honest failure is to overstate rather than to hide
something that turns out to be reachable. What a rule never says is how *likely* any of it
is — see rulelist.py: the table judges that, and better.

The expressions themselves come from the database and are editable at runtime, so they are
evaluated in a sandbox: no builtins, no attribute access, and only the vocabulary registered
in `_functions()` below. A rule that raises is dropped with a log line rather than taking
the whole post down — one broken expression must not cost the other 108 rules.
"""

import structlog
from simpleeval import EvalWithCompoundTypes

from wwstatsbot.data import rulelist
from wwstatsbot.game import roles as roles_registry

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

        **A game needs a first wolf, and only two things are one.** Of the roles that
        turn, the Cursed needs a bite and the Traitor needs wolves to have existed and
        died — so neither can start a pack. Only a dealt pack member or a Wild Child
        whose role model dies can, and the Doppelgänger only ever copies what is already
        there. Counting all of them unconditionally said a game of a Cursed, a Traitor
        and fourteen villagers could produce a wolf: it offered the Guardian Angel a
        wolf to guard and the Hunter a wolf to shoot in a game that has none and can
        never have one.
        """
        if self.present("alpha_wolf"):
            return self.players
        pack = self.count_tag(roles_registry.PACK)
        if not pack and not self.count("wild_child"):
            return 0
        reachable = pack + self.count_tag(roles_registry.POTENTIAL_WOLF)
        reachable += self.count("doppelganger")
        return min(reachable, self.players)

    def max_live_wolves(self):
        """The most wolves that could be alive *at one time*, which is a different count.

        The **Traitor is not among them**. They turn only once every wolf is dead, so they
        are the pack's replacement and never its seventh member — a game dealt six wolves
        and a Traitor can produce seven wolves over its length and never seven at once.
        `max_possible_wolves()` counts them because most of the achievements asking about
        wolves ask how many a game can produce at all ("kill at least 3 wolves in a single
        game", and "I Helped!", which fires precisely *because* the Traitor turns as the
        cub dies). "Be one of 7 living wolves at one time" is the other question.

        Everything else that turns while the pack is still standing does count: the Cursed
        is bitten, the Wild Child's role model dies, the Doppelgänger copies a wolf.
        """
        if self.present("alpha_wolf"):
            return self.players
        pack = self.count_tag(roles_registry.PACK)
        if not pack and not self.count("wild_child"):
            return 0
        live = pack + self.count("cursed") + self.count("wild_child") + self.count("doppelganger")
        return min(live, self.players)

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

        The Doppelgänger and the Thief are counted the same way the wolf ceiling counts
        the Doppelgänger — only when a drunk is there to be copied or stolen, or the bar
        can make one. Neither can conjure a role the game did not deal.
        """
        drunks = self.count("drunk")
        if self.present("barkeep"):
            drunks += self.count("villager")
        if drunks:
            drunks += self.count("doppelganger") + self.count("thief")
        return min(drunks, self.players)

    def attackers(self):
        """Everything that could ever come for you in the night.

        Wolves, the players who could still become wolves, and cultists. Explicitly not the
        Sorcerer, who is wolf-team and attacks nobody.

        No canonical rule uses it, and "Helpful Paranoia" is why it is worth saying so: that
        rule was written as `attackers() >= 2` and the count is the wrong shape for it. Two
        attackers have to arrive *in sequence*, the second turning at the moment the first
        dies, which is a question about which roles can replace which — not about how many
        there are. Kept in the vocabulary because an admin writing a new rule needs
        something to write it with.
        """
        return (
            self.count_tag(roles_registry.PACK) + self.count_tag(roles_registry.POTENTIAL_WOLF) + self.count("cultist")
        )

    def night_killers(self):
        """Players who could choose to kill somebody after dark.

        Not `killers()`, which is every role that can cause a death by any route: the
        Gunner and the Hunter fire by day, so a game whose only killers are those two has
        no night deaths at all. The Hunter's return fire does land at night and is still
        not counted, because a reaction cannot be aimed — see roles.NIGHT_KILLER.
        """
        return self.count_tag(roles_registry.NIGHT_KILLER)

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


class Facts:
    """What the session knows about players, as the questions a rule may ask of one.

    The composition answers "can this *game* produce it". This answers the third question,
    the one neither a role nor a player count can reach: **has the game already decided
    something that closes it**. Cupid picks a couple, the Wild Child picks a role model —
    both happen inside a game that is still running, and both make achievements impossible
    for everybody they did not name while the roles alone still say otherwise.

    Deliberately optimistic in the same direction as everything else here: every question
    is "may", and an unknown answers yes. A session that has been told nothing about the
    couple must behave exactly as it did before any of this existed.
    """

    __slots__ = ("facts", "lovers", "models", "player_roles", "_models_known")

    def __init__(self, facts, player_roles):
        self.facts = facts or {}
        # Kept, not just counted: `model_roles()` has to answer *which* role a
        # recorded model is, and the model is stored as a player key.
        self.player_roles = {key: tuple(candidates) for key, candidates in (player_roles or {}).items()}
        self.lovers = {key for key, entry in self.facts.items() if entry.get("lover")}
        self.models = {entry.get("model") for entry in self.facts.values() if entry.get("model") is not None}
        self._models_known = self._all_models_set(player_roles)

    def _all_models_set(self, player_roles):
        """Whether every Doppelgänger and Wild Child we can see has named a model.

        One of them still to choose means the next model could be anybody, so the question
        is open for the whole table however many have already chosen. Read off the revealed
        composition rather than the roster: a player who has not revealed could be a Wild
        Child, and `player_roles` is the same "what we actually know" every other part of
        this module is built on.

        A recorded model is also required outright. Without it a game with no Doppelgänger
        and no Wild Child at all would answer "all of them have chosen" vacuously, and
        `may_be_own_model()` would then narrow a rule down to nobody rather than leaving it
        as the composition found it.
        """
        if not self.models:
            return False
        for key, candidates in (player_roles or {}).items():
            if "doppelganger" in candidates or "wild_child" in candidates:
                if self.facts.get(key, {}).get("model") is None:
                    return False
        return True

    # --- Love --------------------------------------------------------------

    def is_lover(self, key):
        return key in self.lovers

    def couple_known(self):
        """Whether both halves of the couple have been named.

        Two, not one. `/love` takes a bare player as well as a pair — the real manager
        marks each lover with a heart rather than announcing a couple — so one name told
        us who one lover is and nothing at all about who the other might be. Closing the
        question there would have taken every lover achievement off the fifteen players
        one of whom is the other half.
        """
        return len(self.lovers) >= 2

    def may_love(self, key):
        return self.is_lover(key) or not self.couple_known()

    def partner_known(self, key):
        return self.facts.get(key, {}).get("partner") is not None

    # --- Role models -------------------------------------------------------

    def is_model(self, key):
        return key in self.models

    def models_known(self):
        return self._models_known

    def may_be_own_model(self, key):
        return self.is_model(key) or not self.models_known()

    def model_known(self, key):
        return self.facts.get(key, {}).get("model") is not None

    def model_roles(self, key):
        """The roles this player's recorded model holds, or None while that is still open.

        Not a `may` question like the rest of this class, because it is not asked by a rule:
        `reachable_roles()` asks it to find out what a Doppelgänger can still turn into.
        None for every reason the copy cannot be pinned down — no model chosen, a model who
        has not revealed, a model no longer in the composition — and None means "anything",
        which is what a Doppelgänger with nothing recorded has always meant.
        """
        model = self.facts.get(key, {}).get("model")
        if model is None:
            return None
        return self.player_roles.get(model) or None

    def may_model_partner(self, key):
        """Whether this player's role model could still turn out to be their lover.

        Both halves have to be recorded before this can answer no: a model with no partner
        yet may be the partner-to-be, and a partner with no model chosen is a choice still
        to be made.
        """
        if not self.model_known(key) or not self.partner_known(key):
            return True
        entry = self.facts.get(key, {})
        return entry["model"] == entry["partner"]


def reachable_roles(candidates, composition, facts=None, key=None):
    """Every role a player could still end up as, including the one they reported.

    This is what stops a Cursed player's list from being nearly empty. Conversions are
    listed as reachable, not certain — the renderer marks them apart from ordinary rows,
    because "you can earn this" and "you can earn this if the wolves eat you" are different
    promises.

    Deliberately narrow in two places, both from the game's own rules: a Cursed or Wild
    Child becomes a plain Werewolf and never an Alpha, and the Thief cannot rob a wolf, the
    serial killer or a cultist — but *can* rob the Arsonist or the Sorcerer.

    `facts`/`key` are optional, and without them the answer is the one the roles alone give
    — which is what this returned before a session could say who had already chosen what.
    They narrow one thing: a Doppelgänger who has *named* their role model can only become
    that player, not anybody at the table.
    """
    reachable = set(candidates)

    # Turning into a wolf: by your own role, or by anyone's bad luck when an Alpha is in
    # play. Plain Werewolf either way — the bite does not make Alphas.
    #
    # And only when the game can have a wolf at all, which is the same question
    # `max_possible_wolves()` answers: the Cursed needs somebody to bite them and the
    # Traitor needs wolves to have existed and died, so in a game dealt neither a pack nor
    # a Wild Child, a Cursed player turns into nothing. Without the guard the two halves
    # disagreed — the count said no wolf was possible while this said the Cursed was one
    # away from being it, and the Cursed was listed for the wolves' achievements in a game
    # that has no wolves.
    turns = any(roles_registry.has_tag(role, roles_registry.POTENTIAL_WOLF) for role in candidates)
    if (turns or composition.present("alpha_wolf")) and composition.max_possible_wolves():
        reachable.add("werewolf")

    # The Doppelgänger copies whoever it shadowed — every role at the table until the
    # choice is made, and then exactly one of them.
    #
    # Narrowing it needs the session, because the composition can only see that a
    # Doppelgänger is playing. Unnarrowed it offered "Strongest Alpha" and "Increase the
    # Pack!" to a Doppelgänger who had picked a Villager as their role model, which the
    # game had already ruled out — and a chosen model is the *whole* of the choice: this is
    # not a `may` like the gates in `Facts`, it is the role they are going to copy.
    if "doppelganger" in candidates:
        reachable.update(_shadowable_roles(composition, facts, key))

    # A Thief in the game puts every stealable role within reach of everybody holding one,
    # not just of the Thief. The theft moves an identity between two players and neither
    # end of it is fixed in advance, so the question a list has to answer is "could this
    # achievement end up being yours", and with a Thief at the table it can: the Barkeep
    # who already has Liquid Business is not the only player who might be the Barkeep by
    # morning. Reaching it only from the Thief's own seat answered a narrower question than
    # the post is for, and left the other fifteen players' lists missing rows that were
    # genuinely on the table.
    #
    # Steal-immune both ways, which is the same list either way: the wolves, the cult and
    # the serial killer cannot be robbed, and a player who *is* one cannot be robbed out of
    # it either, so nothing is shuffled onto or off them. The Sorcerer and the Arsonist are
    # fair game — only actual wolves are protected.
    stealable = [role for role in composition.roles if not roles_registry.has_tag(role, roles_registry.STEAL_IMMUNE)]
    if composition.present("thief") and any(role in stealable for role in candidates):
        reachable.update(stealable)

    # The bar turns lowly villagers into drunks.
    if "villager" in candidates and composition.present("barkeep"):
        reachable.add("drunk")

    return frozenset(reachable)


def _shadowable_roles(composition, facts, key):
    """What a Doppelgänger's copy could turn them into: the whole table, or one player's lot.

    Once a model is named the copy is that player — but it is *their* whole lot, not the
    role they revealed, because the copy lands when the model dies and by then the model
    may have turned: a Cursed model eaten in the night is copied as the wolf they became.
    So the answer is the model's own reachable set, which is why this is the one place that
    recurses.

    It recurses **once**, deliberately: the inner call is made without facts, so a model
    who is themselves a Doppelgänger opens back up to the whole composition rather than
    chaining choices — the honest answer for a copy of a copy, and no cycle to walk into.

    The cult is the one conversion not in a reachable set, because it is not a role change
    the roles predict: anybody the cult can reach can be recruited. A Doppelgänger is
    cult-immune while they are one, so the composition's expansion was what put a cultist's
    achievements on their list; narrowing to a *cultable* model must not take them away,
    since copying that model makes them recruitable in turn.
    """
    shadowed = facts.model_roles(key) if facts is not None else None
    if shadowed is None:
        return composition.roles
    copied = set(reachable_roles(shadowed, composition))
    if composition.present("cultist") and any(
        not roles_registry.has_tag(role, roles_registry.CULT_IMMUNE) for role in copied
    ):
        copied.add("cultist")
    return copied


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
        "night_killers": composition.night_killers,
        "distinct_roles": composition.distinct_roles,
        "distinct_bad_roles": lambda: composition.distinct_tagged_roles(roles_registry.BAD),
        # "3 or more different visiting roles" is a count of roles, where "visited by 3
        # people" is a count of players — two achievements a single helper would conflate.
        "distinct_visiting_roles": lambda: composition.distinct_tagged_roles(roles_registry.VISITOR),
        "max_possible_wolves": composition.max_possible_wolves,
        "max_live_wolves": composition.max_live_wolves,
        "max_possible_cultists": composition.max_possible_cultists,
        "max_possible_drunks": composition.max_possible_drunks,
        "cultable_count": composition.cultable_count,
        "attackers": composition.attackers,
        "max_burnable_houses": composition.max_burnable_houses,
    }


def _player_functions(facts, key):
    """The vocabulary a player expression may use, bound to one player.

    Read as questions about "you": the subject of the rule is implied, the way it is in the
    achievement descriptions these come from ("be in love with the tanner"). Every one of
    them is a `may` rather than an `is`, because a list that hid a row the game had not yet
    ruled out would be the failure this module exists to prevent.
    """
    return {
        "is_lover": lambda: facts.is_lover(key),
        "couple_known": facts.couple_known,
        "may_love": lambda: facts.may_love(key),
        "partner_known": lambda: facts.partner_known(key),
        "is_model": lambda: facts.is_model(key),
        "models_known": facts.models_known,
        "may_be_own_model": lambda: facts.may_be_own_model(key),
        "model_known": lambda: facts.model_known(key),
        "may_model_partner": lambda: facts.may_model_partner(key),
    }


def _evaluator(composition, facts=None, key=None):
    """One sandbox, built the same way wherever an expression is evaluated.

    A player expression gets the composition's vocabulary as well as its own, so a rule
    that has to ask both questions at once ("you may be a lover, and there is a Tanner")
    can be written as one expression instead of being split across two fields that would
    then have to agree.
    """
    functions = _functions(composition)
    if facts is not None:
        functions.update(_player_functions(facts, key))
    return EvalWithCompoundTypes(
        names={"players": composition.players, "roles": composition.roles},
        functions=functions,
    )


def evaluate(expr, composition):
    """Evaluate one rule expression against a composition. Never raises.

    A rule that blows up is dropped and logged rather than propagated: these are editable
    at runtime, so one bad expression is a normal operational event, and taking the whole
    Possible Achievements post down over it would be a poor trade for the other 108 rules.
    """
    evaluator = _evaluator(composition)
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


def evaluate_for_player(expr, composition, facts, key):
    """Evaluate a rule's player expression for one player. Never raises.

    Fails **open**, unlike `evaluate`. A composition expression that blows up drops one
    achievement from the post; this one only ever narrows a row that the subject and the
    composition have already agreed on, so the safe answer to a broken gate is the answer
    the bot gave before the gate existed. Failing closed here would hide a row from
    everybody and look exactly like the achievement being impossible.
    """
    evaluator = _evaluator(composition, facts, key)
    try:
        return bool(evaluator.eval(expr))
    except Exception as exc:
        logger.warning("player_rule_eval_failed", expr=expr, error=str(exc))
        return True


def validate(expr):
    """Check an expression before storing it. Returns (ok, error_message).

    /setrule validates on write rather than discovering the problem at render time, when
    the only signal would be an achievement quietly missing from a list. The probe
    compositions are deliberately awkward — an empty game divides the ground out from under
    anything assuming players exist.
    """
    for probe in (Composition(()), Composition([("villager",)] * 3), _KITCHEN_SINK):
        try:
            _evaluator(probe).eval(expr)
        except Exception as exc:  # noqa: BLE001 - the caller wants the message, whatever it is
            return False, "{}: {}".format(type(exc).__name__, exc)
    return True, ""


def validate_player(expr):
    """The same check for a rule's player expression. Returns (ok, error_message).

    Probed against a player nothing is known about *and* one every fact has been recorded
    for, because the two halves of every `may` question take different branches and a rule
    naming a function that does not exist would otherwise pass on the branch that never
    calls it.
    """
    probe = Composition([("doppelganger",), ("villager",), ("villager",)])
    known = Facts({0: {"lover": True, "partner": 1, "model": 1}}, {})
    for facts, key in ((Facts({}, {}), 0), (known, 0), (known, 9)):
        try:
            _evaluator(probe, facts, key).eval(expr)
        except Exception as exc:  # noqa: BLE001 - the caller wants the message, whatever it is
            return False, "{}: {}".format(type(exc).__name__, exc)
    return True, ""


def passing_rules(composition, rules):
    """The rules whose expression holds for this composition: name -> rule.

    Rules opted out of listing are dropped here rather than filtered by every caller, so
    "in this dict" means "listable".
    """
    passing = {}
    for name, rule in rules.items():
        if not rulelist.is_listed(rule):
            continue
        if evaluate(rule["expr"], composition):
            passing[name] = rule
    return passing


def feasible(player_roles, rules, facts=None):
    """What each player could still earn.

    `player_roles` maps a caller's own key (a Telegram user id, in practice) to that
    player's revealed role candidates. `facts` is the same keying over what the session
    knows about each player (`session.player_facts`), and is optional: without it the
    answer is exactly what the roles alone say, which is what this returned before any
    player-level question existed. Returns `(per_player, shared)`:

    * `per_player` — key -> list of {name, swing} in the rules' own order, where `swing`
      marks a row reachable only through a role change;
    * `shared` — the achievements anybody at the table could still earn, as {name}.

    The split exists because "anyone can earn this" and "you can earn this" look identical
    once printed under a name. A rule like Sunday Bloody Sunday belongs to no role at all,
    so repeating it under each of sixteen players says the same thing sixteen times and
    crowds out the rows that are actually about that player. Said once, it reads as what it
    is: a fact about the game.
    """
    composition = Composition(player_roles.values())
    known = Facts(facts, player_roles)
    passing = passing_rules(composition, rules)

    # Whether a player passes a rule's gate, asked once per (rule, player) and remembered:
    # the shared test below asks about every player, and the per-player loop then asks
    # again about each of them.
    gates = {}

    def passes(name, rule, key):
        gate = rule.get("player_expr", "")
        if not gate:
            return True
        if (name, key) not in gates:
            gates[(name, key)] = evaluate_for_player(gate, composition, known, key)
        return gates[(name, key)]

    # Subject "any" was once the whole test for shared, and a gate is what can make it stop
    # being true: an achievement the game has narrowed to the two players in love is no
    # longer a fact about the game, whatever its subject says. So it is shared only while
    # it is still open to *everybody*, and drops into the per-player lists — under exactly
    # those it is still open to — the moment it is not.
    shared = [
        {"name": name}
        for name, rule in passing.items()
        if rule["subject"].strip() == rulelist.ANY and all(passes(name, rule, key) for key in player_roles)
    ]
    shared_names = {entry["name"] for entry in shared}

    per_player = {}
    for key, candidates in player_roles.items():
        candidates = tuple(candidates)
        reachable = reachable_roles(candidates, composition, known, key)
        own = set(candidates)
        entries = []
        for name, rule in passing.items():
            if name in shared_names:
                continue
            subject = rulelist.subject_roles(rule["subject"], roles_registry)
            if not subject.intersection(reachable):
                continue
            if not passes(name, rule, key):
                continue
            entries.append(
                {
                    "name": name,
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
