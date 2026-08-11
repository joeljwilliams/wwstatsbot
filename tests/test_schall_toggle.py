"""Who may work the /schall toggle button.

The button flips one message between "who has it" and "who hasn't". Before this, anyone in
the chat could tap it — so in a busy group the list flipped under the person who asked for
it, and whoever tapped last decided what everyone saw.

Now the requester may flip it, admins may (they moderate), and anyone else is told whose
list it is. The denied tap answers *without* editing, so the message keeps whichever view
its owner chose.
"""

from conftest import SUPERUSER_ID, FakeCallbackQuery, FakeContext, FakeUpdate, FakeUser, bot_message, message

import db
import templates as t
from handlers import search

REQUESTER = 7
BYSTANDER = 12345
ADMIN = 555

PAYLOAD = {
    "name": "Busy Night",
    "desc": "Be visited by four different roles in one night",
    "missing": [(1, "Alice")],
    "have": [(2, "Bob")],
    "unresolved": [],
    "requested_by": REQUESTER,
    "requested_by_name": "Alice",
}


def context(**kwargs):
    return FakeContext(bot_data={"schall": {"TOK": dict(PAYLOAD, **kwargs)}})


async def tap(user_id, ctx, view="have"):
    query = FakeCallbackQuery(data="schall:TOK:" + view, from_user=FakeUser(user_id, "Tapper"))
    await search.schall_callback(FakeUpdate(callback_query=query), ctx)
    return query


class Tripwire:
    """Records whether it was awaited. Used to prove the fast path skips the database."""

    def __init__(self, result=False):
        self.called = False
        self.result = result

    async def __call__(self, user_id):
        self.called = True
        return self.result


# --- Allowed ----------------------------------------------------------------------


async def test_the_requester_may_toggle():
    query = await tap(REQUESTER, context())
    assert query.edits, "the requester's tap must flip the view"
    assert "Obtained (1)" in query.edits[-1][0]


async def test_the_requester_path_does_not_touch_the_database(monkeypatch):
    """The common case by far, so it must not cost an admins lookup per tap."""
    tripwire = Tripwire()
    monkeypatch.setattr(db, "is_admin", tripwire)
    await tap(REQUESTER, context())
    assert not tripwire.called


async def test_an_admin_may_toggle_someone_elses_list(monkeypatch):
    monkeypatch.setattr(db, "is_admin", Tripwire(result=True))
    query = await tap(ADMIN, context())
    assert query.edits, "an admin's tap must flip the view"


async def test_the_superuser_may_toggle_without_a_database_lookup(monkeypatch):
    """is_superuser short-circuits, so the superuser works even if the table is unreachable."""
    tripwire = Tripwire()
    monkeypatch.setattr(db, "is_admin", tripwire)
    query = await tap(SUPERUSER_ID, context())
    assert query.edits
    assert not tripwire.called


# --- Refused ----------------------------------------------------------------------


async def test_a_bystander_is_refused(monkeypatch):
    monkeypatch.setattr(db, "is_admin", Tripwire(result=False))
    query = await tap(BYSTANDER, context())

    assert query.edits == [], "a refused tap must not change the message"
    assert query.answers[-1]["show_alert"] is True


async def test_the_refusal_names_the_requester(monkeypatch):
    monkeypatch.setattr(db, "is_admin", Tripwire(result=False))
    query = await tap(BYSTANDER, context())
    assert query.answers[-1]["text"] == t.SCHALL_NOT_YOURS.format(name="Alice")
    assert "Alice" in query.answers[-1]["text"]


async def test_the_refusal_falls_back_when_no_name_was_stored(monkeypatch):
    """Defensive: an owner id without a name must still produce a sensible alert."""
    monkeypatch.setattr(db, "is_admin", Tripwire(result=False))
    query = await tap(BYSTANDER, context(requested_by_name=None))
    assert "the requester" in query.answers[-1]["text"]


async def test_a_refused_tap_leaves_the_payload_intact(monkeypatch):
    """The list must stay usable by its owner after someone else tries."""
    monkeypatch.setattr(db, "is_admin", Tripwire(result=False))
    ctx = context()
    await tap(BYSTANDER, ctx)
    query = await tap(REQUESTER, ctx)
    assert query.edits, "the owner must still be able to toggle afterwards"


# --- Backwards compatibility ------------------------------------------------------


async def test_a_payload_stored_before_owners_existed_stays_open(monkeypatch):
    """With REDIS_URL set, payloads survive a restart. One stored before this feature has
    no owner recorded — locking its requester out of a live message would be worse than
    leaving it open, so those stay tappable by anyone."""
    tripwire = Tripwire(result=False)
    monkeypatch.setattr(db, "is_admin", tripwire)
    legacy = {k: v for k, v in PAYLOAD.items() if k not in ("requested_by", "requested_by_name")}
    ctx = FakeContext(bot_data={"schall": {"TOK": legacy}})

    query = await tap(BYSTANDER, ctx)
    assert query.edits, "a legacy payload must not become unusable"
    assert not tripwire.called, "an ownerless payload should not trigger an admins lookup"


# --- The predicate ----------------------------------------------------------------


async def test_may_toggle_matrix(monkeypatch):
    monkeypatch.setattr(db, "is_admin", Tripwire(result=False))
    assert await search._may_toggle(REQUESTER, PAYLOAD) is True
    assert await search._may_toggle(SUPERUSER_ID, PAYLOAD) is True
    assert await search._may_toggle(BYSTANDER, PAYLOAD) is False
    assert await search._may_toggle(BYSTANDER, {"requested_by": None}) is True


# --- The command records the owner -------------------------------------------------


async def test_a_run_records_who_asked(achievements, no_fts, stats_api):
    from conftest import FakeEntity

    replied = bot_message(
        "Alice Bob",
        entities=[
            FakeEntity("text_mention", 0, 5, FakeUser(1, "Alice")),
            FakeEntity("text_mention", 6, 3, FakeUser(2, "Bob")),
        ],
    )
    msg = message("/sch busy", from_user=FakeUser(REQUESTER, "Requester"), reply_to_message=replied)
    ctx = FakeContext(args=["busy"])
    await search.display_search_all(FakeUpdate(message=msg), ctx)

    payload = next(iter(ctx.bot_data["schall"].values()))
    assert payload["requested_by"] == REQUESTER
    assert payload["requested_by_name"] == "Requester"


async def test_the_owner_survives_the_persistence_roundtrip(achievements, no_fts, stats_api):
    """bot_data is stored as JSON, so the owner must come back as an int, not a string."""
    from conftest import FakeEntity, assert_json_roundtrips

    replied = bot_message("Alice", entities=[FakeEntity("text_mention", 0, 5, FakeUser(1, "Alice"))])
    msg = message("/sch busy", from_user=FakeUser(REQUESTER, "Requester"), reply_to_message=replied)
    ctx = FakeContext(args=["busy"])
    await search.display_search_all(FakeUpdate(message=msg), ctx)

    restored = assert_json_roundtrips(ctx.bot_data)
    payload = next(iter(restored["schall"].values()))
    assert payload["requested_by"] == REQUESTER
    assert await search._may_toggle(REQUESTER, payload) is True
