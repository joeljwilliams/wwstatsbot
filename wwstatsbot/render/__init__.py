"""Everything that turns state into the bytes a user sees.

`templates.py` holds every user-visible string, `builders.py` the stat messages (apart
from the handlers because a slash command and an inline card render the *same* bytes),
`badges.py` the supporter badge, and `wwstats.py` the /achievements report. HTML escaping
happens here, once; callers pass raw values.
"""
