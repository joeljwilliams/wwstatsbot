"""The stand-in achievement manager: /gs, /role, /rm, /love, /gsend and the Stop button.

Runs a game's roster when the real achievement manager is offline — tracking who is
playing, what they have revealed, who is whose role model, who is in love — and keeps one
live message showing it, the way the manager does.

**Everything here is silent unless this chat has a session.** That is the single most
important property in the module, and the reason for the `_session_for` gate at the top of
every handler. `/role`, `/rm`, `/love` and `/gsend` are the *real manager's* commands, and
Telegram delivers every message beginning with a slash to every bot in the group. Without
the gate, @wwstatsbot would answer over the top of the incumbent in every game it is a
member of — dozens of duplicate replies a round, in chats where nothing is wrong.

`/gs` is gated differently, because it is the command that *creates* a session and so has
no session to check. It requires being addressed explicitly — `/gs@wwstatsbot` — since a
bare `/gs` is how the real manager is started, and PTB's CommandHandler matches a bare
command for every bot. Being addressed by name is the whole "the manager is offline"
signal: nobody types it by accident.

Writes are further gated on **roster membership**. Only players the game bot listed can
reveal, be targeted or stop the session; anyone else is not in the game, and a stand-in
that let a passer-by edit a live roster on the strength of a stray message would be worse
than one that ignored them.
"""

import html
import re
import time

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from unidecode import unidecode

import roles
import session
import templates as t
from handlers.common import mentioned_users

logger = structlog.get_logger(__name__)

STOP_CALLBACK = "standin:stop"

# The Stop button takes two presses. The real manager's stops on the first, and it sits
# under sixteen players' thumbs for the length of a game — a mis-tap there kills a live
# session that then has to be rebuilt by hand. Arming expires so that a press now and a
# stray press ten minutes later cannot add up to a stop.
_STOP_ARM_SECONDS = 15

# Only these two roles have a role model, so only they are valid /rm targets.
_ROLE_MODEL_ROLES = ("wild_child", "doppelganger")


def _now():
    """Wall clock, wrapped so tests can control the stop-button arming window."""
    return time.time()


# --- Gates -----------------------------------------------------------------


def _addressed_to_us(message, username):
    """True only when the command names this bot explicitly: `/gs@wwstatsbot`.

    PTB's CommandHandler already rejects `/gs@someotherbot`, but it accepts a bare `/gs` —
    which is exactly how players start the *real* manager. Answering that would mean two
    bots racing to run the same game.
    """
    entities = message.entities or ()
    if not entities or not message.text:
        return False
    first = entities[0]
    if first.type != MessageEntity.BOT_COMMAND or first.offset != 0:
        return False
    command = message.text[: first.length]
    _, _, addressed = command.partition("@")
    return bool(addressed) and addressed.casefold() == (username or "").casefold()


def _session_for(update, context):
    """This chat's session if the sender may write to it, else None.

    Returns None — meaning "say nothing at all" — in both the no-session and the
    not-a-player case. Neither deserves a reply: the first means the real manager is
    running the game, and the second means the sender is playing in it under the real
    manager while we happen to be in the room.
    """
    current = session.get(context.chat_data)
    if current is None:
        return None
    if not session.is_member(current, update.message.from_user.id):
        return None
    return current


# --- Resolving players -----------------------------------------------------


def _fold(name):
    """Fold a display name for matching: accents, case and stray spacing removed.

    Players are named by typing their name — `/rm omu`, the way the manager's own posts
    render a role model — and those names carry emoji, script variants and decoration
    ("𝑬𝒔𝒓𝒂", "shu . . ⋰ ⋱"). unidecode is what makes the typeable ASCII spelling match.
    """
    return unidecode(name or "").casefold().strip().lstrip("@")


def _find_player(session_data, token):
    """The roster player a typed name refers to: user_id, or None.

    Exact match first, then a unique prefix, so `/rm omu` works and `/rm o` does not
    silently pick one of two players starting with it. Ambiguity resolves to None and is
    reported — guessing would attach a role model to the wrong player, which never fires a
    transform and surfaces much later as an achievement that failed to appear.
    """
    key = _fold(token)
    if not key:
        return None
    exact = [uid for uid, entry in session.players_in_order(session_data) if _fold(entry["name"]) == key]
    if len(exact) == 1:
        return exact[0]
    prefix = [uid for uid, entry in session.players_in_order(session_data) if _fold(entry["name"]).startswith(key)]
    if len(prefix) == 1:
        return prefix[0]
    return None


def _replied_player(update, session_data):
    """The roster player whose message was replied to, or None."""
    replied = update.message.reply_to_message
    if replied is None or replied.from_user is None:
        return None
    uid = replied.from_user.id
    return uid if session.player(session_data, uid) is not None else None


# --- Rendering -------------------------------------------------------------


def _role_label(entry):
    """A player's revealed role(s), rendered. Two of them for an unresolved Seer/Fool."""
    return " / ".join(roles.display(role_id) for role_id in entry["roles"])


def _player_row(session_data, entry):
    """One line of the roster: name, role, role model, lover heart."""
    name = html.escape(entry["name"])
    if not entry["roles"]:
        return t.STANDIN_PLAYER_UNREVEALED.format(name=name)

    label = _role_label(entry)
    if entry["model"] is not None:
        # Resolved to a name here rather than stored as one, so a player renaming
        # mid-game cannot leave a stale label behind in the message.
        model_name = session.name_of(session_data, entry["model"])
        if model_name:
            label += t.STANDIN_MODEL.format(name=html.escape(model_name))
    if entry["lover"]:
        label += t.STANDIN_LOVER
    return t.STANDIN_PLAYER_ROW.format(name=name, role=label)


def render_state(session_data):
    """The live roster message: (html, keyboard).

    Mirrors the achievement manager's own layout — header, `Players (n / total)`, then a
    `Dead Players` section — because a replacement that reorganised the message would be
    the first thing anyone noticed at the moment they are looking for something familiar.
    """
    revealed, total = session.revealed_count(session_data)
    msg = t.STANDIN_HEADER + t.STANDIN_INTRO
    msg += t.STANDIN_PLAYERS_HEADER.format(revealed=revealed, total=total)

    dead = []
    for _, entry in session.players_in_order(session_data):
        if entry["alive"]:
            msg += _player_row(session_data, entry)
        else:
            dead.append(entry)

    msg += t.STANDIN_DEAD_HEADER
    for entry in dead:
        msg += _player_row(session_data, entry)

    if session_data["unresolved"]:
        msg += t.STANDIN_UNRESOLVED.format(names=", ".join(html.escape(n) for n in session_data["unresolved"]))

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(t.STANDIN_STOP_BUTTON, callback_data=STOP_CALLBACK)]])
    return msg, keyboard


async def _refresh_state(context, chat_id, session_data):
    """Re-render the live roster message in place.

    Best-effort by design: the message may have been deleted, or an edit may land on
    identical text when a reveal changed nothing visible. Neither is worth failing a
    command the player did successfully issue, so both are swallowed.
    """
    message_id = session_data.get("state_message_id")
    if message_id is None:
        return
    msg, keyboard = render_state(session_data)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=msg,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest:
        pass


# --- /gs -------------------------------------------------------------------


async def start_session_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/gs@wwstatsbot`, in reply to the game bot's player list, starts standing in."""
    message = update.message
    if not _addressed_to_us(message, context.bot.username):
        # A bare /gs belongs to the real manager. Not an error, not ours: say nothing.
        return

    user = message.from_user
    logger.info("command", command="gs", user_id=user.id, user=unidecode(user.first_name))

    if session.get(context.chat_data) is not None:
        await message.reply_text(t.STANDIN_ALREADY_RUNNING, parse_mode=ParseMode.HTML)
        return

    replied = message.reply_to_message
    if replied is None:
        await message.reply_text(
            t.STANDIN_NEEDS_ROSTER.format(username=html.escape(context.bot.username or "")),
            parse_mode=ParseMode.HTML,
        )
        return

    players, unresolved = mentioned_users(replied)
    if not players:
        await message.reply_text(t.STANDIN_NO_PLAYERS, parse_mode=ParseMode.HTML)
        return

    session_data = session.start(context.chat_data, user.id, players, unresolved, _now())
    msg, keyboard = render_state(session_data)
    posted = await context.bot.send_message(
        chat_id=message.chat.id,
        text=msg,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    # The id is what every later edit needs; without it the roster would be re-posted on
    # each reveal instead of updated.
    if posted is not None:
        session_data["state_message_id"] = posted.message_id
    logger.info("standin_started", chat_id=message.chat.id, players=len(players), unresolved=len(unresolved))


# --- /role -----------------------------------------------------------------


async def role_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/role <role>` — reveal, or correct, a player's role.

    Targets the player replied to when sent as a reply, otherwise the sender. Revealing
    again overwrites: roles change all game, so a second /role is how someone says "I am
    something else now", not a mistake to reject.
    """
    session_data = _session_for(update, context)
    if session_data is None:
        return

    message = update.message
    user = message.from_user
    logger.info("command", command="role", user_id=user.id, user=unidecode(user.first_name), args=context.args)

    if not context.args:
        await message.reply_text(t.STANDIN_ROLE_USAGE, parse_mode=ParseMode.HTML)
        return

    typed = " ".join(context.args)
    resolved = roles.resolve(typed)
    if not resolved:
        msg = t.STANDIN_ROLE_UNKNOWN.format(role=html.escape(typed))
        suggestions = roles.suggest(typed)
        if suggestions:
            msg += t.STANDIN_ROLE_DID_YOU_MEAN.format(names=", ".join(suggestions))
        await message.reply_text(msg, parse_mode=ParseMode.HTML)
        return

    target_id = _replied_player(update, session_data)
    if target_id is None and message.reply_to_message is not None:
        # Replied to someone who is not in this game — better to say so than to silently
        # record the role against the sender instead.
        await message.reply_text(t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML)
        return
    if target_id is None:
        target_id = user.id

    entry = session.set_roles(session_data, target_id, resolved)
    session.touch(session_data, _now())
    await _refresh_state(context, message.chat.id, session_data)

    template = t.STANDIN_ROLE_SET_AMBIGUOUS if len(resolved) > 1 else t.STANDIN_ROLE_SET
    await message.reply_text(
        template.format(name=html.escape(entry["name"]), role=_role_label(entry)),
        parse_mode=ParseMode.HTML,
    )


# --- /rm -------------------------------------------------------------------


async def rolemodel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/rm <model>`, `/rm <model>` in reply, or `/rm <player> <model>`.

    All three forms check that the target actually has a role model. Only the Wild Child
    and the Doppelgänger do, and a role model stored against anyone else is the kind of
    mistake that hides: it never fires a transform, so the error surfaces much later as an
    achievement that failed to appear rather than as a wrong answer anyone can see.
    """
    session_data = _session_for(update, context)
    if session_data is None:
        return

    message = update.message
    user = message.from_user
    logger.info("command", command="rm", user_id=user.id, user=unidecode(user.first_name), args=context.args)

    args = context.args
    if not args:
        await message.reply_text(t.STANDIN_MODEL_USAGE, parse_mode=ParseMode.HTML)
        return

    if len(args) >= 2:
        target_token, model_token = args[0], " ".join(args[1:])
        target_id = _find_player(session_data, target_token)
    else:
        model_token = args[0]
        target_id = _replied_player(update, session_data)
        if target_id is None and message.reply_to_message is not None:
            await message.reply_text(t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML)
            return
        if target_id is None:
            target_id = user.id

    if target_id is None:
        await message.reply_text(t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML)
        return

    model_id = _find_player(session_data, model_token)
    if model_id is None:
        await message.reply_text(t.STANDIN_NOT_IN_GAME.format(name=html.escape(model_token)), parse_mode=ParseMode.HTML)
        return

    target = session.player(session_data, target_id)
    if not target["roles"]:
        await message.reply_text(
            t.STANDIN_MODEL_NEEDS_ROLE.format(name=html.escape(target["name"])), parse_mode=ParseMode.HTML
        )
        return
    if not any(role_id in _ROLE_MODEL_ROLES for role_id in target["roles"]):
        await message.reply_text(
            t.STANDIN_MODEL_WRONG_ROLE.format(name=html.escape(target["name"]), role=_role_label(target)),
            parse_mode=ParseMode.HTML,
        )
        return

    session.set_model(session_data, target_id, model_id)
    session.touch(session_data, _now())
    await _refresh_state(context, message.chat.id, session_data)
    await message.reply_text(
        t.STANDIN_MODEL_SET.format(
            name=html.escape(target["name"]),
            model=html.escape(session.name_of(session_data, model_id)),
        ),
        parse_mode=ParseMode.HTML,
    )


# --- /love -----------------------------------------------------------------


async def love_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/love` (the sender), `/love <player>`, `/love <a> <b>`, or in reply.

    Bare `/love` is the manager's own shape, and the reason lover is a per-player flag: its
    roster marks each partner with a heart rather than listing a couple, so a lover without
    a named partner is a complete, renderable state rather than a half-finished command.
    """
    session_data = _session_for(update, context)
    if session_data is None:
        return

    message = update.message
    user = message.from_user
    logger.info("command", command="love", user_id=user.id, user=unidecode(user.first_name), args=context.args)

    args = context.args
    replied_id = _replied_player(update, session_data)

    if len(args) >= 2:
        first_id, partner_id = _find_player(session_data, args[0]), _find_player(session_data, args[1])
    elif len(args) == 1:
        # With a reply, the named player is the *partner* of whoever was replied to;
        # without one, they are simply in love.
        named = _find_player(session_data, args[0])
        first_id, partner_id = (replied_id, named) if replied_id is not None else (named, None)
    else:
        first_id, partner_id = (replied_id if replied_id is not None else user.id), None

    if first_id is None or (len(args) >= 2 and partner_id is None):
        await message.reply_text(t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML)
        return
    if len(args) == 1 and replied_id is not None and partner_id is None:
        await message.reply_text(t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML)
        return

    entry = session.set_lover(session_data, first_id, partner_id)
    if entry is None:
        await message.reply_text(t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML)
        return

    session.touch(session_data, _now())
    await _refresh_state(context, message.chat.id, session_data)

    if partner_id is None:
        await message.reply_text(t.STANDIN_LOVE_SET.format(name=html.escape(entry["name"])), parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(
            t.STANDIN_LOVE_PAIR_SET.format(
                name=html.escape(entry["name"]),
                partner=html.escape(session.name_of(session_data, partner_id)),
            ),
            parse_mode=ParseMode.HTML,
        )


# --- Ending ----------------------------------------------------------------


async def _finish(context, chat_id, session_data):
    """Drop the keyboard from the roster message so a dead session has no live button."""
    message_id = session_data.get("state_message_id")
    if message_id is None:
        return
    try:
        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except BadRequest:
        pass


async def end_session_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/gsend` — end the session. Roster-gated and silent otherwise, like the rest."""
    session_data = _session_for(update, context)
    if session_data is None:
        return

    message = update.message
    logger.info(
        "command",
        command="gsend",
        user_id=message.from_user.id,
        user=unidecode(message.from_user.first_name),
    )
    session.end(context.chat_data)
    await _finish(context, message.chat.id, session_data)
    await message.reply_text(t.STANDIN_ENDED, parse_mode=ParseMode.HTML)


async def stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The Stop button, which takes two presses from the same player.

    The first press only arms; the second within the window ends the session. The real
    manager stops on one press, and that button sits under a dozen thumbs for a whole game.
    Arming expires so a press now and a stray press later cannot combine.
    """
    query = update.callback_query
    user = query.from_user
    session_data = session.get(context.chat_data)

    if session_data is None:
        await query.answer(t.STANDIN_STOP_EXPIRED, show_alert=True)
        return
    if not session.is_member(session_data, user.id):
        await query.answer(t.STANDIN_STOP_NOT_YOURS, show_alert=True)
        return

    armed_by = session_data.get("stop_armed_by")
    armed_at = session_data.get("stop_armed_at") or 0
    fresh = (_now() - armed_at) <= _STOP_ARM_SECONDS

    logger.info("callback", command="standin_stop", user_id=user.id, armed=bool(armed_by and fresh))

    if armed_by == user.id and fresh:
        session.end(context.chat_data)
        await _finish(context, query.message.chat.id, session_data)
        await query.answer(t.STANDIN_ENDED)
        return

    session_data["stop_armed_by"] = user.id
    session_data["stop_armed_at"] = _now()
    await query.answer(t.STANDIN_STOP_ARM, show_alert=True)


# --- Deaths ----------------------------------------------------------------

# The game bot's roster states its own counts — "Players Alive: 11/16" — which is what
# makes /ad safe to apply as a full reset: the claim can be checked against what we parsed
# before anything is written.
_ROSTER_COUNTS = re.compile(r"Players\s+Alive:\s*(?P<alive>\d+)\s*/\s*(?P<total>\d+)", re.IGNORECASE)

# A dead row carries the role the player was: "omu: 💀 Dead - the Serial Killer 🔪". Worth
# reading, because a player who never got round to /role still contributes their role to
# everyone else's achievements from the moment they die.
_DEAD_ROW = re.compile(r"^(?P<name>.+?):\s*\S*\s*Dead\b\s*[-–—]\s*(?P<role>.+?)\s*$", re.IGNORECASE)


def _transform_lines(session_data, changes):
    """Render the role changes a set of deaths triggered."""
    if not changes:
        return ""
    reasons = {
        session.BY_MODEL_DEATH: t.STANDIN_REASON_MODEL_DIED,
        session.BY_SEER_DEATH: t.STANDIN_REASON_SEER_DIED,
        session.BY_LAST_WOLF_DEATH: t.STANDIN_REASON_WOLVES_DEAD,
    }
    out = t.STANDIN_TRANSFORM_HEADER
    for change in changes:
        name = html.escape(session.name_of(session_data, change["user_id"]) or "")
        if change["reason"] == session.BY_SORROW:
            out += t.STANDIN_TRANSFORM_SORROW.format(name=name)
            continue
        out += t.STANDIN_TRANSFORM_ROW.format(
            name=name,
            role=" / ".join(roles.display(role_id) for role_id in change["roles"]),
            reason=reasons[change["reason"]],
        )
    return out


async def dead_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/dead <player>` or a reply — mark one player dead and run what that triggers."""
    session_data = _session_for(update, context)
    if session_data is None:
        return

    message = update.message
    logger.info(
        "command",
        command="dead",
        user_id=message.from_user.id,
        user=unidecode(message.from_user.first_name),
        args=context.args,
    )

    target_id = _replied_player(update, session_data)
    if target_id is None and context.args:
        target_id = _find_player(session_data, " ".join(context.args))
    if target_id is None:
        await message.reply_text(
            t.STANDIN_DEAD_USAGE if not context.args else t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML
        )
        return

    entry = session.player(session_data, target_id)
    if not entry["alive"]:
        await message.reply_text(
            t.STANDIN_ALREADY_DEAD.format(name=html.escape(entry["name"])), parse_mode=ParseMode.HTML
        )
        return

    session.set_alive(session_data, target_id, False)
    changes = session.apply_transforms(session_data)
    session.touch(session_data, _now())
    await _refresh_state(context, message.chat.id, session_data)

    reply = t.STANDIN_DEAD_MARKED.format(name=html.escape(entry["name"]))
    await message.reply_text(reply + _transform_lines(session_data, changes), parse_mode=ParseMode.HTML)


def _dead_rows(text):
    """(name, role_text) for every "… : 💀 Dead - the X" line in a roster."""
    rows = []
    for line in (text or "").splitlines():
        found = _DEAD_ROW.match(line.strip())
        if found is not None:
            rows.append((found.group("name").strip(), found.group("role").strip()))
    return rows


async def follow_roster_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/ad` in reply to the game bot's roster — follow it wholesale.

    A **full reset**, not a diff: the game bot's list is the authority, so anyone it shows
    as alive *is* alive, including a player a mistyped /dead killed off. Being able to undo
    that matters more than guarding against a misparse — and the roster's own header is a
    better guard anyway, so a list whose counts disagree with what we read changes nothing
    at all. Half-applying a roster would be worse than refusing one.
    """
    session_data = _session_for(update, context)
    if session_data is None:
        return

    message = update.message
    logger.info("command", command="ad", user_id=message.from_user.id, user=unidecode(message.from_user.first_name))

    replied = message.reply_to_message
    if replied is None:
        await message.reply_text(t.STANDIN_AD_USAGE, parse_mode=ParseMode.HTML)
        return

    # Alive players are the mentions: the game bot links every living player's name and
    # leaves the dead as plain text, so this needs no text parsing at all.
    alive, _ = mentioned_users(replied)
    alive_ids = [uid for uid, _ in alive if session.player(session_data, uid) is not None]

    body = replied.text or replied.caption or ""
    counts = _ROSTER_COUNTS.search(body)
    if counts is None or int(counts.group("alive")) != len(alive_ids):
        claimed = counts.group("alive") if counts else "?"
        total = counts.group("total") if counts else "?"
        await message.reply_text(
            t.STANDIN_AD_MISMATCH.format(
                claimed=claimed,
                total=total,
                found=len(alive_ids),
                plural="" if len(alive_ids) == 1 else "s",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    died, revived = session.sync_alive(session_data, alive_ids)

    # Dead rows name the role the player was. They carry no user id — the game bot stops
    # linking a player once they are out — so they are matched by display name, and an
    # ambiguous or unknown one skips *the role* only. Aliveness came from the mentions
    # above and is never at risk from this.
    learned = []
    for name, role_text in _dead_rows(body):
        uid = _find_player(session_data, name)
        if uid is None:
            continue
        entry = session.player(session_data, uid)
        if entry["roles"]:
            continue
        resolved = roles.resolve(role_text)
        if resolved:
            session.set_roles(session_data, uid, resolved)
            learned.append(entry["name"])

    changes = session.apply_transforms(session_data)
    session.touch(session_data, _now())
    await _refresh_state(context, message.chat.id, session_data)

    if not died and not revived and not learned and not changes:
        await message.reply_text(t.STANDIN_AD_NO_CHANGE, parse_mode=ParseMode.HTML)
        return

    def names(ids):
        return ", ".join(html.escape(session.name_of(session_data, uid) or "") for uid in ids)

    reply = t.STANDIN_AD_SUMMARY
    if died:
        reply += t.STANDIN_AD_DIED.format(names=names(died))
    if revived:
        reply += t.STANDIN_AD_REVIVED.format(names=names(revived))
    if learned:
        reply += t.STANDIN_AD_ROLES_LEARNED.format(names=", ".join(html.escape(n) for n in learned))
    reply += _transform_lines(session_data, changes)
    await message.reply_text(reply, parse_mode=ParseMode.HTML)


# --- /steal ----------------------------------------------------------------


async def steal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/steal <player>` — the Thief's theft, as one command instead of two /roles.

    Swaps both roles, so the robbed player becomes the new Thief, and carries the role
    model across with the role it belongs to: a stolen Wild Child means nothing without
    whoever the Wild Child was watching.
    """
    session_data = _session_for(update, context)
    if session_data is None:
        return

    message = update.message
    user = message.from_user
    logger.info("command", command="steal", user_id=user.id, user=unidecode(user.first_name), args=context.args)

    thief = session.player(session_data, user.id)
    if "thief" not in thief["roles"]:
        await message.reply_text(t.STANDIN_STEAL_NOT_THIEF, parse_mode=ParseMode.HTML)
        return

    target_id = _replied_player(update, session_data)
    if target_id is None and context.args:
        target_id = _find_player(session_data, " ".join(context.args))
    if target_id is None:
        await message.reply_text(
            t.STANDIN_STEAL_USAGE if not context.args else t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML
        )
        return

    target = session.player(session_data, target_id)
    if not target["roles"]:
        await message.reply_text(
            t.STANDIN_MODEL_NEEDS_ROLE.format(name=html.escape(target["name"])), parse_mode=ParseMode.HTML
        )
        return
    if any(roles.has_tag(role_id, roles.STEAL_IMMUNE) for role_id in target["roles"]):
        await message.reply_text(
            t.STANDIN_STEAL_IMMUNE.format(name=html.escape(target["name"]), role=_role_label(target)),
            parse_mode=ParseMode.HTML,
        )
        return

    stolen = _role_label(target)
    session.swap_roles(session_data, user.id, target_id)
    session.touch(session_data, _now())
    await _refresh_state(context, message.chat.id, session_data)
    await message.reply_text(
        t.STANDIN_STEAL_DONE.format(thief=html.escape(thief["name"]), role=stolen, name=html.escape(target["name"])),
        parse_mode=ParseMode.HTML,
    )
