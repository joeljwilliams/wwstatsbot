"""The handler registration table.

Every other test in this suite calls a handler directly, which means they all pass
whether or not that handler is actually *reachable from Telegram*. The registration
table in `build_application()` is the single place a command can silently cease to
exist: delete a line and the handler keeps passing its own tests while no user can
invoke it any more.

That gap matters most during the coming split of `main.py`. Moving handlers into
`handlers/` modules means rewriting this table, and a dropped registration produces no
error at import, no failing test, and no log line — just a command that stops working.

The callback-pattern tests are the sharpest tool here: rather than restating the
patterns, they feed real `callback_data` produced by the renderers through the
registered handlers, so renaming a prefix on either side fails.
"""

import re

from telegram.ext import CallbackQueryHandler, CommandHandler, InlineQueryHandler

import main
import settings
from handlers import achievements as achv_handlers
from handlers import admin, errors, gamesession, inline, misc, search, stats

# Commands advertised in Telegram's "/" menu. Sourced from main so the test tracks the
# real list rather than a copy that could drift out of step with it.
ADVERTISED = [command.command for command in main.PUBLIC_COMMANDS]

# Registered but deliberately absent from the menu: /schall and /allinfo are kept for
# muscle memory (/sch and /info reroute to them), the rest are privileged.
#
# The stand-in session's five (gs, role, rm, love, gsend) are the strongest case of all for
# staying out of the menu: they are the *real* achievement manager's command words, and
# advertising them would put this bot forward as the thing to type them at while the
# incumbent is running the game. They are registered because Telegram hands every slash
# command to every bot in the group either way — the handlers stay silent unless this chat
# has a session.
UNADVERTISED = [
    "schall",
    "allinfo",
    "roll",
    "addadmin",
    "deladmin",
    "admins",
    "setnote",
    "clearnote",
    "db",
    "gs",
    "role",
    "rm",
    "love",
    "gsend",
    "dead",
    "ad",
    "steal",
    "la",
    "alt",
]

# Aliases that must keep working alongside their primary verb.
ALIASES = ["sch", "achv", "getachv"]


def application():
    """A built Application. Never contacts Telegram — build() only constructs objects."""
    return main.build_application()


def all_handlers(app):
    return [handler for group in app.handlers.values() for handler in group]


def command_map(app):
    """command name -> handler callback, across every registered CommandHandler."""
    mapping = {}
    for handler in all_handlers(app):
        if isinstance(handler, CommandHandler):
            for command in handler.commands:
                mapping[command] = handler.callback
    return mapping


# --- Every command is reachable --------------------------------------------------


def test_every_advertised_command_is_registered():
    """A command in the "/" menu that no handler serves is a dead entry in the UI."""
    registered = command_map(application())
    missing = [command for command in ADVERTISED if command not in registered]
    assert not missing, "advertised in PUBLIC_COMMANDS but not registered: {}".format(missing)


def test_every_unadvertised_command_is_registered():
    registered = command_map(application())
    missing = [command for command in UNADVERTISED if command not in registered]
    assert not missing, "not registered: {}".format(missing)


def test_aliases_are_registered():
    registered = command_map(application())
    missing = [alias for alias in ALIASES if alias not in registered]
    assert not missing, "alias no longer registered: {}".format(missing)


def test_aliases_share_a_callback_with_their_primary_verb():
    """/sch must do what /search does, not merely exist."""
    registered = command_map(application())
    assert registered["sch"] is registered["search"]
    assert registered["achv"] is registered["achievements"]
    assert registered["getachv"] is registered["info"]


def test_commands_are_wired_to_the_expected_callbacks():
    """Guards against two handlers being transposed during a move — the failure mode
    that leaves every command registered but some of them doing the wrong thing."""
    registered = command_map(application())
    expected = {
        "start": misc.startme,
        "stats": stats.display_stats,
        "kills": stats.display_kills,
        "killedby": stats.display_killed_by,
        "deaths": stats.display_deaths,
        "search": search.display_search,
        "schall": search.display_search_all,
        "about": misc.display_about,
        "version": misc.display_version,
        "achievements": achv_handlers.display_achv,
        "info": achv_handlers.display_achv_info,
        "allinfo": achv_handlers.all_info_cmd,
        "addadmin": admin.add_admin_cmd,
        "deladmin": admin.del_admin_cmd,
        "admins": admin.list_admins_cmd,
        "setnote": admin.set_note_cmd,
        "clearnote": admin.clear_note_cmd,
        "db": admin.db_console_cmd,
    }
    for command, callback in expected.items():
        assert registered[command] is callback, "/{} is wired to {}".format(command, registered[command])


def test_no_command_is_registered_twice_to_different_handlers():
    """Two handlers claiming one command means the second is unreachable — PTB
    dispatches to the first match in the group."""
    seen = {}
    for handler in all_handlers(application()):
        if isinstance(handler, CommandHandler):
            for command in handler.commands:
                assert command not in seen, "/{} is registered more than once".format(command)
                seen[command] = handler


def test_the_menu_lists_no_privileged_command():
    """Admin/superuser commands are intentionally kept out of the "/" list."""
    privileged = {"addadmin", "deladmin", "admins", "setnote", "clearnote", "db"}
    assert not (set(ADVERTISED) & privileged)


# --- Callbacks: real callback_data must reach a handler ---------------------------


def callback_handlers(app):
    return [handler for handler in all_handlers(app) if isinstance(handler, CallbackQueryHandler)]


def handler_for_callback_data(app, data):
    """The handler whose pattern matches `data`, or None. Mirrors PTB's own check."""
    for handler in callback_handlers(app):
        pattern = handler.pattern
        if pattern is not None and re.match(pattern, data):
            return handler.callback
    return None


def test_schall_toggle_button_reaches_its_handler():
    """Feeds the renderer's actual output through the registered patterns, so a prefix
    renamed on either side fails rather than silently producing a dead button."""
    payload = {"name": "X", "desc": "d", "missing": [(1, "A")], "have": [(2, "B")], "unresolved": []}
    _, keyboard = search._render_schall(payload, "TOK", show_have=False)
    data = keyboard.inline_keyboard[0][0].callback_data

    assert handler_for_callback_data(application(), data) is search.schall_callback


def test_the_standin_stop_button_reaches_its_handler():
    """Built by render_state, so the real callback_data is what gets fed through.

    A dead Stop button is a particularly bad shape of broken: the session keeps running,
    keeps capturing /role in the chat, and the only visible way to end it does nothing.
    """
    session_data = {
        "order": [],
        "players": {},
        "unresolved": [],
        "state_message_id": None,
    }
    _, keyboard = gamesession.render_state(session_data)
    data = keyboard.inline_keyboard[0][0].callback_data

    assert handler_for_callback_data(application(), data) is gamesession.stop_callback


def test_every_allinfo_button_reaches_its_handler():
    app = application()
    _, keyboard = achv_handlers._render_allinfo_page(["A", "B", "C"], 0, "TOK")
    for row in keyboard.inline_keyboard:
        for button in row:
            assert handler_for_callback_data(app, button.callback_data) is achv_handlers.all_info_callback, (
                "no handler matches {!r}".format(button.callback_data)
            )


def test_the_group_pm_handoff_button_reaches_its_handler():
    """Built inline in all_info_cmd rather than by a renderer, so it needs its own check."""
    data = "{}{}:{}".format(achv_handlers._ALLINFO_PREFIX, achv_handlers._ALLINFO_PM, "TOK")
    assert handler_for_callback_data(application(), data) is achv_handlers.all_info_callback


def test_the_legacy_bare_token_callback_still_reaches_a_handler():
    """Buttons posted before the pager existed carried just a token, and their messages
    may still be sitting in a group."""
    data = "{}{}".format(achv_handlers._ALLINFO_PREFIX, "TOK")
    assert handler_for_callback_data(application(), data) is achv_handlers.all_info_callback


def test_callback_patterns_do_not_overlap():
    """Overlapping patterns make dispatch order significant, which is fragile."""
    app = application()
    for data, expected in [
        ("allinfo:p:TOK:0", achv_handlers.all_info_callback),
        ("schall:TOK:have", search.schall_callback),
    ]:
        matches = [
            handler.callback
            for handler in callback_handlers(app)
            if handler.pattern is not None and re.match(handler.pattern, data)
        ]
        assert matches == [expected], "{!r} matched {}".format(data, matches)


# --- The remaining wiring --------------------------------------------------------


def test_exactly_one_inline_query_handler_is_registered():
    handlers = [h for h in all_handlers(application()) if isinstance(h, InlineQueryHandler)]
    assert len(handlers) == 1
    assert handlers[0].callback is inline.inline_query


def test_an_error_handler_is_registered():
    """Without it, PTB logs to its own logger and the log-group report never happens."""
    app = application()
    assert errors.error_handler in app.error_handlers


def test_lifecycle_hooks_are_attached():
    """_post_init brings up the database and flips readiness; losing it means the bot
    reports ready to Railway while holding no connection pool."""
    app = application()
    assert app.post_init is main._post_init
    assert app.post_shutdown is main._post_shutdown


def test_persistence_is_disabled_without_redis(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", None)
    assert application().persistence is None


def test_persistence_is_enabled_with_redis(monkeypatch):
    """The durable path: /allinfo and /sch buttons survive a restart."""
    import redis_persistence
    from redis_persistence import RedisPersistence

    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    # from_url is lazy, but stub it anyway so nothing can attempt a connection.
    monkeypatch.setattr(redis_persistence.redis.Redis, "from_url", staticmethod(lambda url, **kw: None))
    monkeypatch.setattr(redis_persistence.aioredis.Redis, "from_url", staticmethod(lambda url, **kw: None))

    assert isinstance(application().persistence, RedisPersistence)


def test_the_handler_count_is_accounted_for():
    """A blunt backstop: if a handler is added or removed without updating this file,
    say so, so the lists above can't quietly fall behind the table."""
    expected = len(ADVERTISED) + len(UNADVERTISED) + len(ALIASES)
    commands = len(command_map(application()))
    assert commands == expected, (
        "{} commands registered, expected {} — update ADVERTISED / UNADVERTISED / "
        "ALIASES in this file to match build_application()".format(commands, expected)
    )
