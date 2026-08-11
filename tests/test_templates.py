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
