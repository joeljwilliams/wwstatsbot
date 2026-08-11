"""The two-tier permission model.

* **Superuser** — an env-var id comparison: /addadmin, /deladmin, /admins, /db.
* **Admin** — superuser *or* a row in the `admins` table: /setnote, /clearnote.

`db.run_sql` executes whatever SQL it is handed. It is safe only because of its
superuser gate, so the tests below assert not merely that an unauthorised user gets a
refusal but that the privileged function is **never reached** — a refusal printed after
the damage is done would still pass a weaker assertion.

`main.py` is about to be split, and moving a handler is exactly how a guard clause gets
dropped, so every gated command is covered.
"""

import pytest
from conftest import SUPERUSER_ID, FakeContext, FakeUpdate, FakeUser, message

import db
import main
import settings


class Tripwire:
    """Records whether it was called; raises if it ever is."""

    def __init__(self):
        self.called = False

    async def __call__(self, *args, **kwargs):
        self.called = True
        raise AssertionError("privileged function reached without authorisation")


@pytest.fixture
def superuser():
    return FakeUser(SUPERUSER_ID, "Root")


@pytest.fixture
def outsider():
    return FakeUser(12345, "Randall")


@pytest.fixture
def not_an_admin(monkeypatch):
    async def _no(user_id):
        return False

    monkeypatch.setattr(db, "is_admin", _no)


@pytest.fixture
def is_an_admin(monkeypatch):
    async def _yes(user_id):
        return True

    monkeypatch.setattr(db, "is_admin", _yes)


# --- The predicates themselves ---------------------------------------------------


def test_superuser_matches_the_configured_id():
    assert main.is_superuser(SUPERUSER_ID) is True
    assert main.is_superuser(SUPERUSER_ID + 1) is False


def test_nobody_is_superuser_when_unconfigured(monkeypatch):
    """An unset SUPERUSER_ID must not make everyone (or user 0) a superuser."""
    monkeypatch.setattr(settings, "SUPERUSER_ID", None)
    assert main.is_superuser(0) is False
    assert main.is_superuser(999) is False


async def test_admin_check_accepts_the_superuser_without_a_db_lookup(monkeypatch):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "is_admin", tripwire)
    assert await main.is_admin_user(SUPERUSER_ID) is True
    assert not tripwire.called, "the superuser short-circuit should skip the query"


async def test_admin_check_consults_the_table_for_everyone_else(is_an_admin):
    assert await main.is_admin_user(12345) is True


async def test_admin_check_rejects_a_stranger(not_an_admin):
    assert await main.is_admin_user(12345) is False


# --- /db: the raw SQL console -----------------------------------------------------


async def test_db_console_refuses_a_non_superuser_and_never_runs_sql(monkeypatch, outsider):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "run_sql", tripwire)

    msg = message("/db DROP TABLE achievements", from_user=outsider)
    await main.db_console_cmd(FakeUpdate(message=msg), FakeContext())

    assert msg.last_reply == "Only the superuser can run raw SQL."
    assert not tripwire.called


async def test_db_console_refuses_an_ordinary_admin(monkeypatch, is_an_admin):
    """/db is superuser-only — being in the admins table is not enough."""
    tripwire = Tripwire()
    monkeypatch.setattr(db, "run_sql", tripwire)

    msg = message("/db SELECT 1", from_user=FakeUser(12345, "Mod"))
    await main.db_console_cmd(FakeUpdate(message=msg), FakeContext())

    assert "Only the superuser" in msg.last_reply
    assert not tripwire.called


async def test_db_console_runs_sql_for_the_superuser(monkeypatch, superuser):
    seen = {}

    async def fake_run_sql(sql):
        seen["sql"] = sql
        return ["n"], [(1,)], "SELECT 1"

    monkeypatch.setattr(db, "run_sql", fake_run_sql)

    msg = message("/db SELECT 1", from_user=superuser)
    await main.db_console_cmd(FakeUpdate(message=msg), FakeContext())

    assert seen["sql"] == "SELECT 1"
    assert "<pre>n\n1</pre>" in msg.last_reply


async def test_db_console_shows_usage_without_a_statement(superuser):
    msg = message("/db", from_user=superuser)
    await main.db_console_cmd(FakeUpdate(message=msg), FakeContext())
    assert "Usage:" in msg.last_reply


async def test_db_console_reports_sql_errors_instead_of_raising(monkeypatch, superuser):
    async def boom(sql):
        raise RuntimeError('relation "nope" does not exist')

    monkeypatch.setattr(db, "run_sql", boom)

    msg = message("/db SELECT * FROM nope", from_user=superuser)
    await main.db_console_cmd(FakeUpdate(message=msg), FakeContext())
    assert "SQL error:" in msg.last_reply
    assert "does not exist" in msg.last_reply


async def test_db_console_refreshes_the_cache_after_a_write(monkeypatch, superuser):
    """A non-SELECT may have changed the achievements table; the cache must reload."""
    reloaded = {}

    async def fake_run_sql(sql):
        return [], [], "UPDATE 1"

    async def fake_load_cache():
        reloaded["yes"] = True

    monkeypatch.setattr(db, "run_sql", fake_run_sql)
    monkeypatch.setattr(db, "load_cache", fake_load_cache)

    msg = message("/db UPDATE achievements SET notes=''", from_user=superuser)
    await main.db_console_cmd(FakeUpdate(message=msg), FakeContext())
    assert reloaded.get("yes"), "the in-memory cache would otherwise go stale"


async def test_db_console_does_not_reload_the_cache_after_a_select(monkeypatch, superuser):
    async def fake_run_sql(sql):
        return ["n"], [(1,)], "SELECT 1"

    tripwire = Tripwire()
    monkeypatch.setattr(db, "run_sql", fake_run_sql)
    monkeypatch.setattr(db, "load_cache", tripwire)

    msg = message("/db SELECT 1", from_user=superuser)
    await main.db_console_cmd(FakeUpdate(message=msg), FakeContext())
    assert not tripwire.called


# --- Admin management (superuser only) -------------------------------------------


async def test_add_admin_refuses_a_non_superuser_and_never_writes(monkeypatch, outsider):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "add_admin", tripwire)

    msg = message("/addadmin 5", from_user=outsider)
    await main.add_admin_cmd(FakeUpdate(message=msg), FakeContext(args=["5"]))

    assert msg.last_reply == "Only the superuser can add admins."
    assert not tripwire.called


async def test_del_admin_refuses_a_non_superuser_and_never_deletes(monkeypatch, outsider):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "remove_admin", tripwire)

    msg = message("/deladmin 5", from_user=outsider)
    await main.del_admin_cmd(FakeUpdate(message=msg), FakeContext(args=["5"]))

    assert msg.last_reply == "Only the superuser can remove admins."
    assert not tripwire.called


async def test_list_admins_refuses_a_non_superuser_and_never_queries(monkeypatch, outsider):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "list_admins", tripwire)

    msg = message("/admins", from_user=outsider)
    await main.list_admins_cmd(FakeUpdate(message=msg), FakeContext())

    assert msg.last_reply == "Only the superuser can list admins."
    assert not tripwire.called


async def test_add_admin_by_id_for_the_superuser(monkeypatch, superuser):
    added = {}

    async def fake_add(user_id, username, first_name, added_by):
        added.update(user_id=user_id, added_by=added_by)

    monkeypatch.setattr(db, "add_admin", fake_add)

    msg = message("/addadmin 555", from_user=superuser)
    await main.add_admin_cmd(FakeUpdate(message=msg), FakeContext(args=["555"]))

    assert added == {"user_id": 555, "added_by": SUPERUSER_ID}


async def test_add_admin_by_reply_prefers_the_replied_user(monkeypatch, superuser):
    added = {}

    async def fake_add(user_id, username, first_name, added_by):
        added.update(user_id=user_id, username=username, first_name=first_name)

    monkeypatch.setattr(db, "add_admin", fake_add)

    replied = message("hi", from_user=FakeUser(777, "Target", username="target"))
    msg = message("/addadmin", from_user=superuser, reply_to_message=replied)
    await main.add_admin_cmd(FakeUpdate(message=msg), FakeContext(args=[]))

    assert added == {"user_id": 777, "username": "target", "first_name": "Target"}


async def test_add_admin_rejects_a_non_numeric_id(monkeypatch, superuser):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "add_admin", tripwire)

    msg = message("/addadmin notanid", from_user=superuser)
    await main.add_admin_cmd(FakeUpdate(message=msg), FakeContext(args=["notanid"]))

    assert "Usage:" in msg.last_reply
    assert not tripwire.called


async def test_del_admin_reports_when_no_row_matched(monkeypatch, superuser):
    async def fake_remove(user_id):
        return False

    monkeypatch.setattr(db, "remove_admin", fake_remove)

    msg = message("/deladmin 5", from_user=superuser)
    await main.del_admin_cmd(FakeUpdate(message=msg), FakeContext(args=["5"]))
    assert msg.last_reply == "That user is not an admin."


# --- Note editing (admin tier) ---------------------------------------------------


async def test_set_note_refuses_a_stranger_and_never_writes(monkeypatch, not_an_admin, achievements, outsider):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "update_notes", tripwire)

    replied = message("Busy Night")
    msg = message("/setnote hello", from_user=outsider, reply_to_message=replied)
    await main.set_note_cmd(FakeUpdate(message=msg), FakeContext())

    assert msg.last_reply == "Only admins can edit notes."
    assert not tripwire.called


async def test_clear_note_refuses_a_stranger_and_never_writes(monkeypatch, not_an_admin, achievements, outsider):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "update_notes", tripwire)

    replied = message("Busy Night")
    msg = message("/clearnote", from_user=outsider, reply_to_message=replied)
    await main.clear_note_cmd(FakeUpdate(message=msg), FakeContext())

    assert msg.last_reply == "Only admins can edit notes."
    assert not tripwire.called


async def test_set_note_writes_for_an_admin(monkeypatch, is_an_admin, achievements):
    written = {}

    async def fake_update(name, notes):
        written.update(name=name, notes=notes)
        return True

    monkeypatch.setattr(db, "update_notes", fake_update)

    replied = message("Busy Night")
    msg = message("/setnote watch out", from_user=FakeUser(12345, "Mod"), reply_to_message=replied)
    await main.set_note_cmd(FakeUpdate(message=msg), FakeContext())

    assert written["name"] == "Busy Night"
    assert written["notes"] == "\N{MEMO} watch out"


async def test_set_note_preserves_the_other_field(monkeypatch, is_an_admin, achievements):
    """Setting the memo must not wipe an existing probability (and vice versa)."""
    written = {}

    async def fake_update(name, notes):
        written["notes"] = notes
        return True

    monkeypatch.setattr(db, "update_notes", fake_update)

    replied = message("Liquid Business")  # already has both fields
    msg = message("/setnote prob 99%", from_user=FakeUser(12345, "Mod"), reply_to_message=replied)
    await main.set_note_cmd(FakeUpdate(message=msg), FakeContext())

    assert written["notes"] == "\N{MEMO} Needs the drunk role.\n\N{GAME DIE} 99%"


async def test_set_note_needs_an_identifiable_achievement(monkeypatch, is_an_admin, achievements):
    tripwire = Tripwire()
    monkeypatch.setattr(db, "update_notes", tripwire)

    replied = message("Not An Achievement Card")
    msg = message("/setnote x", from_user=FakeUser(12345, "Mod"), reply_to_message=replied)
    await main.set_note_cmd(FakeUpdate(message=msg), FakeContext())

    assert "Could not identify the achievement" in msg.last_reply
    assert not tripwire.called


async def test_clear_note_all_clears_both_fields(monkeypatch, is_an_admin, achievements):
    written = {}

    async def fake_update(name, notes):
        written["notes"] = notes
        return True

    monkeypatch.setattr(db, "update_notes", fake_update)

    replied = message("Liquid Business")
    msg = message("/clearnote all", from_user=FakeUser(12345, "Mod"), reply_to_message=replied)
    await main.clear_note_cmd(FakeUpdate(message=msg), FakeContext(args=["all"]))

    assert written["notes"] == ""


async def test_clear_note_prob_leaves_the_memo(monkeypatch, is_an_admin, achievements):
    written = {}

    async def fake_update(name, notes):
        written["notes"] = notes
        return True

    monkeypatch.setattr(db, "update_notes", fake_update)

    replied = message("Liquid Business")
    msg = message("/clearnote prob", from_user=FakeUser(12345, "Mod"), reply_to_message=replied)
    await main.clear_note_cmd(FakeUpdate(message=msg), FakeContext(args=["prob"]))

    assert written["notes"] == "\N{MEMO} Needs the drunk role."
