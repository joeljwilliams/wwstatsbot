"""Guards the template/call-site drift class.

Every user-visible string lives in `templates.py` and is `.format()`-ed from one of the
application modules. Nothing connects the two, so renaming a field on one side fails at
runtime, inside a handler, in production.

`main.py` is being split across several modules, which multiplies the number of places a
template is referenced — the references are already spread over `main.py`, `builders.py`
and `wwstats.py`. These tests are cheap insurance: they don't verify wording, they verify
that every template is *reachable and formattable*, and that no call site references a
template that no longer exists.
"""

import ast
import pathlib
import string

import templates as t

REPO = pathlib.Path(__file__).resolve().parent.parent


def sources():
    """Every application module that could reference a template.

    Discovered rather than listed, because `main.py` is being split into modules and a
    hardcoded list goes stale on every slice. The two failure directions differ, and both
    are avoided by globbing:

    * `test_no_template_is_unreferenced` **fails loudly** when references move to a module
      not on the list — the templates look orphaned. Noisy but safe.
    * `test_every_referenced_template_exists` **degrades silently**: fewer sources scanned
      means fewer call sites validated, and it keeps passing while checking less.
    """
    skip = {"conftest.py", "config.py", "configEXAMPLE.py"}
    found = [path for path in sorted(REPO.glob("*.py")) if path.name not in skip]
    found += sorted(REPO.glob("handlers/*.py"))
    return found


def template_names():
    """Every public template constant in templates.py (UPPER_CASE strings)."""
    return [name for name in dir(t) if name.isupper() and isinstance(getattr(t, name), str)]


def fields_of(template):
    """The named {fields} a template expects, ignoring literal text."""
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def test_there_are_templates_to_check():
    assert len(template_names()) > 30


def test_every_template_formats_with_its_own_fields():
    """A template whose fields can't be satisfied is a latent crash."""
    for name in template_names():
        template = getattr(t, name)
        kwargs = {field: "x" for field in fields_of(template)}
        try:
            template.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            raise AssertionError("templates.{} cannot be formatted: {!r}".format(name, exc)) from exc


def test_no_template_uses_positional_placeholders():
    """Named fields only — call sites pass **kwargs, so `{}` would raise."""
    for name in template_names():
        for _, field, _, _ in string.Formatter().parse(getattr(t, name)):
            if field is not None:
                assert field != "", (
                    "templates.{} uses a positional {{}} placeholder; call sites format with keywords".format(name)
                )


def referenced_template_names():
    """Every `t.NAME` / `templates.NAME` attribute read across the app's sources."""
    referenced = set()
    for path in sources():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in ("t", "templates")
                and node.attr.isupper()
            ):
                referenced.add(node.attr)
    return referenced


def test_every_referenced_template_exists():
    """Catches a template renamed or deleted while a call site still reads it."""
    missing = sorted(referenced_template_names() - set(template_names()))
    assert not missing, "referenced but absent from templates.py: {}".format(missing)


def test_no_template_is_unreferenced():
    """Catches dead prose left behind after a handler is rewritten.

    Not a style nit: an orphaned template is a strong hint that a code path was
    dropped by accident during a refactor.
    """
    orphans = sorted(set(template_names()) - referenced_template_names())
    assert not orphans, "templates.py defines these but nothing reads them: {}".format(orphans)


def test_html_templates_have_balanced_simple_tags():
    """A dropped closing tag makes Telegram reject the whole message with a 400."""
    for name in template_names():
        template = getattr(t, name)
        for tag in ("b", "i", "code", "pre", "a"):
            opens = template.count("<{}>".format(tag)) + template.count("<{} ".format(tag))
            closes = template.count("</{}>".format(tag))
            assert opens == closes, "templates.{} has {} <{}> open vs {} close".format(name, opens, tag, closes)


# --- Extraction must be able to see every string ----------------------------------

# babel.cfg scans templates.py alone, so prose that creeps back into a handler would be
# silently untranslatable — extraction would never see it and no test would fail.
#
# Only the modules that actually render replies are scanned. Deliberately excluded:
#   achvlist.py   achievement seed data. Names and descriptions come from the game and stay
#                 English; every lookup matches on them (see db.py).
#   db.py         SQL.
#   health.py     an HTTP header.
#   settings.py   startup diagnostics for whoever deploys the bot. They are emitted before
#                 any locale could be known — there is no user yet — and go to the console,
#                 not to Telegram.
_SCANNED = ["builders.py", "wwstats.py", "main.py"]

# Strings inside the scanned modules that are not prose and must stay literal.
_NOT_PROSE = {
    # Matched against Telegram's own error text, which is always English. Translating these
    # would break the swallow-list and every long-poll timeout would report to the log group.
    "timed out",
    "not modified",
    "query_id_invalid",
    # Postgres' own spelling of the value in the /db console. A translated console must not
    # misrepresent what the database returned.
    "NULL",
    # What a player *types*, not what the bot says: the long spellings of the Beholder's
    # claim about the Seer (handlers/gamesession.py). Translating a command argument would
    # stop the English one working, which is the one everybody in the group actually uses.
    "beholder no seer",
    "beholdernoseer",
    "bh no seer",
    "no seer",
}


def scanned_sources():
    paths = [REPO / name for name in _SCANNED]
    paths += sorted((REPO / "handlers").glob("*.py"))
    return [p for p in paths if p.exists()]


def prose_literals(path):
    """String constants in `path` that look like user-visible prose.

    Heuristic and deliberately loose: long enough to be a sentence fragment, containing a
    space, not a docstring, and not a compiled regex pattern. A false positive costs one
    allowlist entry; a false negative is a string no translator will ever see.
    """
    source = path.read_text()
    tree = ast.parse(source)

    skip = set(_NOT_PROSE)
    for node in [tree] + [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]:
        doc = ast.get_docstring(node, clean=False)
        if doc:
            skip.add(doc)
    # Anything handed to re.compile is a pattern, not prose.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "compile"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            skip.add(node.args[0].value)

    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and len(node.value) >= 12
        and " " in node.value
        and "\n" not in node.value[:1]
        and node.value not in skip
    }


def test_no_prose_outside_templates():
    """All user-visible prose lives in templates.py, so extraction can find all of it.

    This is what makes scanning one file in babel.cfg correct. Without it, a message added
    straight into a handler would work in English forever and never reach a translator.
    """
    offenders = {p.name: sorted(found) for p in scanned_sources() if (found := prose_literals(p))}
    assert not offenders, "prose found outside templates.py: {}".format(offenders)


def test_every_template_is_extractable():
    """Every constant in templates.py appears in the .pot, and nothing extra does.

    Catches a constant added without its N_() marker: it would still work in English and
    still pass every other test, while being invisible to translators.
    """
    from babel.messages.pofile import read_po

    pot = REPO / "locales" / "messages.pot"
    assert pot.exists(), "run: uv run pybabel extract -F babel.cfg -o locales/messages.pot ."
    with pot.open(encoding="utf-8") as handle:
        catalog = read_po(handle)

    extracted = {message.id for message in catalog if message.id}
    defined = {getattr(t, name) for name in template_names()}
    assert not defined - extracted, "in templates.py but not extracted (missing N_()?): {}".format(
        sorted(s[:60] for s in defined - extracted)
    )
    assert not extracted - defined, "extracted but gone from templates.py (stale .pot — re-extract): {}".format(
        sorted(s[:60] for s in extracted - defined)
    )
