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
    "<a href='tg://user?id={user_id}'>{name}</a>:\n"
    "(✅ attained · ☑️ not yet)\n\n"
)
SEARCH_ROW = "{mark} <code>{name}</code>\n"
SEARCH_ATTAINED = "✅"
SEARCH_NOT_ATTAINED = "☑️"
# Shown when more matches exist than the display cap; nudges toward a narrower query.
SEARCH_TRUNCATED = "\n<i>…and {extra} more. Refine your search to see them.</i>\n"

# --- Markdown: /achv achievement list (wwstats.py) -------------------------

ATTAINED_HEADER = "*ATTAINED ({attained}/{total}):*\n"
MISSING_MAIN = "*MISSING ({missing}/{total}):*\n"
MISSING_HEADER = "*MISSING AND ATTAINABLE VIA PLAYING ({count}/{total}):*\n\n"
NOT_VIA_PLAYING_HEADER = "*NOT DIRECTLY ATTAINABLE VIA PLAYING ({count}/{total}):*\n\n"
INACTIVE_HEADER = "*INACTIVE ({count}/{total}):*\n\n"
# One achievement entry in the missing/not-via-playing/inactive sections.
ACHV_LINE = "`- {name}`\n>>> _{desc}_\n"
