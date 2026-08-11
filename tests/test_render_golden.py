"""Golden (characterization) tests: the exact bytes every builder emits today.

These exist for one purpose. `main.py` is about to be split into modules, which is
almost entirely code motion, and the output is HTML assembled by string concatenation
with manual `html.escape()` — the kind of code where a move silently changes a space,
drops an escape, or escapes twice.

So the assertions here are deliberately whole-string equality, not "contains". If a
refactor changes one character of user-visible output, a test fails.

**If one of these fails during a refactor, the refactor is wrong — not the test.**
Only edit an expectation when you are intentionally changing what users see, and then
say so in the commit message.

The corollary: a deliberate copy change means editing a golden, and that edit *is* the
review artifact — it shows exactly which bytes users will see differently. Keep such
changes in their own commit, never mixed with a refactor, so the two are never
ambiguous in a diff.
"""

from conftest import assert_json_roundtrips

import builders
from handlers import achievements as achv_handlers
from handlers import admin, search

# --- Achievement cards -----------------------------------------------------------


def test_card_plain(achievements):
    achv = next(a for a in achievements if a["name"] == "Welcome to Hell")
    assert builders.format_single_achv(achv) == ("<b>Welcome to Hell</b>\n\nPlay a game\n\nType: <code>game-end</code>")


def test_card_escapes_apostrophe_and_leaves_at_mentions(achievements):
    """The description holds an apostrophe and an @handle; only the former is escaped."""
    achv = next(a for a in achievements if a["name"] == "O HAI DER!")
    assert builders.format_single_achv(achv) == (
        "<b>O HAI DER!</b>\n\nPlay a game with Para&#x27;s secret account (not @para949)\n\nType: <code>game-end</code>"
    )


def test_card_with_both_note_fields_uses_expandable_blockquote(achievements):
    achv = next(a for a in achievements if a["name"] == "Liquid Business")
    assert builders.format_single_achv(achv) == (
        "<b>Liquid Business</b>\n\n"
        "Drink the potion &amp; survive\n\n"
        "Type: <code>instantaneous</code>\n\n"
        "<blockquote expandable>\N{MEMO} Needs the drunk role.\n"
        "\N{GAME DIE} ~5%</blockquote>"
    )


def test_card_normalises_legacy_unmarked_notes():
    """A note stored before the marker scheme existed gains its memo marker on display."""
    achv = {"name": "X", "desc": "d", "type": "game-end", "notes": "just some old text"}
    assert builders.format_single_achv(achv) == (
        "<b>X</b>\n\nd\n\nType: <code>game-end</code>\n\n"
        "<blockquote expandable>\N{MEMO} just some old text</blockquote>"
    )


def test_card_defaults_missing_type_to_instantaneous():
    achv = {"name": "X", "desc": "d", "notes": ""}
    assert builders.format_single_achv(achv).endswith("Type: <code>instantaneous</code>")


# --- /schall renderer ------------------------------------------------------------

PAYLOAD = {
    "name": "Liquid Business",
    "desc": "Drink the potion & survive",
    "missing": [(1, "Alice"), (2, "Bob & Co")],
    "have": [(3, "Carol")],
    "unresolved": ["@dave"],
}


def test_schall_missing_view():
    msg, keyboard = search._render_schall(PAYLOAD, "TOK", show_have=False)
    assert msg == (
        "Achievement: <b>Liquid Business</b>\n"
        "<i>Drink the potion &amp; survive</i>\n\n"
        "Checked 3 players for it:\n\n"
        "☑️ <b>Not obtained (2)</b>\n"
        "<a href='tg://user?id=1'>Alice</a>\n"
        "<a href='tg://user?id=2'>Bob &amp; Co</a>\n"
        "\n<i>Couldn't check: @dave</i>\n"
    )
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "✅ Show who has it (1)"
    assert button.callback_data == "schall:TOK:have"


def test_schall_have_view():
    msg, keyboard = search._render_schall(PAYLOAD, "TOK", show_have=True)
    assert msg == (
        "Achievement: <b>Liquid Business</b>\n"
        "<i>Drink the potion &amp; survive</i>\n\n"
        "Checked 3 players for it:\n\n"
        "✅ <b>Obtained (1)</b>\n"
        "<a href='tg://user?id=3'>Carol</a>\n"
        "\n<i>Couldn't check: @dave</i>\n"
    )
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "☑️ Show who hasn't (2)"
    assert button.callback_data == "schall:TOK:missing"


def test_schall_empty_bucket_renders_none_row_and_singular_player():
    payload = {"name": "X", "desc": "d", "missing": [], "have": [(1, "A")], "unresolved": []}
    msg, _ = search._render_schall(payload, "TOK", show_have=False)
    assert msg == (
        "Achievement: <b>X</b>\n<i>d</i>\n\n"
        "Checked 1 player for it:\n\n"  # singular, not "1 players"
        "☑️ <b>Not obtained (0)</b>\n"
        "<i>none</i>\n"
    )


def test_schall_escapes_exactly_once_after_a_persistence_roundtrip():
    """Names are stored raw and escaped at render, so a re-render can't double-escape.

    This is the invariant that keeps Redis-persisted payloads safe: if storage held
    escaped names, `&` would become `&amp;amp;` the second time a view rendered.
    """
    restored = assert_json_roundtrips(PAYLOAD)
    # JSON turns the (id, name) tuples into lists; they must unpack identically.
    first, _ = search._render_schall(PAYLOAD, "TOK", show_have=False)
    second, _ = search._render_schall(restored, "TOK", show_have=False)
    assert first == second
    assert "&amp;amp;" not in second
    assert "Bob &amp; Co" in second


# --- /info pager -----------------------------------------------------------------


def test_allinfo_page_footer_and_wraparound():
    msg, keyboard = achv_handlers._render_allinfo_page(["CARD-A", "CARD-B", "CARD-C"], 0, "TOK")
    assert msg == "CARD-A\n\n<i>1/3</i>"
    prev, nxt = keyboard.inline_keyboard[0]
    # Prev from the first card wraps to the last, so the keyboard never changes shape.
    assert prev.callback_data == "allinfo:p:TOK:2"
    assert nxt.callback_data == "allinfo:p:TOK:1"
    assert keyboard.inline_keyboard[1][0].callback_data == "allinfo:all:TOK"
    assert keyboard.inline_keyboard[1][0].text == "\U0001f4c4 Send all 3"


def test_allinfo_page_wraps_forward_from_last():
    _, keyboard = achv_handlers._render_allinfo_page(["A", "B", "C"], 2, "TOK")
    assert keyboard.inline_keyboard[0][1].callback_data == "allinfo:p:TOK:0"


def test_allinfo_single_card_has_no_keyboard():
    msg, keyboard = achv_handlers._render_allinfo_page(["ONLY"], 0, "TOK")
    assert msg == "ONLY\n\n<i>1/1</i>"
    assert keyboard is None


def test_allinfo_unmatched_escapes_and_caps_at_ten():
    assert achv_handlers._allinfo_unmatched(["a", "b & c", "d"]) == "Could not match: a, b &amp; c, d"
    capped = achv_handlers._allinfo_unmatched([f"n{i}" for i in range(12)])
    assert capped == "Could not match: n0, n1, n2, n3, n4, n5, n6, n7, n8, n9, ..."


# --- /db console formatter -------------------------------------------------------


def test_sql_result_select_renders_null_and_row_count():
    assert admin._format_sql_result(["id", "name"], [(1, "a"), (2, None)], "SELECT 2") == (
        "<pre>id | name\n1 | a\n2 | NULL</pre>\n(2 rows)"
    )


def test_sql_result_singular_row():
    assert admin._format_sql_result(["n"], [(1,)], "SELECT 1").endswith("\n(1 row)")


def test_sql_result_non_select_shows_only_status():
    assert admin._format_sql_result([], [], "UPDATE 3") == "<pre>UPDATE 3</pre>"
    assert admin._format_sql_result([], [], None) == "<pre>OK</pre>"


def test_sql_result_caps_rows_and_says_so():
    out = admin._format_sql_result(["n"], [(i,) for i in range(60)], "SELECT 60")
    assert out.endswith("(60 rows), showing first 50")
    assert "\n49</pre>" in out and "\n50\n" not in out


def test_sql_result_escapes_html_in_values():
    out = admin._format_sql_result(["v"], [("<script>",)], "SELECT 1")
    assert "&lt;script&gt;" in out and "<script>" not in out


# --- Stat builders (against the mocked stats API) --------------------------------


async def test_stats_msg(stats_api):
    assert await builders.build_stats_msg(7, "Alice") == (
        "<a href='tg://user?id=7'>Alice the Villager</a>\n"
        "<code>2    </code> Achievements Unlocked!\n"
        "<code>60   </code> Games Won <code>(60%)</code>\n"
        "<code>40   </code> Games Lost <code>(40%)</code>\n"
        "<code>50   </code> Games Survived <code>(50%)</code>\n"
        "<code>100  </code> Total Games\n"
        "<code>7    </code> times I've gleefully killed Bob\n"
        "<code>3    </code> times I've been slaughtered by Al &amp; Sons\n\n"
    )


async def test_stats_msg_by_id_omits_the_user_link(stats_api):
    msg = await builders.build_stats_msg(7, "7", by_id=True)
    assert msg.startswith("7 the Villager\n")
    assert "tg://user" not in msg


async def test_stats_msg_no_games(stats_api):
    stats_api.routes["/Stats/PlayerStats/"] = {}
    assert await builders.build_stats_msg(7, "Alice") == (
        "<a href='tg://user?id=7'>Alice</a> has not played any games."
    )
    stats_api.routes["/Stats/PlayerStats/"] = {}
    assert await builders.build_stats_msg(7, "7", by_id=True) == "7 has not played any games."


async def test_stats_msg_omits_most_killed_lines_when_null(stats_api):
    stats_api.routes["/Stats/PlayerStats/"] = dict(
        stats_api.routes["/Stats/PlayerStats/"], mostKilled=None, mostKilledBy=None
    )
    msg = await builders.build_stats_msg(7, "Alice")
    assert msg.endswith("<code>100  </code> Total Games\n")


async def test_kills_msg(stats_api):
    assert await builders.build_kills_msg(7, "Alice") == (
        "Players <a href='tg://user?id=7'>Alice</a> most killed:\n"
        "<code>7    </code> <b>Bob</b>\n"
        "<code>3    </code> <b>Al &amp; Sons</b>\n"
    )


async def test_killed_by_msg(stats_api):
    assert await builders.build_killed_by_msg(7, "Alice") == (
        "Players who killed <a href='tg://user?id=7'>Alice</a> most:\n<code>5    </code> <b>Carol</b>\n"
    )


async def test_deaths_msg_derives_approximate_totals(stats_api):
    # 100 played - 50 survived = 50 deaths; 40% -> 20, 20% -> 10.
    assert await builders.build_deaths_msg(7, "Alice") == (
        "Types of deaths that <a href='tg://user?id=7'>Alice</a> most had:\n"
        "<code>40%</code>   <b>Lynched</b>   <code>(approx. 20)</code>\n"
        "<code>20%</code>   <b>Eaten</b>   <code>(approx. 10)</code>\n"
    )


async def test_empty_lists_render_header_only(stats_api):
    stats_api.routes["/Stats/PlayerKills/"] = []
    assert await builders.build_kills_msg(7, "Alice") == ("Players <a href='tg://user?id=7'>Alice</a> most killed:\n")
