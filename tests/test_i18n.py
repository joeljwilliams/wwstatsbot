"""Catalog loading and locale resolution.

Three failure modes matter more than the happy path, because each one degrades quietly:

* a **missing catalog** must answer in English, not raise — a bot that fails to reply
  because a `.mo` did not ship is worse than one that replies in the wrong language;
* an **unknown or absent** `language_code` must fall back — Telegram's tag is optional and
  can name any language;
* a group member's own preference must **not** leak into a group's language, or one person
  would change what everyone in the room sees.

The catalogs here are compiled on the fly from tiny `.po` files, so these tests exercise the
real `gettext` loading path rather than a stub, and do not depend on whatever translations
`locales/` happens to contain.
"""

import pathlib
import subprocess
import sys

import pytest

import i18n

FA_PO = """
msgid ""
msgstr ""
"Language: fa\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Plural-Forms: nplurals=2; plural=(n==0 || n==1) ? 0 : 1;\\n"

msgid "This list has expired. Please run /sch again."
msgstr "این فهرست منقضی شده است."

msgid "one player"
msgid_plural "{count} players"
msgstr[0] "یک بازیکن"
msgstr[1] "{count} بازیکن"
"""


@pytest.fixture
def catalogs(tmp_path, monkeypatch):
    """A compiled fa catalog in a temp dir, with i18n pointed at it."""
    po = tmp_path / "fa" / "LC_MESSAGES" / "messages.po"
    po.parent.mkdir(parents=True)
    po.write_text(FA_PO, encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "compile", "-d", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    i18n.reset_cache()
    yield tmp_path
    i18n.reset_cache()


@pytest.fixture
def empty_locales(tmp_path, monkeypatch):
    """A locale dir with no catalogs at all — the packaging-mistake case."""
    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    i18n.reset_cache()
    yield tmp_path
    i18n.reset_cache()


# --- normalise_lang ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("fa", "fa"),
        ("FA", "fa"),
        ("fa-IR", "fa"),  # Telegram sends regional variants
        ("fa_IR", "fa"),  # and underscores appear in the wild
        ("  fa  ", "fa"),
        ("en", "en"),
        ("en-US", "en"),
        ("pt-br", None),  # a real Telegram value we have no catalog for
        ("de", None),
        ("", None),
        (None, None),  # language_code is optional on telegram.User
        ("!!", None),
        (123, None),  # defensive: never trust the wire
    ],
)
def test_normalise_lang(tag, expected):
    assert i18n.normalise_lang(tag) == expected


# --- resolve_lang ----------------------------------------------------------------


def test_group_uses_its_stored_setting():
    assert i18n.resolve_lang(chat_type="group", stored_lang="fa") == "fa"


def test_group_ignores_the_askers_telegram_language():
    """One member's app language must not change what the whole room sees."""
    assert i18n.resolve_lang(chat_type="group", telegram_lang="fa") == "en"


def test_group_with_no_setting_uses_the_default():
    assert i18n.resolve_lang(chat_type="group") == i18n.DEFAULT_LANG


def test_private_prefers_the_users_own_setting_over_telegram():
    """An explicit choice beats detection — the user has already told us."""
    assert i18n.resolve_lang(chat_type="private", stored_lang="en", telegram_lang="fa") == "en"


def test_private_falls_back_to_the_telegram_language():
    assert i18n.resolve_lang(chat_type="private", telegram_lang="fa") == "fa"


def test_private_with_an_unsupported_telegram_language_uses_the_default():
    """A Brazilian-Portuguese client gets English, not a crash."""
    assert i18n.resolve_lang(chat_type="private", telegram_lang="pt-br") == i18n.DEFAULT_LANG


def test_private_with_no_language_code_at_all():
    assert i18n.resolve_lang(chat_type="private", telegram_lang=None) == i18n.DEFAULT_LANG


def test_an_unsupported_stored_setting_is_ignored():
    """A row left behind by a locale we later dropped must not break the chat."""
    assert i18n.resolve_lang(chat_type="group", stored_lang="de") == i18n.DEFAULT_LANG


# --- translator ------------------------------------------------------------------


def test_english_needs_no_catalog(empty_locales):
    """English is the source language: the msgid is the message."""
    tr = i18n.translator("en")
    assert tr.lang == "en"
    assert tr.gettext("This list has expired. Please run /sch again.") == (
        "This list has expired. Please run /sch again."
    )


def test_a_real_catalog_translates(catalogs):
    tr = i18n.translator("fa")
    assert tr.gettext("This list has expired. Please run /sch again.") == "این فهرست منقضی شده است."


def test_an_untranslated_string_passes_through(catalogs):
    """A msgid the translator has not filled in yet must render in English."""
    assert i18n.translator("fa").gettext("Only admins can edit notes.") == "Only admins can edit notes."


def test_a_missing_catalog_degrades_to_english(empty_locales):
    """The packaging mistake. Answering in English beats not answering."""
    tr = i18n.translator("fa")
    assert tr.lang == "fa"
    assert tr.gettext("Only admins can edit notes.") == "Only admins can edit notes."


def test_a_missing_catalog_is_logged(empty_locales):
    """It is a deployment fault, not a user error, so it must be visible in the logs.

    Uses structlog's own capture rather than pytest's `caplog`: configure_logging() runs in
    main(), never in tests, so structlog falls back to its default PrintLogger and never
    reaches stdlib logging — caplog sees nothing.
    """
    from structlog.testing import capture_logs

    with capture_logs() as entries:
        i18n.translator("fa")
    assert [e for e in entries if e["event"] == "i18n_catalog_missing" and e["lang"] == "fa"]


def test_an_unsupported_language_yields_english(empty_locales):
    tr = i18n.translator("de")
    assert tr.lang == "en"


def test_none_yields_english(empty_locales):
    assert i18n.translator(None).lang == "en"


def test_catalogs_are_cached(catalogs):
    """One .mo read, not one per message."""
    assert i18n.translator("fa") is i18n.translator("fa")


def test_the_translator_is_callable(catalogs):
    """`_("...")` is the conventional spelling."""
    tr = i18n.translator("fa")
    assert tr("This list has expired. Please run /sch again.") == tr.gettext(
        "This list has expired. Please run /sch again."
    )


def test_repr_names_the_language(catalogs):
    assert repr(i18n.translator("fa")) == "Translator('fa')"


# --- Plurals: the reason gettext is here at all -----------------------------------


def test_english_plurals(empty_locales):
    tr = i18n.translator("en")
    assert tr.ngettext("one player", "{count} players", 1) == "one player"
    assert tr.ngettext("one player", "{count} players", 2) == "{count} players"


def test_english_treats_zero_as_plural(empty_locales):
    assert i18n.translator("en").ngettext("one player", "{count} players", 0) == "{count} players"


def test_persian_treats_zero_as_singular(catalogs):
    """The case a conditional cannot express.

    CLDR gives Persian `one: i = 0 or n = 1`, so 0 takes the *singular* form — the opposite
    of English. `plural="" if n == 1 else "s"` has no way to say this, which is why the three
    hand-rolled sites become ngettext calls.
    """
    tr = i18n.translator("fa")
    assert tr.ngettext("one player", "{count} players", 0) == "یک بازیکن"
    assert tr.ngettext("one player", "{count} players", 1) == "یک بازیکن"
    assert tr.ngettext("one player", "{count} players", 2) == "{count} بازیکن"


# --- Configuration sanity ---------------------------------------------------------


def test_default_language_is_supported():
    assert i18n.DEFAULT_LANG in i18n.SUPPORTED_LANGS


def test_the_locale_dir_is_inside_the_repo():
    """It is resolved from __file__, so a working directory change cannot break catalogs."""
    assert i18n.LOCALE_DIR.name == "locales"
    assert i18n.LOCALE_DIR.parent == pathlib.Path(i18n.__file__).resolve().parent
