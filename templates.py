"""Centralised user-facing message templates.

All human-visible strings live here so they can be edited without touching the
handler/builder logic. Two Telegram parse modes are in play:

  * HTML     — the /stats, /kills, /killedby, /deaths, /info and inline output
               (assembled in builders.py and the handlers/ modules).
  * Markdown — the /achv achievement list (built in wwstats.py).

Templates use str.format named fields; call `.format(**kwargs)`. Small render
helpers cover the patterns that repeat across builders.
"""


def N_(message):
    """Mark a string for extraction without translating it.

    gettext's standard no-op marker, and `pybabel extract` recognises `N_` by default. Every
    constant below therefore lands in locales/messages.pot while this module goes on holding
    plain English — translation happens at render time against the resolved locale.

    The marker has to be *here* rather than at the point of use, because extraction only sees
    literal strings at the call site: `_(t.SEARCH_HEADER)` is invisible to pybabel, which
    cannot follow the name back to this file. That is what lets prose stay in one place and
    still produce standard .po files whose msgids are the English source.
    """
    return message


# --- HTML: stat builders (builders.py) -------------------------------------

KILLS_HEADER = N_("Players <a href='tg://user?id={user_id}'>{name}</a> most killed:\n")
KILLED_BY_HEADER = N_("Players who killed <a href='tg://user?id={user_id}'>{name}</a> most:\n")
DEATHS_HEADER = N_("Types of deaths that <a href='tg://user?id={user_id}'>{name}</a> most had:\n")

# One "<count> <label>" row, used by kills and killed-by.
COUNT_ROW = N_("<code>{count:<5}</code> <b>{label}</b>\n")
DEATH_ROW = N_("<code>{percent}%</code>   <b>{method}</b>   <code>(approx. {total})</code>\n")

STATS_NAME = N_("<a href='tg://user?id={user_id}'>{name} the {role}</a>\n")
STATS_NAME_BY_ID = N_("{name} the {role}\n")
STATS_ACHIEVEMENTS = N_("<code>{count:<5}</code> Achievements Unlocked!\n")
STATS_WON = N_("<code>{total:<5}</code> Games Won <code>({percent}%)</code>\n")
STATS_LOST = N_("<code>{total:<5}</code> Games Lost <code>({percent}%)</code>\n")
STATS_SURVIVED = N_("<code>{total:<5}</code> Games Survived <code>({percent}%)</code>\n")
STATS_TOTAL = N_("<code>{total:<5}</code> Total Games\n")
STATS_MOST_KILLED = N_("<code>{times:<5}</code> times I've gleefully killed {name}\n")
STATS_MOST_KILLED_BY = N_("<code>{times:<5}</code> times I've been slaughtered by {name}\n\n")
NO_GAMES = N_("<a href='tg://user?id={user_id}'>{name}</a> has not played any games.")
NO_GAMES_BY_ID = N_("{name} has not played any games.")

# --- HTML: achievement info card (builders.py) -----------------------------

ACHV_CARD = N_("<b>{name}</b>\n\n{desc}\n\nType: <code>{type}</code>")
ACHV_CARD_NOTES = N_("\n\n<blockquote expandable>{notes}</blockquote>")

# --- HTML: /version release + build info (handlers/misc.py) ----------------

# The release version answers "what is deployed", the branch and short commit answer
# "exactly which build". The full 40-char sha used to be shown next to the short one,
# which was redundant on screen — it now only backs the link.
#
# Two variants (linked / plain) mirror the STATS_NAME split so no conditional lives
# inside the string. The handler picks LINKED when a commit_url exists.
VERSION_INFO_LINKED = N_(
    "<b>wwstatsbot</b> <code>v{version}</code>\n"
    "Branch: <code>{branch}</code>\n"
    'Commit: <a href="{commit_url}">{short_commit}</a>'
)
VERSION_INFO_PLAIN = N_(
    "<b>wwstatsbot</b> <code>v{version}</code>\nBranch: <code>{branch}</code>\nCommit: <code>{short_commit}</code>"
)

# --- HTML: /search achievement match list (handlers/search.py) -------------

# Each matching achievement is tagged with whether the target user has it.
SEARCH_HEADER = N_("Achievements matching <b>{query}</b> for <a href='tg://user?id={user_id}'>{name}</a>:\n\n")
SEARCH_ROW = N_("{mark} <code>{name}</code>\n")
SEARCH_ATTAINED = N_("✅")
SEARCH_NOT_ATTAINED = N_("☑️")
# Shown when more matches exist than the display cap; nudges toward a narrower query.
SEARCH_TRUNCATED = N_("\n<i>…and {extra} more. Refine your search to see them.</i>\n")

# --- HTML: /schall — who among mentioned players has an achievement --------

# Head of the reply: the single achievement the query matched (shown clearly so
# it's obvious what everyone is being checked against), then one of the lists.
SCHALL_HEADER = N_("Achievement: <b>{name}</b>\n<i>{desc}</i>\n\nChecked {count} player{plural} for it:\n")
# Section header for whichever list is on screen; only one shows at a time and
# the button below swaps them (marks mirror /search: ☑️ not attained, ✅ attained).
SCHALL_MISSING_HEADER = N_("\n☑️ <b>Not obtained ({count})</b>\n")
SCHALL_HAVE_HEADER = N_("\n✅ <b>Obtained ({count})</b>\n")
SCHALL_USER_ROW = N_("<a href='tg://user?id={user_id}'>{name}</a>\n")
SCHALL_NONE_ROW = N_("<i>none</i>\n")
# Toggle button labels — each names the list you'd switch *to*, with its size so
# the count is visible without tapping.
SCHALL_TOGGLE_TO_HAVE = N_("✅ Show who has it ({count})")
SCHALL_TOGGLE_TO_MISSING = N_("☑️ Show who hasn't ({count})")
# Footer noting players that couldn't be checked: @username mentions carry no
# user id (so no stats lookup is possible) and any that errored out.
SCHALL_UNRESOLVED = N_("\n<i>Couldn't check: {names}</i>\n")
# These name /sch, not /schall: /sch is the advertised spelling and routes here on
# its own when it replies to a bot message that mentions players. /schall still
# works when typed, it's just no longer the way anyone is told to reach this.
SCHALL_USAGE = N_(
    "Invalid parameter! Syntax:\n<code>/sch [achievement_to_search]</code>\n(reply to a message that mentions players)"
)
SCHALL_EXPIRED = N_("This list has expired. Please run /sch again.")
# Shown when someone other than the requester (or an admin) taps the toggle. Callback
# answers are plain text — no HTML, and Telegram truncates past ~200 characters.
SCHALL_NOT_YOURS = N_("Only {name} can switch this list. Send /sch yourself to get your own.")
# /schall with no reply re-uses the players from this chat's last reply-based run. The age
# is always shown, so a result built from a remembered roster is never mistaken for a fresh
# one — a game group's line-up changes every round. Deliberately terse: it qualifies the
# "Checked N players" line above it rather than explaining itself.
SCHALL_FROM_CACHE = N_("🕐 <i>{age}</i>\n")
# Nothing remembered for this chat yet.
SCHALL_NO_REPLY_NO_CACHE = N_(
    "Reply to a message that mentions players with <code>/sch &lt;achievement&gt;</code>.\n"
    "After that, <code>/schall &lt;achievement&gt;</code> re-checks the same players for {ttl}."
)
# Remembered, but older than the TTL.
SCHALL_CACHE_STALE = N_(
    "This chat's remembered player list is more than {ttl} old, so I've forgotten it.\n"
    "Reply to a player list with <code>/sch &lt;achievement&gt;</code> to start again."
)

# --- HTML: /info achievement card pager (handlers/achievements.py) ---------

# Group hand-off: one public message with a button, so several people can each
# pull their own copy of the cards without re-running the command.
ALLINFO_PROMPT = N_(
    "Found info for <b>{count}</b> achievement{plural} from that list.\n"
    "Tap the button to get the info cards in your PM."
)
ALLINFO_NOT_MATCHED = N_("Could not match: {names}")
ALLINFO_PM_BUTTON = N_("📥 Send me the info in PM")
# The pager: one card at a time. The position goes *after* the card so the first
# line stays the bare achievement name, which /setnote matches a reply against.
ALLINFO_PAGE_FOOTER = N_("\n\n<i>{index}/{total}</i>")
ALLINFO_PREV = N_("◀️ Prev")
ALLINFO_NEXT = N_("Next ▶️")
ALLINFO_SEND_ALL = N_("📄 Send all {count}")
# Errors and button acknowledgements. Like the /sch strings above, these name
# /info rather than the now-hidden /allinfo.
# --- HTML: /roll — pick who gets a once-only achievement --------------------

# A rolled player's name, linked when the post it was read from mentioned them.
ROLL_MENTION = N_("<a href='tg://user?id={user_id}'>{name}</a>")

ROLL_USAGE = N_(
    "Reply to a <b>Possible Achievements</b> message with <code>/roll &lt;achievement&gt;</code> "
    "and I'll pick between the players who can still get it."
)
ROLL_NO_LIST = N_("I can't find any players in that message. Reply to a Possible Achievements post.")
ROLL_NOT_LISTED = N_("Nobody in that list can get <b>{name}</b>.")
# Refused rather than resolved to the first match: picking one would decide a game on a
# coin toss nobody saw.
ROLL_AMBIGUOUS = N_("<b>{name}</b> matches more than one achievement in that list — be more specific.")
# The candidates are named before the winner so the roll is visibly between *those* people
# — a winner on its own is just an assertion.
ROLL_RESULT = N_("Rolling <b>{name}</b> for {players}…\n\n\N{DIRECT HIT} Winner is <b>{winner}</b>")
# Rolling between one person is not a roll, and pretending otherwise reads as rigged.
ROLL_ONLY_ONE = N_("{winner} is the only one who can get <b>{name}</b> — no roll needed.")

ALLINFO_NEED_REPLY = N_("Reply to a 'Possible Achievements' message with <code>/info</code>.")
ALLINFO_NO_ACHIEVEMENTS = N_(
    "No achievements found in that message. Make sure it contains lines like <code>- Achievement Name</code>."
)
ALLINFO_NO_MATCH = N_("No matching achievements found.")
ALLINFO_EXPIRED = N_("This request has expired. Please run /info again.")
ALLINFO_GONE = N_("Those achievements are no longer available.")
ALLINFO_NO_PM = N_(
    "I can't message you yet. Start a private chat with me first (tap my name, then Start), then tap the button again."
)
ALLINFO_SENT_PAGER = N_("Sent the achievement info to your PM ✅")
ALLINFO_SENT_ALL = N_("Sent {count} card{plural} to your PM ✅")

# --- Markdown: /achv achievement list (wwstats.py) -------------------------

ATTAINED_HEADER = N_("*ATTAINED ({attained}/{total}):*\n")
MISSING_MAIN = N_("*MISSING ({missing}/{total}):*\n")
MISSING_HEADER = N_("*MISSING AND ATTAINABLE VIA PLAYING ({count}/{total}):*\n\n")
NOT_VIA_PLAYING_HEADER = N_("*NOT DIRECTLY ATTAINABLE VIA PLAYING ({count}/{total}):*\n\n")
INACTIVE_HEADER = N_("*INACTIVE ({count}/{total}):*\n\n")
# One achievement entry in the missing/not-via-playing/inactive sections.
ACHV_LINE = N_("`- {name}`\n>>> _{desc}_\n")

# --- HTML: privileged commands (handlers/admin.py) --------------------------

# Refusals. Deliberately say which tier is required rather than just "denied", so an
# ordinary admin hitting a superuser-only command knows why.
ADMIN_ONLY_ADD = N_("Only the superuser can add admins.")
ADMIN_ONLY_REMOVE = N_("Only the superuser can remove admins.")
ADMIN_ONLY_LIST = N_("Only the superuser can list admins.")
ADMIN_ONLY_SQL = N_("Only the superuser can run raw SQL.")
ADMIN_ONLY_NOTES = N_("Only admins can edit notes.")

ADMIN_ADD_USAGE = N_("Usage: reply to a user with /addadmin, or /addadmin <user_id>.")
ADMIN_DEL_USAGE = N_("Usage: reply to a user with /deladmin, or /deladmin <user_id>.")
ADMIN_ADDED = N_("Added <a href='tg://user?id={user_id}'>{name}</a> as an admin.")
ADMIN_REMOVED = N_("Removed admin {user_id}.")
ADMIN_NOT_AN_ADMIN = N_("That user is not an admin.")
ADMIN_LIST_EMPTY = N_("No admins yet.")
ADMIN_LIST_HEADER = N_("<b>Admins:</b>")
ADMIN_LIST_ROW = N_("<code>{user_id}</code> {name}{username}")
ADMIN_LIST_UNKNOWN_NAME = N_("(unknown)")

# /setnote and /clearnote.
NOTE_SET_USAGE = N_(
    "Reply to an achievement /info card with <code>/setnote &lt;note&gt;</code> "
    "or <code>/setnote prob &lt;probability&gt;</code>."
)
NOTE_SET_NEEDS_TEXT = N_(
    "Please provide the text: <code>/setnote &lt;note&gt;</code> or "
    "<code>/setnote prob &lt;probability&gt;</code>. Use /clearnote to remove a field."
)
NOTE_CLEAR_USAGE = N_(
    "Reply to an achievement /info card with <code>/clearnote</code> (memo), "
    "<code>/clearnote prob</code>, or <code>/clearnote all</code>."
)
NOTE_UNIDENTIFIED = N_("Could not identify the achievement from that message. Reply to a single /info card.")
NOTE_UPDATED = N_("Note updated.\n\n")

# /db console.
DB_USAGE = N_("Usage: <code>/db &lt;sql&gt;</code>\nRuns a single SQL statement.")
DB_ERROR = N_("<b>SQL error:</b>\n<pre>{error}</pre>")
DB_RESULT = N_("<pre>{body}</pre>{footer}")
DB_ROW_COUNT = N_("\n({count} row{plural})")
DB_ROWS_SHOWN = N_(", showing first {count}")
DB_TRUNCATED = N_("\n… (truncated)")
DB_STATUS_OK = N_("OK")

# --- HTML: search and achievement lookup usage errors ----------------------

# Shared by /search and /info: same wording, different command named in the syntax line.
SEARCH_USAGE = N_("Invalid parameter! Syntax:\n<code>/search [achievement_to_search]</code>\n")
INFO_USAGE = N_("Invalid parameter! Syntax:\n<code>/info [achievement_to_search]</code>\n")
QUERY_TOO_SHORT = N_("Please enter at least 3 letters to search for!\n")
NO_MATCHES = N_("No matching achievements found!\n")
SCHALL_NEEDS_DIRECT_MENTIONS = N_(
    "Reply to a message that mentions players directly. I can't check plain @username mentions (they carry no user id)."
)
# Fallback when a stored payload has an owner id but no name.
SCHALL_REQUESTER_FALLBACK = N_("the requester")
# The cache lifetime, worded. A placeholder rather than a baked-in "60 minutes" so the
# number has one source (handlers.search._SCHALL_CACHE_TTL) and the phrasing can be
# translated around it. Becomes an ngettext call when plurals land.
SCHALL_TTL_LABEL = N_("{count} minutes")

# --- Markdown / plain: /achievements delivery, /start, /about ---------------

ACHV_SENT_TO_PM = N_("I have sent you your achievement list in PM.")
ACHV_NEEDS_PM = N_("You have to start me in PM first.")
START_ME_BUTTON = N_("Start Me!")
START_PRIVATE = N_("Thank you for starting me. Use /stats and /achievements to check your related stats!")
ABOUT = N_(
    "Use /stats for stats. Use /achievements or /achv for achivement list."
    "\n\nThis is an actively maintained fork of the original `@wolfcardbot` "
    "(originally by Carson True, later edited by @jeffffc)."
    "\nSource for this maintained version: [{repo}]({repo})"
    "\nUse /version to see the exact running build."
)

# --- Inline mode result titles (handlers/inline.py) ------------------------

INLINE_MY_STATS = N_("My Stats")
INLINE_MY_KILLS = N_("My Kills")
INLINE_MY_KILLED_BY = N_("My Killed By")
INLINE_MY_DEATHS = N_("My Deaths")
INLINE_NO_MATCH_TITLE = N_("No matching achievements")
INLINE_NO_MATCH_BODY = N_("No matching achievements found.")

# --- The "/" command menu (main.py) ----------------------------------------

# Descriptions only; the command words themselves are not translated. Telegram accepts a
# separate menu per language, so these are what a user sees in their own language.
CMD_STATS = N_("Your game stats (or reply to another player)")
CMD_KILLS = N_("Players you've killed the most")
CMD_KILLEDBY = N_("Players who've killed you the most")
CMD_DEATHS = N_("Your most common causes of death")
CMD_SEARCH = N_("Search your achievements, or reply to a player list to check everyone")
CMD_ACHIEVEMENTS = N_("List all achievements")
CMD_INFO = N_("Look up an achievement, or reply to a list to get them all")
CMD_ABOUT = N_("About this bot")
CMD_VERSION = N_("Show the running bot version")
CMD_START = N_("Start the bot in a private chat")


# --- HTML: the stand-in game session (handlers/gamesession.py) --------------
#
# Worded to match the achievement manager this stands in for, because standing in
# convincingly is mostly a matter of nobody noticing: its own posts say "GAME RUNNING!",
# "Players (16 / 16):" and "<name>'s role was set to: <Role>", and a replacement that
# invented its own phrasing would read as a different tool at exactly the moment people
# are looking for a familiar one.
#
# The one place we deliberately differ is the instruction line. The manager says "reveal
# your roles by saying them", which it can do because it reads ordinary messages; we only
# ever see commands, so ours names /role explicitly rather than promising something that
# would silently not work.

# One place builds a tappable player name, so the link markup lives here with the rest of
# the presentation rather than being concatenated in the handler.
STANDIN_MENTION = N_("<a href='tg://user?id={user_id}'>{name}</a>")

STANDIN_HEADER = N_("<b>GAME RUNNING!</b>\n\n")
# The roster message outlives the session — it stays in the chat as the record of the game
# — so a stopped session must not go on announcing itself as running. Its instructions go
# with it: telling people to reveal a role into a session that has ended is worse than
# saying nothing.
STANDIN_HEADER_ENDED = N_("<b>GAME ENDED</b>\n\n")
STANDIN_INTRO = N_(
    "Standing in for the achievement manager. Reveal your role with <code>/role &lt;role&gt;</code> — "
    "e.g. <code>/role gunner</code> or <code>/role sk</code>. To set somebody else's, @mention them "
    'or reply to them. Hit "Stop" when the game ends.\n\n'
)
STANDIN_PLAYERS_HEADER = N_("<b>Players ({revealed} / {total}):</b>\n")
# {name} arrives already rendered as a tg://user link (see gamesession._mention), which is
# why nothing here escapes it: a bare display name would be plain text where every other
# mention in the chat is tappable.
STANDIN_PLAYER_ROW = N_("{name}: {role}\n")
STANDIN_PLAYER_UNREVEALED = N_("{name}: <i>not revealed</i>\n")
# The role model rides inline in parentheses, the way the manager renders it:
#   J J: Wild Child 👶 (omu)
STANDIN_MODEL = N_(" ({name})")
# A heart on each partner rather than a couple line — again the manager's own convention,
# and it is why lover status is a per-player flag with an optional partner.
STANDIN_LOVER = N_(" \N{HEAVY BLACK HEART}")
STANDIN_DEAD_HEADER = N_("\n<b>Dead Players:</b>\n")
STANDIN_UNRESOLVED = N_("\n<i>Not tracked (no user id): {names}</i>\n")
STANDIN_STOP_BUTTON = N_("Stop")
# Two presses, so the first only arms. The manager's Stop takes one, and it sits under a
# dozen thumbs for a whole game.
STANDIN_STOP_ARM = N_("Press Stop again to end the session.")
STANDIN_STOP_NOT_YOURS = N_("Only players in this game can stop it.")
STANDIN_STOP_EXPIRED = N_("That session has already ended.")

STANDIN_ALREADY_RUNNING = N_(
    "A stand-in session is already running in this chat. Stop it first, or use <code>/gsend</code>."
)
STANDIN_NEEDS_ROSTER = N_(
    "Reply to the game bot's player list with <code>/gs@{username}</code> so I know who is playing."
)
STANDIN_NO_PLAYERS = N_(
    "That message doesn't mention any players I can track. I need direct mentions — plain "
    "@username mentions carry no user id."
)
STANDIN_ENDED = N_("Stand-in session ended.")
# Said in the chat, not just to whoever pressed: a game ending is everybody's business, and
# the button's toast is only ever seen by the person who tapped it.
STANDIN_STOPPED_BY = N_("{name} has considered the game stopped!")

# Confirmations. "<name>'s role was set to: <Role>" is the manager's exact wording.
STANDIN_ROLE_SET = N_("{name}'s role was set to: {role}")
STANDIN_ROLE_SET_AMBIGUOUS = N_("{name}'s role was set to: {role}\n<i>Both are being counted until you know which.</i>")
STANDIN_ROLE_USAGE = N_("Usage: <code>/role &lt;role&gt;</code> — try <code>/role seer</code>.")
STANDIN_ROLE_UNKNOWN = N_("I don't know a role called <b>{role}</b>.")
STANDIN_ROLE_DID_YOU_MEAN = N_("\nDid you mean: {names}?")
STANDIN_MODEL_SET = N_("{name}'s rolemodel is now {model}")
# Players are named by mention or reply — never by typing a display name, which has
# spaces and emoji in it and cannot be told apart from the rest of the line.
STANDIN_MODEL_USAGE = N_(
    "Usage: <code>/rm @rolemodel</code> (yours), or the same in reply to a player, "
    "or <code>/rm @player @rolemodel</code>."
)
# Only the Wild Child and the Doppelgänger have a role model. A /rm against anyone else is
# reported rather than stored, because a stored one would never fire a transform and the
# mistake would only surface much later as an achievement that failed to appear.
STANDIN_MODEL_WRONG_ROLE = N_(
    "{name} is {role}, which has no rolemodel. Only the Wild Child \N{BABY} and the "
    "Doppelg\N{LATIN SMALL LETTER A WITH DIAERESIS}nger \N{PERFORMING ARTS} do."
)
STANDIN_MODEL_NEEDS_ROLE = N_("{name} hasn't revealed yet, so I can't tell if they have a rolemodel.")
STANDIN_LOVE_SET = N_("{name} is now in love.")
STANDIN_LOVE_PAIR_SET = N_("{name} and {partner} are now in love.")

# The Beholder is shown the real Seer at the start of the game, so their claim is the one
# that settles the Seer/Fool question for everyone else.
STANDIN_BEHOLDER_NO_SEER = N_("{name} is the Beholder \N{EYE} — and there is no Seer in this game.")
STANDIN_BEHOLDER_SEER = N_("{name} is the Beholder \N{EYE}, and {seer} is the Seer \N{MAN WITH TURBAN}.")
STANDIN_BEHOLDER_SETTLED = N_("\nUnsure seer/fool claims settled as the Fool \N{PLAYING CARD BLACK JOKER}: {names}")
STANDIN_NOT_IN_GAME = N_("{name} isn't in this game's player list.")
STANDIN_UNKNOWN_TARGET = N_(
    "I need a player from this game — reply to them, or @mention them. "
    "Typing a name won't do: I match on the mention, not the spelling."
)


# --- HTML: deaths, the roster sync and the Thief (handlers/gamesession.py) --

STANDIN_DEAD_MARKED = N_("{name} is dead.")

# An alt is a second account of somebody already playing. They keep their role — it still
# shapes everyone else's achievements — but they are not offered any of their own.
STANDIN_ALT_SET = N_("{name} is an alt — leaving them out of the achievements list.")
STANDIN_ALT_CLEARED = N_("{name} is not an alt any more, and is back in the achievements list.")
# Shown on the roster so it is obvious why they have no entry in the list.
STANDIN_ALT_MARK = N_(" <i>(alt)</i>")
STANDIN_ALT_NEEDS_TARGET = N_(
    "Reply to the account with <code>/alt</code>, @mention it, or send <code>/alt</code> on its own to mark your own."
)
STANDIN_ALREADY_DEAD = N_("{name} is already dead.")

STANDIN_AD_USAGE = N_("Reply to the game bot's player list with <code>/ad</code> and I'll follow it.")
# The roster states its own counts ("Players Alive: 11/16"), so a parse can be checked
# before it is applied. It is applied as a full reset — anyone the game bot lists is alive,
# anyone it doesn't is dead — which is exactly why a misread must change nothing at all.
STANDIN_AD_MISMATCH = N_(
    "That roster says {claimed} of {total} alive, but I can only see {found} player{plural} in it. "
    "Nothing changed — the list may be from a different game, or it didn't mention everyone directly."
)
STANDIN_AD_NO_CHANGE = N_("Roster matches what I have — nobody's status changed.")
STANDIN_AD_SUMMARY = N_("Roster followed.\n")
STANDIN_AD_DIED = N_("\N{SKULL} Now dead: {names}\n")
STANDIN_AD_REVIVED = N_("\N{SLIGHTLY SMILING FACE} Back among the living: {names}\n")
STANDIN_AD_ROLES_LEARNED = N_("Learned from the death notices: {names}\n")

# Role changes the deaths triggered, appended to whichever command caused them.
STANDIN_TRANSFORM_HEADER = N_("\n")
STANDIN_TRANSFORM_ROW = N_(
    "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS} {name} is now {role} ({reason})\n"
)
STANDIN_TRANSFORM_SORROW = N_("\N{BROKEN HEART} {name} dies of sorrow.\n")
STANDIN_REASON_MODEL_DIED = N_("their rolemodel died")
STANDIN_REASON_SEER_DIED = N_("the seer is gone")
STANDIN_REASON_WOLVES_DEAD = N_("the wolves are gone")

STANDIN_STEAL_USAGE = N_("Usage: <code>/steal @player</code>, or reply to them.")
STANDIN_STEAL_NOT_THIEF = N_("Only the Thief \N{SMILING FACE WITH HORNS} can steal a role.")
# The game protects these outright, so a /steal against one is a rules mistake worth
# reporting rather than a swap worth recording.
STANDIN_STEAL_IMMUNE = N_("The Thief can't steal from {name} — {role} is out of reach.")
STANDIN_STEAL_DONE = N_("{thief} stole {role} from {name}, who is now the Thief \N{SMILING FACE WITH HORNS}.")


# --- HTML: the Possible Achievements post (handlers/gamesession.py) ---------
#
# Deliberately the same shape the game's achievement manager posts, because /info already
# parses that shape (handlers/achievements.py::_extract_possible_achievements): an
# unindented player name, then indented " - " rows. Reply to this post with /info and the
# cards come back, exactly as they do for the incumbent's.

STANDIN_LIST_HEADER = N_("Possible Achievements:\n\n")
# Name alone, no role: the game's own manager lists players this way, and the role is
# already on the roster message a few lines up.
STANDIN_LIST_PLAYER = N_("{name}\n")
# Three row shapes, one prefix each. The dash and the space are load-bearing — they are
# what /info matches on — so a marker always follows them rather than replacing them.
STANDIN_LIST_ROW = N_(" - {name}\n")
STANDIN_LIST_ROW_MAYBE = N_(" - \N{BLACK QUESTION MARK ORNAMENT} {name}\n")
STANDIN_LIST_ROW_SWING = N_(" - \N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS} {name}\n")
STANDIN_LIST_MORE = N_(" - <i>…and {count} more</i>\n")
STANDIN_LIST_NOBODY = N_("<i>Nothing yet — no roles revealed.</i>\n")
# Not the same thing, and saying the first when the second is true reads as a bug: early on
# a lone revealed Villager really does have nothing available, because almost everything
# needs some *other* role to be in play.
STANDIN_LIST_NOTHING_POSSIBLE = N_("<i>Nothing available yet from what has been revealed.</i>\n")

# Achievements no role gates go at the bottom, each with the players who can still get it —
# the shape the game's own manager uses. Printing them under every player instead would say
# the same thing sixteen times and crowd out the rows that are about somebody in particular.
# No marker on these, matching the manager: everything in this post is "possible", and a
# section that belongs to nobody in particular has no per-player certainty to qualify.
STANDIN_LIST_GROUP_HEADER = N_("{name} ({count}):\n")
STANDIN_LIST_GROUP_NAMES = N_("{names}\n\n")

STANDIN_LIST_FOOTER = N_(
    "\n\N{CLOCK FACE ONE OCLOCK} {revealed} of {total} revealed · "
    "\N{BLACK QUESTION MARK ORNAMENT} needs luck · "
    "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS} if your role changes\n"
)
# Shown when the full list will not fit in one Telegram message and the uncertain rows
# were dropped to make room. Silently truncating would read as "this is everything".
STANDIN_LIST_TRIMMED = N_("<i>Trimmed to fit — reply with /info for any of them.</i>\n")

STANDIN_LA_POINTER = N_("The list is here, and updates as roles come in.")
STANDIN_LA_NOTHING_YET = N_("Nobody has revealed a role yet — the list appears once someone does.")

STANDIN_IDLE_WARNING = N_(
    "No updates for {minutes} minutes. I'll end the stand-in session in {grace} minutes unless "
    "something happens — any <code>/role</code>, <code>/dead</code> or <code>/ad</code> keeps it alive."
)
STANDIN_IDLE_ENDED = N_("Stand-in session ended — nothing happened for a while.")
