"""Token-keyed callback state in bot_data.

Telegram caps `callback_data` at 64 bytes, so buttons carry only a token and the real
payload lives in `context.bot_data`. Two properties matter and neither is obvious from
reading a single function:

* the stores are **bounded** (200) with insertion-order eviction, so an expired token is
  a normal runtime case rather than an edge case — every callback handler must cope;
* payloads must stay **JSON-serializable**, because RedisPersistence stores the whole
  bot_data blob as JSON. Tuples survive the trip only by degrading to lists, which the
  renderers have to tolerate.
"""

from conftest import FakeContext, assert_json_roundtrips

from handlers import achievements as achv_handlers
from handlers import search


def test_store_allinfo_returns_a_token_and_stores_under_it():
    ctx = FakeContext()
    token = achv_handlers._store_allinfo_names(ctx, ["Busy Night"])
    assert ctx.bot_data["allinfo"][token] == ["Busy Night"]


def test_tokens_are_unique():
    ctx = FakeContext()
    tokens = {achv_handlers._store_allinfo_names(ctx, ["x"]) for _ in range(50)}
    assert len(tokens) == 50


def test_allinfo_store_is_bounded_and_evicts_oldest_first():
    ctx = FakeContext()
    first = achv_handlers._store_allinfo_names(ctx, ["first"])
    for i in range(achv_handlers._ALLINFO_MAX):
        achv_handlers._store_allinfo_names(ctx, ["n{}".format(i)])
    assert len(ctx.bot_data["allinfo"]) <= achv_handlers._ALLINFO_MAX
    # The oldest token is gone — its button now reports "expired", by design.
    assert first not in ctx.bot_data["allinfo"]


def test_schall_store_is_bounded_and_evicts_oldest_first():
    ctx = FakeContext()
    first = search._store_schall_result(ctx, {"name": "first"})
    for i in range(search._SCHALL_MAX):
        search._store_schall_result(ctx, {"name": "n{}".format(i)})
    assert len(ctx.bot_data["schall"]) <= search._SCHALL_MAX
    assert first not in ctx.bot_data["schall"]


def test_stores_share_bot_data_without_colliding():
    ctx = FakeContext()
    a = achv_handlers._store_allinfo_names(ctx, ["names"])
    s = search._store_schall_result(ctx, {"name": "x"})
    assert ctx.bot_data["allinfo"][a] == ["names"]
    assert ctx.bot_data["schall"][s] == {"name": "x"}


def test_allinfo_payload_is_json_serializable():
    """A list of names round-trips unchanged."""
    ctx = FakeContext()
    achv_handlers._store_allinfo_names(ctx, ["Busy Night", "Liquid Business"])
    assert assert_json_roundtrips(ctx.bot_data) == ctx.bot_data


def test_schall_payload_survives_json_with_tuples_degrading_to_lists():
    """The renderer unpacks (id, name) pairs, so lists must work identically."""
    ctx = FakeContext()
    payload = {
        "name": "X",
        "desc": "d",
        "missing": [(1, "Alice")],
        "have": [(2, "Bob")],
        "unresolved": ["@dave"],
    }
    token = search._store_schall_result(ctx, payload)
    restored = assert_json_roundtrips(ctx.bot_data)["schall"][token]

    assert restored["missing"] == [[1, "Alice"]]  # tuple -> list
    # ...and the renderer must not care which it got.
    before, _ = search._render_schall(payload, token, show_have=False)
    after, _ = search._render_schall(restored, token, show_have=False)
    assert before == after


def test_callback_data_stays_within_telegrams_64_byte_cap():
    """The whole reason state is token-keyed rather than embedded in the button."""
    ctx = FakeContext()
    token = search._store_schall_result(ctx, {"name": "X", "desc": "d", "missing": [], "have": [], "unresolved": []})
    _, keyboard = search._render_schall(ctx.bot_data["schall"][token], token, show_have=False)
    data = keyboard.inline_keyboard[0][0].callback_data
    assert len(data.encode()) <= 64

    _, kb = achv_handlers._render_allinfo_page(["a", "b"], 0, token)
    for row in kb.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
