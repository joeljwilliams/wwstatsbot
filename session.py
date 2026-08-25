"""Stand-in game session state: the roster, what everyone revealed, and who is alive.

Pure state. Nothing here touches Telegram, renders a message or decides what a player can
earn — that keeps the rules of the session testable without a bot, and means the handler
module is only about turning updates into these calls.

**The session lives in `chat_data`**, which buys two properties for free: it is per-chat,
so one group's game can never leak into another's, and `RedisPersistence` keeps it across
restarts when `REDIS_URL` is set. The price is that every value must survive a JSON
round-trip — dict keys come back as strings, tuples come back as lists — so player ids are
stored as **string keys** throughout and normalised on the way in. A session that silently
stopped matching its own players after a redeploy would be worse than one that was lost.

**The roster is closed.** It is taken from the game bot's player list when `/gs` runs, and
only those players exist for the rest of the session: they are the ones who may reveal, be
targeted, or die. Anyone else is not in the game, and a stand-in that let a passer-by write
into a live roster would be worse than one that ignored them.
"""

import roles

KEY = "standin"


# What a player's entry looks like before they have revealed anything. `roles` is a list
# rather than a single id because /role sf records a Seer/Fool pair the player themselves
# cannot yet distinguish (see roles.SEER_FOOL).
def _blank_player(name):
    return {
        "name": name,
        "roles": [],
        # Wild Child / Doppelgänger only. Stored as the model's id, resolved to a name at
        # render time so a rename cannot leave a stale label in the message.
        "model": None,
        # Lover status is a flag with an optional partner, not a pair: the real manager
        # accepts a bare /love and marks each partner with a heart rather than listing a
        # couple, and some achievements only need "this player is a lover".
        "lover": False,
        "partner": None,
        "alive": True,
        # Achievements this player already holds, from the stats API. None means "not
        # fetched" and is treated as "we don't know", which shows everything: over-offering
        # is recoverable by the player, and hiding an achievement they could still earn is
        # the failure this whole list exists to prevent.
        "attained": None,
    }


def start(chat_data, user_id, players, unresolved, now):
    """Open a session for this chat and return it. Overwrites any existing one.

    `players` is the (id, name) list read out of the game bot's roster. Names are stored
    **unescaped**; escaping happens once at render time, so a persistence round-trip cannot
    double-escape a player called "Al & Sons".
    """
    session = {
        "started_by": user_id,
        "started_at": now,
        "order": [uid for uid, _ in players],
        "players": {str(uid): _blank_player(name) for uid, name in players},
        # @username mentions carry no id, so those players cannot be tracked at all. Kept
        # so the reply can say so rather than pretending the roster is complete.
        "unresolved": list(unresolved),
        "state_message_id": None,
        "list_message_id": None,
        "stop_armed_by": None,
        "stop_armed_at": None,
        "last_activity": now,
    }
    chat_data[KEY] = session
    return session


def get(chat_data):
    """This chat's session, or None. The gate every command starts with."""
    return chat_data.get(KEY)


def end(chat_data):
    """Close the session. Returns the session that was ended, or None."""
    return chat_data.pop(KEY, None)


def touch(session, now):
    """Record activity, which is what the idle timer measures."""
    session["last_activity"] = now


def is_member(session, user_id):
    """Whether this user is in the roster.

    The only authorisation this feature has. Everything that writes to a session is gated
    on it, because a stand-in that accepted commands from outside the game would be
    corrupting a live roster on the strength of a stray message.
    """
    return str(user_id) in session["players"]


def player(session, user_id):
    """One player's entry, or None if they are not in the roster."""
    return session["players"].get(str(user_id))


def players_in_order(session):
    """(user_id, entry) pairs in the game bot's own roster order.

    Roster order, not reveal order: the state message is read against the game bot's list,
    and a message whose rows moved between edits is hard to read at a glance.
    """
    for uid in session["order"]:
        entry = session["players"].get(str(uid))
        if entry is not None:
            yield int(uid), entry


def name_of(session, user_id):
    """A player's display name, or None. Unescaped — callers escape at render time."""
    entry = player(session, user_id)
    return None if entry is None else entry["name"]


# --- Writes ----------------------------------------------------------------


def set_roles(session, user_id, role_ids):
    """Record what a player revealed. Returns the entry, or None if not in the roster.

    Overwrites rather than merges, deliberately: roles change all game — the Thief steals,
    the Cursed turns, the Wild Child's model dies — so a second /role is the normal way to
    say "I am something else now", not a mistake to be rejected.
    """
    entry = player(session, user_id)
    if entry is None:
        return None
    entry["roles"] = list(role_ids)
    return entry


def set_model(session, user_id, model_id):
    """Record a role model. Returns the entry, or None if either player is unknown."""
    entry = player(session, user_id)
    if entry is None or player(session, model_id) is None:
        return None
    entry["model"] = model_id
    return entry


def set_lover(session, user_id, partner_id=None):
    """Mark a player as in love, and their partner too when one is named.

    Symmetric on purpose. Love is mutual in this game, and a one-sided record would show a
    heart against one of the couple and not the other — which reads as a bug in the list
    rather than as a half-finished command.
    """
    entry = player(session, user_id)
    if entry is None:
        return None
    entry["lover"] = True
    if partner_id is not None:
        partner = player(session, partner_id)
        if partner is None:
            return None
        entry["partner"] = partner_id
        partner["lover"] = True
        partner["partner"] = user_id
    return entry


def swap_roles(session, a_id, b_id):
    """Exchange two players' roles and role models — the Thief's theft, in one step.

    The model travels with the role because it belongs to it: a stolen Wild Child is only
    meaningful alongside whoever the Wild Child was watching.
    """
    a, b = player(session, a_id), player(session, b_id)
    if a is None or b is None:
        return None
    a["roles"], b["roles"] = b["roles"], a["roles"]
    a["model"], b["model"] = b["model"], a["model"]
    return a, b


def set_alive(session, user_id, alive):
    """Mark one player alive or dead. Returns the entry, or None if unknown."""
    entry = player(session, user_id)
    if entry is None:
        return None
    entry["alive"] = alive
    return entry


def sync_alive(session, alive_ids):
    """Reconcile the whole roster against the game bot's list. Returns (died, revived).

    A **full reset, not a diff**: the game bot's roster is the authority, so anyone it
    lists is alive — including a player a mistyped /dead killed off — and anyone it does
    not is dead. Being able to undo a bad /dead matters more than guarding against a bad
    parse, and the caller checks the roster's own count before calling this, which is the
    better guard.
    """
    alive_ids = {str(uid) for uid in alive_ids}
    died, revived = [], []
    for uid, entry in list(players_in_order(session)):
        should_be_alive = str(uid) in alive_ids
        if entry["alive"] and not should_be_alive:
            died.append(uid)
        elif not entry["alive"] and should_be_alive:
            revived.append(uid)
        entry["alive"] = should_be_alive
    return died, revived


def set_attained(session, user_id, names):
    """Record what a player has already earned. Returns the entry, or None if unknown."""
    entry = player(session, user_id)
    if entry is None:
        return None
    entry["attained"] = sorted(names)
    return entry


def already_has(session, user_id, achievement):
    """Whether a player already holds an achievement.

    False when their list was never fetched: an unknown answer must not hide a row. The
    stats API is the sort of thing that is briefly unavailable, and a game played during
    one of those minutes should still get a list.
    """
    entry = player(session, user_id)
    if entry is None or entry["attained"] is None:
        return False
    return achievement in entry["attained"]


# --- Reads for the feasibility layer ---------------------------------------


def revealed_roles(session, alive_only=True):
    """user_id -> tuple of role ids, for players who have revealed.

    Dead players are left out by default, and that is a judgement call worth stating: the
    list answers "what is still possible", and an achievement needing a harlot to visit is
    not still possible once the harlot is dead. Unrevealed players are absent too — we know
    nothing about them, and guessing would be worse than a shorter list.
    """
    revealed = {}
    for uid, entry in players_in_order(session):
        if alive_only and not entry["alive"]:
            continue
        if entry["roles"]:
            revealed[uid] = tuple(entry["roles"])
    return revealed


def revealed_count(session):
    """(revealed, total) — the counter the state message carries."""
    total = len(session["order"])
    revealed = sum(1 for _, entry in players_in_order(session) if entry["roles"])
    return revealed, total


# --- Transforms ------------------------------------------------------------
#
# Roles change when people die, and the manager is expected to keep up: a Wild Child whose
# role model was eaten is a wolf from that moment, and listing them as a Wild Child for the
# rest of the game would give them the wrong achievements twice over — theirs, and not the
# pack's.
#
# Each rule below is stated in the game's own terms and applied to a fixed point, because
# transforms cascade: a lover dying of sorrow can be the death that promotes an Apprentice
# Seer, whose promotion changes nothing else but might have. The iteration cap is a
# backstop against a cycle nobody anticipated (two Doppelgängers shadowing each other),
# because a stand-in that hangs is worse than one that stops transforming.

_TRANSFORM_PASSES = 10

# Why a role changed, for the reply. Kept as plain identifiers rather than prose so the
# handler owns the wording and this module stays free of user-visible strings.
BY_MODEL_DEATH = "model_died"
BY_SEER_DEATH = "seer_died"
BY_LAST_WOLF_DEATH = "wolves_dead"
BY_SORROW = "sorrow"


def _has(entry, *role_ids):
    return any(role_id in entry["roles"] for role_id in role_ids)


def _any_alive_with_tag(session, tag):
    return any(
        entry["alive"] and any(roles.has_tag(role_id, tag) for role_id in entry["roles"])
        for _, entry in players_in_order(session)
    )


def _any_dead_with_tag(session, tag):
    return any(
        not entry["alive"] and any(roles.has_tag(role_id, tag) for role_id in entry["roles"])
        for _, entry in players_in_order(session)
    )


def _dead(session, user_id):
    entry = player(session, user_id)
    return entry is not None and not entry["alive"]


def apply_transforms(session):
    """Run every death-triggered role change until nothing more fires.

    Returns a list of {user_id, roles, reason} describing what changed, so the caller can
    report it. An empty list means the deaths that just landed changed nobody's role.
    """
    changes = []
    for _ in range(_TRANSFORM_PASSES):
        changed = False

        for uid, entry in list(players_in_order(session)):
            # A lover dies of sorrow. First, because the death it causes can trigger any
            # of the others below.
            if entry["alive"] and entry["partner"] is not None and _dead(session, entry["partner"]):
                entry["alive"] = False
                changes.append({"user_id": uid, "roles": list(entry["roles"]), "reason": BY_SORROW})
                changed = True
                continue

            if not entry["alive"] or entry["model"] is None or not _dead(session, entry["model"]):
                continue

            # The Wild Child turns when their role model dies — a plain Werewolf, never an
            # Alpha; the pack gains a member, not a leader.
            if _has(entry, "wild_child"):
                entry["roles"] = ["werewolf"]
                changes.append({"user_id": uid, "roles": ["werewolf"], "reason": BY_MODEL_DEATH})
                changed = True
            # The Doppelgänger becomes whatever their model was. Their *current* roles, so
            # a model who had already transformed is copied as they ended up.
            elif _has(entry, "doppelganger"):
                model = player(session, entry["model"])
                if model is not None and model["roles"]:
                    entry["roles"] = list(model["roles"])
                    entry["model"] = None
                    changes.append({"user_id": uid, "roles": list(entry["roles"]), "reason": BY_MODEL_DEATH})
                    changed = True

        # The Apprentice Seer is promoted once no Seer is left alive. Written as "no living
        # seer, but there was one" rather than "the seer died" so it cannot fire in a game
        # that never had a Seer to lose.
        if not _any_alive_with_role(session, "seer") and _any_dead_with_role(session, "seer"):
            for uid, entry in players_in_order(session):
                if entry["alive"] and _has(entry, "apprentice_seer"):
                    entry["roles"] = ["seer"]
                    changes.append({"user_id": uid, "roles": ["seer"], "reason": BY_SEER_DEATH})
                    changed = True
                    break

        # The Traitor becomes a wolf once the pack is gone. Same shape: a game with no
        # wolves at all must not promote them on the first death.
        if not _any_alive_with_tag(session, roles.PACK) and _any_dead_with_tag(session, roles.PACK):
            for uid, entry in players_in_order(session):
                if entry["alive"] and _has(entry, "traitor"):
                    entry["roles"] = ["werewolf"]
                    changes.append({"user_id": uid, "roles": ["werewolf"], "reason": BY_LAST_WOLF_DEATH})
                    changed = True
                    break

        if not changed:
            break

    return changes


def _any_alive_with_role(session, role_id):
    return any(entry["alive"] and role_id in entry["roles"] for _, entry in players_in_order(session))


def _any_dead_with_role(session, role_id):
    return any(not entry["alive"] and role_id in entry["roles"] for _, entry in players_in_order(session))
