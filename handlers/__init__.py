"""Telegram handlers, grouped by command family.

Split out of main.py, which now holds only the registration table
(build_application) and the process lifecycle. Each module here owns one family of
user-facing commands plus the private helpers only that family uses.

The grouping is not arbitrary. Two commands reroute to another handler based on what
they reply to — /sch to display_search_all, and a bare /info to all_info_cmd — and both
pairs are deliberately kept in the *same* module, so those calls stay ordinary
intra-module references rather than becoming cross-module imports. tests/test_wiring.py
asserts every command is registered and bound to the callback it should be.
"""
