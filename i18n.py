"""Message catalogs and locale resolution.

The runtime uses the standard library's `gettext` — no third-party dependency. Babel is a
dev-only tool for extracting, updating and compiling catalogs; nothing here imports it.

Catalogs live in `locales/<lang>/LC_MESSAGES/messages.mo`, compiled from the `.po` files
that translators edit. `.mo` files are build output and are not committed.

Three properties this module guarantees, each with a test:

* **English needs no catalog.** It is the source language, so its "translation" is the msgid
  itself. `translator("en")` returns a passthrough rather than looking for a file.
* **A missing catalog degrades to English.** `gettext` is asked with `fallback=True`, so an
  absent or unreadable `.mo` yields `NullTranslations`. The bot must never fail to answer
  because a catalog did not ship.
* **Unknown locales fall back rather than raise.** Telegram sends an IETF tag from the
  user's app, which may be absent entirely or name a language we do not have.
"""

import gettext
import pathlib

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_LANG = "en"

# Every locale the bot can render. English is the source language and has no catalog.
SUPPORTED_LANGS = ("en", "fa")

DOMAIN = "messages"
LOCALE_DIR = pathlib.Path(__file__).resolve().parent / "locales"

# Loading a .mo per message would be wasteful, so catalogs are cached after first use.
# Keyed by language. Cleared by reset_cache(), which exists for the tests.
_CATALOGS: dict[str, "Translator"] = {}


def normalise_lang(tag):
    """Map an IETF language tag to a supported locale, or None.

    Telegram's `language_code` is a tag like "fa", "fa-IR", "en-US" or "pt-br", and it is
    *optional* — `telegram.User.language_code` defaults to None. Only the primary subtag is
    considered, so a regional variant resolves to its base language:

        "fa" / "fa-IR" / "FA_IR"  -> "fa"
        "en-US"                    -> "en"
        "pt-br"                    -> None   (we have no Portuguese)
        None / "" / "!!"           -> None

    Returning None rather than DEFAULT_LANG keeps "the user asked for something we don't
    have" distinguishable from "the user expressed no preference" — the caller decides.
    """
    if not tag or not isinstance(tag, str):
        return None
    primary = tag.strip().lower().replace("_", "-").split("-")[0]
    return primary if primary in SUPPORTED_LANGS else None


def resolve_lang(*, chat_type, stored_lang=None, telegram_lang=None):
    """Decide which language to answer in.

    Deliberately pure: the caller fetches the stored setting, so this stays testable without
    a database and cannot issue a query per message.

    A group's setting is the group's; a member's own preference must not change what everyone
    else in the room sees, which is why `telegram_lang` is ignored outside private chats.

        group    stored -> default
        private  stored -> the user's Telegram app language -> default
    """
    stored = normalise_lang(stored_lang)
    if stored:
        return stored
    if chat_type == "private":
        detected = normalise_lang(telegram_lang)
        if detected:
            return detected
    return DEFAULT_LANG


class Translator:
    """A resolved language plus its catalog.

    Wraps `gettext` rather than exposing it directly so callers have one object to pass
    around, and so `.lang` is available for logging and for locale-specific rendering
    decisions later.
    """

    __slots__ = ("_catalog", "lang")

    def __init__(self, lang, catalog):
        self.lang = lang
        self._catalog = catalog

    def gettext(self, message):
        """Translate `message`, or return it unchanged if there is no translation."""
        return self._catalog.gettext(message)

    def ngettext(self, singular, plural, n):
        """Pick the plural form for `n` in this locale.

        The catalog's Plural-Forms header decides, which is the point: Persian's `one`
        category covers both 0 and 1, so "0 players" takes the singular there and the plural
        in English. No call site can express that with a conditional.
        """
        return self._catalog.ngettext(singular, plural, n)

    # `_("...")` is the conventional spelling, so make the object callable.
    __call__ = gettext

    def __repr__(self):
        return "Translator({!r})".format(self.lang)


def translator(lang):
    """The Translator for `lang`, cached. Never raises.

    An unsupported language, a missing catalog or an unreadable one all yield English.
    """
    lang = normalise_lang(lang) or DEFAULT_LANG
    cached = _CATALOGS.get(lang)
    if cached is not None:
        return cached

    if lang == DEFAULT_LANG:
        # English is the source language: the msgid *is* the message.
        catalog = gettext.NullTranslations()
    else:
        # fallback=True turns "no .mo found" into a passthrough instead of FileNotFoundError.
        catalog = gettext.translation(DOMAIN, localedir=str(LOCALE_DIR), languages=[lang], fallback=True)
        if isinstance(catalog, gettext.NullTranslations) and type(catalog) is gettext.NullTranslations:
            # Worth a log line: the bot is about to answer in English to someone who asked
            # for another language, and a missing catalog is a packaging mistake, not a
            # user error. Once per language, since the result is cached.
            logger.warning("i18n_catalog_missing", lang=lang, localedir=str(LOCALE_DIR))

    resolved = Translator(lang, catalog)
    _CATALOGS[lang] = resolved
    return resolved


def reset_cache():
    """Drop cached catalogs. For tests that point LOCALE_DIR somewhere else."""
    _CATALOGS.clear()
