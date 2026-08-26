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

import asyncio
import html
import re
import time

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ReplyParameters, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from unidecode import unidecode

import api
import db
import feasibility
import roles
import rulelist
import session
import templates as t
from handlers.common import is_admin_user, mentioned_users

logger = structlog.get_logger(__name__)

STOP_CALLBACK = "standin:stop"

# The Stop button takes two presses. The real manager's stops on the first, and it sits
# under sixteen players' thumbs for the length of a game — a mis-tap there kills a live
# session that then has to be rebuilt by hand. Arming expires so that a press now and a
# stray press ten minutes later cannot add up to a stop.
_STOP_ARM_SECONDS = 15

# Only these two roles have a role model, so only they are valid /rm targets.
_ROLE_MODEL_ROLES = ("wild_child", "doppelganger")

# "I am the Beholder and there is no Seer." The Beholder is shown the real Seer at the
# start of the game, so this is the one claim that can settle the Seer/Fool question for
# everybody else — which is why it is a claim of its own rather than just /role beholder.
_NO_SEER_CLAIMS = frozenset(
    roles.normalise(spelling) for spelling in ("bhns", "beholder no seer", "beholdernoseer", "bh no seer", "no seer")
)
# "I am the Beholder and X is the Seer." The rest of the line names X.
_WITH_SEER_CLAIMS = frozenset(roles.normalise(spelling) for spelling in ("bhws", "bh", "beholder"))


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


def _mention(user_id, name):
    """A player's name as a tappable mention.

    Every other message this bot sends links the people it names (see builders.py), and a
    roster of sixteen plain-text names is the one place that would stand out — you cannot
    tap through to anybody, and two players with similar display names are impossible to
    tell apart. Escaping happens here, once, on the way in.
    """
    return t.STANDIN_MENTION.format(user_id=user_id, name=html.escape(name or ""))


def _mention_player(session_data, user_id):
    """The tappable form of a roster player's name; their id alone if they are unknown."""
    name = session.name_of(session_data, user_id)
    return _mention(user_id, name) if name is not None else str(user_id)


def _role_label(entry):
    """A player's revealed role(s), rendered. Two of them for an unresolved Seer/Fool."""
    return " / ".join(roles.display(role_id) for role_id in entry["roles"])


def _player_row(session_data, user_id, entry):
    """One line of the roster: name, role, role model, lover heart."""
    name = _mention(user_id, entry["name"])
    if not entry["roles"]:
        return t.STANDIN_PLAYER_UNREVEALED.format(name=name)

    label = _role_label(entry)
    if entry["model"] is not None:
        # Resolved to a name here rather than stored as one, so a player renaming
        # mid-game cannot leave a stale label behind in the message.
        if session.name_of(session_data, entry["model"]) is not None:
            label += t.STANDIN_MODEL.format(name=_mention_player(session_data, entry["model"]))
    if entry["lover"]:
        label += t.STANDIN_LOVER
    return t.STANDIN_PLAYER_ROW.format(name=name, role=label)


def render_state(session_data, ended=False):
    """The live roster message: (html, keyboard).

    Mirrors the achievement manager's own layout — header, `Players (n / total)`, then a
    `Dead Players` section — because a replacement that reorganised the message would be
    the first thing anyone noticed at the moment they are looking for something familiar.
    """
    revealed, total = session.revealed_count(session_data)
    # The roster stays in the chat after the session ends, as the record of the game, so
    # it has to stop saying "GAME RUNNING" — and stop inviting reveals into a session that
    # no longer exists.
    msg = t.STANDIN_HEADER_ENDED if ended else t.STANDIN_HEADER + t.STANDIN_INTRO
    msg += t.STANDIN_PLAYERS_HEADER.format(revealed=revealed, total=total)

    dead = []
    for uid, entry in session.players_in_order(session_data):
        if entry["alive"]:
            msg += _player_row(session_data, uid, entry)
        else:
            dead.append((uid, entry))

    msg += t.STANDIN_DEAD_HEADER
    for uid, entry in dead:
        msg += _player_row(session_data, uid, entry)

    if session_data["unresolved"]:
        msg += t.STANDIN_UNRESOLVED.format(names=", ".join(html.escape(n) for n in session_data["unresolved"]))

    if ended:
        return msg, None
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
    await _load_attained(session_data, players)
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
    # A session nobody ever touches still has to expire, so the idle clock starts here
    # rather than on the first reveal.
    _schedule_idle(context, message.chat.id)
    logger.info("standin_started", chat_id=message.chat.id, players=len(players), unresolved=len(unresolved))


async def _load_attained(session_data, players):
    """Fetch what every player already holds, once, when the session opens.

    Nobody is hunting an achievement they finished months ago, so the post has to subtract
    them — and that is only affordable as one batch here rather than per render: a publish
    happens every few seconds during a busy round, and sixteen API calls each time would be
    both slow and rude to a stats API that owes us nothing.

    Failures degrade to "we don't know", which shows the player everything. The stats API
    is occasionally unavailable, and a game played during one of those minutes should still
    get a list — an achievement wrongly offered is a moment's confusion, one wrongly hidden
    is the thing this feature exists to prevent.
    """
    results = await asyncio.gather(*[api.get_achievements(uid) for uid, _ in players], return_exceptions=True)
    failed = 0
    for (uid, _), result in zip(players, results, strict=True):
        if isinstance(result, Exception):
            failed += 1
            continue
        session.set_attained(session_data, uid, [a["name"] for a in result])
    if failed:
        logger.warning("standin_attained_lookup_failed", players=failed)


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
    if await _beholder_claim(update, context, session_data, typed):
        return

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
    await _changed(context, message.chat.id, session_data)

    template = t.STANDIN_ROLE_SET_AMBIGUOUS if len(resolved) > 1 else t.STANDIN_ROLE_SET
    await message.reply_text(
        template.format(name=_mention(target_id, entry["name"]), role=_role_label(entry)),
        parse_mode=ParseMode.HTML,
    )


async def _beholder_claim(update, context, session_data, typed):
    """Handle "I am the Beholder, and…". True when the claim was dealt with here.

    Two shapes, and the difference between them matters to everyone else in the game:

        /role bhns                  — there is no Seer
        /role bhws <player>         — <player> is the Seer

    Both are a role reveal *and* an answer to the Seer/Fool question, because the game
    shows the Beholder who the real Seer is. A plain `/role bh` with nothing after it is
    just the role, and is left to the ordinary path.
    """
    message = update.message
    user = message.from_user
    words = typed.split()
    head = roles.normalise(words[0]) if words else ""

    if roles.normalise(typed) in _NO_SEER_CLAIMS:
        session.set_roles(session_data, user.id, ("beholder",))
        settled = session.set_no_seer(session_data)
        await _changed(context, message.chat.id, session_data)
        reply = t.STANDIN_BEHOLDER_NO_SEER.format(name=_mention(user.id, user.first_name))
        await message.reply_text(reply + _settled_note(session_data, settled), parse_mode=ParseMode.HTML)
        return True

    if head not in _WITH_SEER_CLAIMS or len(words) < 2:
        return False

    seer_id = _find_player(session_data, " ".join(words[1:]))
    if seer_id is None:
        # "bhws" is unambiguous about intent, so a name we cannot place is worth reporting.
        # A bare "beholder <something>" is more likely a role name we failed to parse, so
        # it falls through to the ordinary "did you mean" path instead.
        if head != roles.normalise("bhws"):
            return False
        await message.reply_text(t.STANDIN_UNKNOWN_TARGET, parse_mode=ParseMode.HTML)
        return True

    session.set_roles(session_data, user.id, ("beholder",))
    result = session.set_seer(session_data, seer_id)
    settled = result[1] if result else []
    await _changed(context, message.chat.id, session_data)
    reply = t.STANDIN_BEHOLDER_SEER.format(
        name=_mention(user.id, user.first_name), seer=_mention_player(session_data, seer_id)
    )
    await message.reply_text(reply + _settled_note(session_data, settled), parse_mode=ParseMode.HTML)
    return True


def _settled_note(session_data, settled):
    """Name anyone whose unsure seer/fool claim this just resolved."""
    if not settled:
        return ""
    return t.STANDIN_BEHOLDER_SETTLED.format(names=", ".join(_mention_player(session_data, uid) for uid in settled))


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
            t.STANDIN_MODEL_NEEDS_ROLE.format(name=_mention(target_id, target["name"])),
            parse_mode=ParseMode.HTML,
        )
        return
    if not any(role_id in _ROLE_MODEL_ROLES for role_id in target["roles"]):
        await message.reply_text(
            t.STANDIN_MODEL_WRONG_ROLE.format(name=_mention(target_id, target["name"]), role=_role_label(target)),
            parse_mode=ParseMode.HTML,
        )
        return

    session.set_model(session_data, target_id, model_id)
    await _changed(context, message.chat.id, session_data)
    await message.reply_text(
        t.STANDIN_MODEL_SET.format(
            name=_mention(target_id, target["name"]),
            model=_mention_player(session_data, model_id),
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

    await _changed(context, message.chat.id, session_data)

    if partner_id is None:
        await message.reply_text(
            t.STANDIN_LOVE_SET.format(name=_mention(first_id, entry["name"])), parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            t.STANDIN_LOVE_PAIR_SET.format(
                name=_mention(first_id, entry["name"]),
                partner=_mention_player(session_data, partner_id),
            ),
            parse_mode=ParseMode.HTML,
        )


# --- Ending ----------------------------------------------------------------


async def _is_chat_admin(context, chat_id, user_id):
    """Whether this user administrates the group. False if Telegram will not say.

    A group admin is not necessarily playing — they are usually the person who notices the
    session is still running after the game ended, which is exactly the moment somebody
    needs to be able to stop it without being on the roster.
    """
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except Exception as exc:
        # An unreachable API must not hand a stop to someone who has no claim to it.
        logger.warning("standin_admin_lookup_failed", user_id=user_id, error=str(exc))
        return False
    return getattr(member, "status", None) in ("administrator", "creator")


async def _may_stop(context, chat_id, session_data, user_id):
    """Who can end a session: its players, and the group's admins.

    The roster check comes first because it costs nothing — players stopping their own
    game is the common case, and only the unusual one is worth an API call for.
    """
    if session.is_member(session_data, user_id):
        return True
    return await _is_chat_admin(context, chat_id, user_id) or await is_admin_user(user_id)


async def _announce_stopped(context, chat_id, user_id, name):
    """Say in the chat who stopped it, once the session is already gone."""
    await context.bot.send_message(
        chat_id=chat_id,
        text=t.STANDIN_STOPPED_BY.format(name=_mention(user_id, name)),
        parse_mode=ParseMode.HTML,
    )


async def _finish(context, chat_id, session_data):
    """Close the roster message out: ended header, no instructions, no live button."""
    message_id = session_data.get("state_message_id")
    if message_id is None:
        return
    msg, _ = render_state(session_data, ended=True)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=msg,
            reply_markup=None,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
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
    await _announce_stopped(context, message.chat.id, message.from_user.id, message.from_user.first_name)


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
    if not await _may_stop(context, query.message.chat.id, session_data, user.id):
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
        await _announce_stopped(context, query.message.chat.id, user.id, user.first_name)
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
        name = _mention_player(session_data, change["user_id"])
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
            t.STANDIN_ALREADY_DEAD.format(name=_mention(target_id, entry["name"])), parse_mode=ParseMode.HTML
        )
        return

    session.set_alive(session_data, target_id, False)
    changes = session.apply_transforms(session_data)
    await _changed(context, message.chat.id, session_data)

    reply = t.STANDIN_DEAD_MARKED.format(name=_mention(target_id, entry["name"]))
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
    await _changed(context, message.chat.id, session_data)

    if not died and not revived and not learned and not changes:
        await message.reply_text(t.STANDIN_AD_NO_CHANGE, parse_mode=ParseMode.HTML)
        return

    def names(ids):
        return ", ".join(_mention_player(session_data, uid) for uid in ids)

    reply = t.STANDIN_AD_SUMMARY
    if died:
        reply += t.STANDIN_AD_DIED.format(names=names(died))
    if revived:
        reply += t.STANDIN_AD_REVIVED.format(names=names(revived))
    if learned:
        reply += t.STANDIN_AD_ROLES_LEARNED.format(names=", ".join(learned))
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
            t.STANDIN_MODEL_NEEDS_ROLE.format(name=_mention(target_id, target["name"])),
            parse_mode=ParseMode.HTML,
        )
        return
    if any(roles.has_tag(role_id, roles.STEAL_IMMUNE) for role_id in target["roles"]):
        await message.reply_text(
            t.STANDIN_STEAL_IMMUNE.format(name=_mention(target_id, target["name"]), role=_role_label(target)),
            parse_mode=ParseMode.HTML,
        )
        return

    stolen = _role_label(target)
    session.swap_roles(session_data, user.id, target_id)
    await _changed(context, message.chat.id, session_data)
    await message.reply_text(
        t.STANDIN_STEAL_DONE.format(
            thief=_mention(user.id, thief["name"]), role=stolen, name=_mention(target_id, target["name"])
        ),
        parse_mode=ParseMode.HTML,
    )


# --- The Possible Achievements post ----------------------------------------

# Telegram caps a message at 4096 characters, and this list is the one thing here that can
# genuinely overrun it: sixteen players with twenty-odd reachable achievements each is well
# past it. So the renderer degrades in steps rather than truncating blindly — first fewer
# rows each, then only the certain ones — because dropping *players* would be the one
# outcome nobody could work around, while dropping the least certain rows still leaves
# everyone able to see where they stand.
_LIST_LIMIT = 3900
_ROW_LADDER = (None, 8, 5, 3)

_ROW_TEMPLATES = {
    rulelist.CHECK: t.STANDIN_LIST_ROW,
    rulelist.MAYBE: t.STANDIN_LIST_ROW_MAYBE,
}


def _entry_sort_key(entry):
    """Certain rows first, then the lucky ones, then the ones needing a role change."""
    if entry["swing"]:
        return 2
    return 0 if entry["tier"] == rulelist.CHECK else 1


def _build_list(session_data, per_player, shared, row_cap, include_uncertain):
    """One rendering attempt. See _LIST_LIMIT for why there is more than one."""
    msg = t.STANDIN_LIST_HEADER
    listed = 0

    for uid, player_entry in session.players_in_order(session_data):
        if not player_entry["alive"] or not player_entry["roles"]:
            continue
        entries = sorted(per_player.get(uid, []), key=_entry_sort_key)
        # Nobody is hunting an achievement they already hold, so what a player has earned
        # is dropped before anything else. This is why the roster's attained lists are
        # fetched at /gs: without them the post is a list of things half the room finished
        # months ago.
        entries = [e for e in entries if not session.already_has(session_data, uid, e["name"])]
        if not include_uncertain:
            entries = [e for e in entries if e["tier"] == rulelist.CHECK and not e["swing"]]
        if not entries:
            continue

        listed += 1
        msg += t.STANDIN_LIST_PLAYER.format(name=html.escape(player_entry["name"]))
        shown = entries if row_cap is None else entries[:row_cap]
        for entry in shown:
            template = t.STANDIN_LIST_ROW_SWING if entry["swing"] else _ROW_TEMPLATES[entry["tier"]]
            msg += template.format(name=html.escape(entry["name"]))
        if len(entries) > len(shown):
            msg += t.STANDIN_LIST_MORE.format(count=len(entries) - len(shown))
        msg += "\n\n"

    groups = _group_sections(session_data, shared, include_uncertain)

    revealed, total = session.revealed_count(session_data)
    if not listed and not groups:
        msg += t.STANDIN_LIST_NOBODY if not revealed else t.STANDIN_LIST_NOTHING_POSSIBLE

    msg += groups
    msg += t.STANDIN_LIST_FOOTER.format(revealed=revealed, total=total)
    if not include_uncertain:
        msg += t.STANDIN_LIST_TRIMMED
    return msg


def _group_sections(session_data, shared, include_uncertain):
    """The bottom of the post: each roleless achievement, and who can still get it.

    Every living player is a candidate, revealed or not — these depend on no role, so a
    player who has not said what they are is as able to earn one as anybody. An achievement
    nobody is missing is left out entirely rather than printed with an empty list.
    """
    out = ""
    for entry in sorted(shared, key=lambda e: 0 if e["tier"] == rulelist.CHECK else 1):
        if not include_uncertain and entry["tier"] != rulelist.CHECK:
            continue
        eligible = [
            player_entry["name"]
            for uid, player_entry in session.players_in_order(session_data)
            if player_entry["alive"] and not session.already_has(session_data, uid, entry["name"])
        ]
        if not eligible:
            continue
        out += t.STANDIN_LIST_GROUP_HEADER.format(name=html.escape(entry["name"]), count=len(eligible))
        out += t.STANDIN_LIST_GROUP_NAMES.format(names=", ".join(html.escape(n) for n in eligible))
    return out


def render_list(session_data):
    """The Possible Achievements post.

    Byte-compatible with the game's own manager — an unindented player name, then indented
    " - " rows — so replying to it with /info returns the cards, exactly as it does for the
    incumbent's post. The status markers sit *after* the dash for the same reason.
    """
    revealed = session.revealed_roles(session_data)
    per_player, shared = feasibility.feasible(revealed, db.get_rules())

    for row_cap in _ROW_LADDER:
        msg = _build_list(session_data, per_player, shared, row_cap, include_uncertain=True)
        if len(msg) <= _LIST_LIMIT:
            return msg
    # Still too long with three rows each: drop everything uncertain and say so, rather
    # than let Telegram reject the message and leave the list frozen at its last edit.
    return _build_list(session_data, per_player, shared, 3, include_uncertain=False)


# --- Scheduling: one trailing debounce per chat ----------------------------

# Reveals arrive in a burst — sixteen players typing /role within a minute of each other —
# and every one of them changes both live messages. Editing on each would be sixteen edits
# a minute per message, which is how a bot meets Telegram's rate limiter.
#
# A *trailing* debounce is what the group asked for and is also the right shape: the first
# change schedules a publish five seconds out, and every change until then simply lands in
# the session and is picked up when it fires. Sixteen reveals in three seconds cost one
# edit, not sixteen, and nobody waits more than five seconds to see their reveal.
_DEBOUNCE_SECONDS = 5
_PUBLISH_JOB = "standin_publish:{}"

# A session that outlives its game keeps capturing /role in a chat where the real manager
# has come back online, which is worse than one that ended early — so it expires on
# silence, with a warning first so a quiet stretch mid-game is survivable.
_IDLE_WARNING_SECONDS = 10 * 60
_IDLE_GRACE_SECONDS = 2 * 60
_IDLE_JOB = "standin_idle:{}"


def _job_queue(context):
    """The JobQueue, or None when the bot was built without one."""
    return getattr(context, "job_queue", None)


def _schedule_publish(context, chat_id):
    """Ask for a publish in five seconds, unless one is already pending."""
    queue = _job_queue(context)
    if queue is None:
        return
    name = _PUBLISH_JOB.format(chat_id)
    if queue.get_jobs_by_name(name):
        # Already pending. It will render whatever the session says when it fires, which
        # includes this change — re-scheduling would only push the whole burst later.
        return
    queue.run_once(_publish, _DEBOUNCE_SECONDS, chat_id=chat_id, name=name)


def _schedule_idle(context, chat_id):
    """Restart the idle countdown. Any activity pushes the end of the session back."""
    queue = _job_queue(context)
    if queue is None:
        return
    name = _IDLE_JOB.format(chat_id)
    for job in queue.get_jobs_by_name(name):
        job.schedule_removal()
    queue.run_once(_idle_warning, _IDLE_WARNING_SECONDS, chat_id=chat_id, name=name)


async def _changed(context, chat_id, session_data):
    """Record activity and schedule the messages to catch up. Called after every write."""
    session.touch(session_data, _now())
    _schedule_publish(context, chat_id)
    _schedule_idle(context, chat_id)


async def _publish(context):
    """Bring both live messages up to date. The debounce's callback."""
    chat_id = context.job.chat_id
    session_data = session.get(context.chat_data)
    if session_data is None:
        # Ended between the schedule and the fire. Nothing to say.
        return

    await _refresh_state(context, chat_id, session_data)

    msg = render_list(session_data)
    message_id = session_data.get("list_message_id")
    if message_id is None:
        posted = await context.bot.send_message(
            chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        if posted is not None:
            session_data["list_message_id"] = posted.message_id
        return
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest:
        # Identical to what is already there — a change that unlocked nothing.
        pass


async def _idle_warning(context):
    """Ten minutes of silence: say the session is about to end, and set the grace timer."""
    chat_id = context.job.chat_id
    if session.get(context.chat_data) is None:
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text=t.STANDIN_IDLE_WARNING.format(minutes=_IDLE_WARNING_SECONDS // 60, grace=_IDLE_GRACE_SECONDS // 60),
        parse_mode=ParseMode.HTML,
    )
    queue = _job_queue(context)
    if queue is not None:
        queue.run_once(_idle_end, _IDLE_GRACE_SECONDS, chat_id=chat_id, name=_IDLE_JOB.format(chat_id))


async def _idle_end(context):
    """The grace period ran out. End the session rather than leave it capturing /role."""
    chat_id = context.job.chat_id
    session_data = session.get(context.chat_data)
    if session_data is None:
        return
    session.end(context.chat_data)
    await _finish(context, chat_id, session_data)
    await context.bot.send_message(chat_id=chat_id, text=t.STANDIN_IDLE_ENDED, parse_mode=ParseMode.HTML)
    logger.info("standin_expired", chat_id=chat_id)


# --- /la -------------------------------------------------------------------


async def list_achievements_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/la` — point at the live list rather than posting a second copy of it.

    The list already exists and already updates itself, so re-posting it would leave two in
    the chat, one of them going stale. A reply pointing at the live one is both shorter and
    correct a minute later.
    """
    session_data = _session_for(update, context)
    if session_data is None:
        return

    message = update.message
    logger.info("command", command="la", user_id=message.from_user.id, user=unidecode(message.from_user.first_name))

    message_id = session_data.get("list_message_id")
    if message_id is None:
        await message.reply_text(t.STANDIN_LA_NOTHING_YET, parse_mode=ParseMode.HTML)
        return

    await context.bot.send_message(
        chat_id=message.chat.id,
        text=t.STANDIN_LA_POINTER,
        reply_parameters=ReplyParameters(message_id=message_id, allow_sending_without_reply=True),
        parse_mode=ParseMode.HTML,
    )
