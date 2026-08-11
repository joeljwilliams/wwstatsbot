"""Parsing achievement names out of the game bot's "Possible Achievements" message.

The message nests achievements under the player they're available to, and the two
levels are told apart only by indentation:

    Possible Achievements:

    Ren
     - Traffic Control

The regression this file guards (commit dba4af2) is subtle: player names sit at the
left margin and **can themselves begin with a dash** — "-Mini | ˹ʙᴜ…" is a real
player — so matching any line starting with "-" scoops names up as achievements, which
then get reported as unmatchable or, worse, fuzzy-match onto an unrelated achievement.
Both the indent and the space after the dash carry weight.
"""

import main

REAL_MESSAGE = """Possible Achievements:

Ren
 - Traffic Control
 - Strongest Alpha

-Mini | ˹ʙᴜ...
 - Busy Night
"""


def test_extracts_indented_rows_only():
    assert main._extract_possible_achievements(REAL_MESSAGE) == [
        "Traffic Control",
        "Strongest Alpha",
        "Busy Night",
    ]


def test_dash_prefixed_player_name_is_not_read_as_an_achievement():
    """The dba4af2 regression: '-Mini | ˹ʙᴜ...' must never appear as an achievement."""
    names = main._extract_possible_achievements(REAL_MESSAGE)
    assert not any("Mini" in n for n in names)


def test_dash_without_a_following_space_is_not_a_row():
    """A player literally named '-Someone' has no space after the dash."""
    assert main._extract_possible_achievements("-Someone\n - Real Achievement") == [
        "Real Achievement"
    ]


def test_falls_back_to_unindented_rows_when_none_are_indented():
    """Text that lost its leading spaces (copy-paste, a trimming client) still works."""
    assert main._extract_possible_achievements("- One\n- Two") == ["One", "Two"]


def test_indented_rows_win_over_unindented_ones():
    """When both shapes are present the indented ones are the real achievement rows."""
    text = "-PlayerName | tag\n - Real One\n- Not A Row"
    assert main._extract_possible_achievements(text) == ["Real One"]


def test_multiple_dashes_and_tabs_are_accepted():
    assert main._extract_possible_achievements("\t-- Double Dash") == ["Double Dash"]


def test_dedupes_case_insensitively_preserving_first_seen_order():
    text = " - Busy Night\n - busy night\n - Alpha\n - BUSY NIGHT"
    assert main._extract_possible_achievements(text) == ["Busy Night", "Alpha"]


def test_trailing_whitespace_is_stripped():
    assert main._extract_possible_achievements(" - Padded   ") == ["Padded"]


def test_empty_and_none_input():
    assert main._extract_possible_achievements("") == []
    assert main._extract_possible_achievements(None) == []


def test_no_rows_at_all():
    assert main._extract_possible_achievements("Just a sentence.\nAnother line.") == []


def test_a_bare_dash_is_not_a_row():
    assert main._extract_possible_achievements(" - \n - Real") == ["Real"]


# --- Name -> achievement resolution ----------------------------------------------


def test_best_match_is_case_insensitive(achievements):
    assert main._best_achievement_match("busy night")["name"] == "Busy Night"
    assert main._best_achievement_match("BUSY NIGHT")["name"] == "Busy Night"


def test_best_match_returns_none_for_an_unknown_name(achievements):
    assert main._best_achievement_match("no such achievement") is None


async def test_resolve_cards_reports_unmatched_names(achievements, no_fts):
    cards, not_found = await main._resolve_achievement_cards(["Busy Night", "Nonsense XYZ"])
    assert len(cards) == 1
    assert "<b>Busy Night</b>" in cards[0]
    assert not_found == ["Nonsense XYZ"]


async def test_resolve_cards_falls_back_to_fuzzy_search(achievements, no_fts):
    """An inexact name still resolves via build_info_results' substring fallback."""
    cards, not_found = await main._resolve_achievement_cards(["Liquid"])
    assert not_found == []
    assert "<b>Liquid Business</b>" in cards[0]
