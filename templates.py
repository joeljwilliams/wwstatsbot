"""Centralised user-facing message templates.

All human-visible strings live here so they can be edited without touching the
handler/builder logic. Two Telegram parse modes are in play:

  * HTML     — the /stats, /kills, /killedby, /deaths, /info and inline output
               (built in main.py).
  * Markdown — the /achv achievement list (built in wwstats.py).

Templates use str.format named fields; call `.format(**kwargs)`. Small render
helpers cover the patterns that repeat across builders.
"""

# --- HTML: stat builders (main.py) -----------------------------------------

KILLS_HEADER = "Players <a href='tg://user?id={user_id}'> {name}</a> most killed:\n"
KILLED_BY_HEADER = "Players who killed <a href='tg://user?id={user_id}'>{name}</a> most:\n"
DEATHS_HEADER = "Types of deaths that <a href='tg://user?id={user_id}'>{name}</a> most had:\n"

# One "<count> <label>" row, used by kills and killed-by.
COUNT_ROW = "<code>{count:<5}</code> <b>{label}</b>\n"
DEATH_ROW = "<code>{percent}%</code>   <b>{method}</b>   <code>(approx. {total})</code>\n"

STATS_NAME = "<a href='tg://user?id={user_id}'>{name} the {role}</a>\n"
STATS_NAME_BY_ID = "{name} the {role}\n"
STATS_ACHIEVEMENTS = "<code>{count:<5}</code> Achievements Unlocked!\n"
STATS_WON = "<code>{total:<5}</code> Games Won <code>({percent}%)</code>\n"
STATS_LOST = "<code>{total:<5}</code> Games Lost <code>({percent}%)</code>\n"
STATS_SURVIVED = "<code>{total:<5}</code> Games Survived <code>({percent}%)</code>\n"
STATS_TOTAL = "<code>{total:<5}</code> Total Games\n"
STATS_MOST_KILLED = "<code>{times:<5}</code> times I've gleefully killed {name}\n"
STATS_MOST_KILLED_BY = "<code>{times:<5}</code> times I've been slaughted by {name}\n\n"
NO_GAMES = "<a href='tg://user?id={user_id}'>{name}</a> has not played any games."
NO_GAMES_BY_ID = "{name} has not played any games."

# --- HTML: achievement info card (main.py) ---------------------------------

ACHV_CARD = "<b>{name}</b>\n\n{desc}\n\nType: <code>{type}</code>"
ACHV_CARD_NOTES = "\n\n<blockquote expandable>{notes}</blockquote>"

# --- HTML: /version build info (main.py) -----------------------------------

# Two variants (linked / plain) mirror the STATS_NAME split so no conditional
# lives inside the string. The handler picks LINKED when a commit_url exists.
VERSION_INFO_LINKED = (
    "<b>wwstatsbot</b>\n"
    "Branch: <code>{branch}</code>\n"
    "Commit: <a href=\"{commit_url}\">{short_commit}</a> <code>{commit}</code>"
)
VERSION_INFO_PLAIN = (
    "<b>wwstatsbot</b>\n"
    "Branch: <code>{branch}</code>\n"
    "Commit: <code>{short_commit}</code>"
)

# --- HTML: /search achievement match list (main.py) ------------------------

# Each matching achievement is tagged with whether the target user has it.
SEARCH_HEADER = (
    "Achievements matching <b>{query}</b> for "
    "<a href='tg://user?id={user_id}'>{name}</a>:\n\n"
)
SEARCH_ROW = "{mark} <code>{name}</code>\n"
SEARCH_ATTAINED = "✅"
SEARCH_NOT_ATTAINED = "☑️"
# Shown when more matches exist than the display cap; nudges toward a narrower query.
SEARCH_TRUNCATED = "\n<i>…and {extra} more. Refine your search to see them.</i>\n"

# --- HTML: /schall — who among mentioned players has an achievement --------

# Head of the reply: the single achievement the query matched (shown clearly so
# it's obvious what everyone is being checked against), then one of the lists.
SCHALL_HEADER = (
    "Achievement: <b>{name}</b>\n"
    "<i>{desc}</i>\n\n"
    "Checked {count} player{plural} for it:\n"
)
# Section header for whichever list is on screen; only one shows at a time and
# the button below swaps them (marks mirror /search: ☑️ not attained, ✅ attained).
SCHALL_MISSING_HEADER = "\n☑️ <b>Not obtained ({count})</b>\n"
SCHALL_HAVE_HEADER = "\n✅ <b>Obtained ({count})</b>\n"
SCHALL_USER_ROW = "<a href='tg://user?id={user_id}'>{name}</a>\n"
SCHALL_NONE_ROW = "<i>none</i>\n"
# Toggle button labels — each names the list you'd switch *to*, with its size so
# the count is visible without tapping.
SCHALL_TOGGLE_TO_HAVE = "✅ Show who has it ({count})"
SCHALL_TOGGLE_TO_MISSING = "☑️ Show who hasn't ({count})"
# Footer noting players that couldn't be checked: @username mentions carry no
# user id (so no stats lookup is possible) and any that errored out.
SCHALL_UNRESOLVED = "\n<i>Couldn't check: {names}</i>\n"
# These name /sch, not /schall: /sch is the advertised spelling and routes here on
# its own when it replies to a bot message that mentions players. /schall still
# works when typed, it's just no longer the way anyone is told to reach this.
SCHALL_NEED_REPLY = (
    "Reply to a message that mentions players with "
    "<code>/sch &lt;achievement&gt;</code>."
)
SCHALL_USAGE = (
    "Invalid parameter! Syntax:\n<code>/sch [achievement_to_search]</code>\n"
    "(reply to a message that mentions players)"
)
SCHALL_EXPIRED = "This list has expired. Please run /sch again."

# --- HTML: /info achievement card pager (main.py) ---------------------------

# Group hand-off: one public message with a button, so several people can each
# pull their own copy of the cards without re-running the command.
ALLINFO_PROMPT = (
    "Found info for <b>{count}</b> achievement{plural} from that list.\n"
    "Tap the button to get the info cards in your PM."
)
ALLINFO_NOT_MATCHED = "Could not match: {names}"
ALLINFO_PM_BUTTON = "📥 Send me the info in PM"
# The pager: one card at a time. The position goes *after* the card so the first
# line stays the bare achievement name, which /setnote matches a reply against.
ALLINFO_PAGE_FOOTER = "\n\n<i>{index}/{total}</i>"
ALLINFO_PREV = "◀️ Prev"
ALLINFO_NEXT = "Next ▶️"
ALLINFO_SEND_ALL = "📄 Send all {count}"
# Errors and button acknowledgements. Like the /sch strings above, these name
# /info rather than the now-hidden /allinfo.
ALLINFO_NEED_REPLY = (
    "Reply to a 'Possible Achievements' message with <code>/info</code>."
)
ALLINFO_NO_ACHIEVEMENTS = (
    "No achievements found in that message. Make sure it contains lines like "
    "<code>- Achievement Name</code>."
)
ALLINFO_NO_MATCH = "No matching achievements found."
ALLINFO_EXPIRED = "This request has expired. Please run /info again."
ALLINFO_GONE = "Those achievements are no longer available."
ALLINFO_NO_PM = (
    "I can't message you yet. Start a private chat with me first "
    "(tap my name, then Start), then tap the button again."
)
ALLINFO_SENT_PAGER = "Sent the achievement info to your PM ✅"
ALLINFO_SENT_ALL = "Sent {count} card{plural} to your PM ✅"

# --- Markdown: /achv achievement list (wwstats.py) -------------------------

ATTAINED_HEADER = "*ATTAINED ({attained}/{total}):*\n"
MISSING_MAIN = "*MISSING ({missing}/{total}):*\n"
MISSING_HEADER = "*MISSING AND ATTAINABLE VIA PLAYING ({count}/{total}):*\n\n"
NOT_VIA_PLAYING_HEADER = "*NOT DIRECTLY ATTAINABLE VIA PLAYING ({count}/{total}):*\n\n"
INACTIVE_HEADER = "*INACTIVE ({count}/{total}):*\n\n"
# One achievement entry in the missing/not-via-playing/inactive sections.
ACHV_LINE = "`- {name}`\n>>> _{desc}_\n"
