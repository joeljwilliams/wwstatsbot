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
