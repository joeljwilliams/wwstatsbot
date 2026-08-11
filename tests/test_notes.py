"""The notes encoding: two sub-fields packed into one TEXT column.

Notes are stored marker-prefixed (📝 memo, 🎲 probability) so the column stays
schema-free and readable in the /db console. parse_notes/serialize_notes are the only
encoders, and every read path normalises through both — so round-tripping is the
property that actually matters here.
"""

import notes

MEMO = "\N{MEMO}"
DIE = "\N{GAME DIE}"


def test_parse_empty_and_none():
    assert notes.parse_notes("") == {"memo": "", "prob": ""}
    assert notes.parse_notes(None) == {"memo": "", "prob": ""}


def test_parse_legacy_unmarked_text_becomes_the_memo():
    """Notes predating the marker scheme have no marker; they are memos."""
    assert notes.parse_notes("plain old note") == {"memo": "plain old note", "prob": ""}


def test_parse_both_fields():
    raw = "{} the memo\n{} 12%".format(MEMO, DIE)
    assert notes.parse_notes(raw) == {"memo": "the memo", "prob": "12%"}


def test_parse_multiline_memo_keeps_its_line_breaks():
    raw = "{} line one\nline two\n{} 3%".format(MEMO, DIE)
    assert notes.parse_notes(raw) == {"memo": "line one\nline two", "prob": "3%"}


def test_parse_text_before_the_first_marker_is_the_memo():
    """Back-compat: a legacy note that later had a probability appended."""
    raw = "legacy text\n{} 50%".format(DIE)
    assert notes.parse_notes(raw) == {"memo": "legacy text", "prob": "50%"}


def test_parse_tolerates_leading_whitespace_before_a_marker():
    raw = "   {} indented".format(MEMO)
    assert notes.parse_notes(raw)["memo"] == "indented"


def test_serialize_omits_empty_fields():
    assert notes.serialize_notes({"memo": "", "prob": ""}) == ""
    assert notes.serialize_notes({"memo": "m", "prob": ""}) == "{} m".format(MEMO)
    assert notes.serialize_notes({"memo": "", "prob": "9%"}) == "{} 9%".format(DIE)


def test_serialize_always_emits_memo_before_prob():
    """Field order is fixed by _NOTE_MARKERS, not by dict insertion order."""
    out = notes.serialize_notes({"prob": "9%", "memo": "m"})
    assert out == "{} m\n{} 9%".format(MEMO, DIE)


def test_serialize_handles_a_missing_key():
    assert notes.serialize_notes({}) == ""
    assert notes.serialize_notes({"memo": "m"}) == "{} m".format(MEMO)


def test_roundtrip_is_stable():
    """serialize(parse(x)) must be idempotent — every display path relies on it."""
    for raw in [
        "",
        "plain legacy",
        "{} m".format(MEMO),
        "{} m\n{} 5%".format(MEMO, DIE),
        "{} multi\nline\n{} 5%".format(MEMO, DIE),
        "legacy\n{} 5%".format(DIE),
    ]:
        once = notes.serialize_notes(notes.parse_notes(raw))
        twice = notes.serialize_notes(notes.parse_notes(once))
        assert once == twice, raw


def test_roundtrip_normalises_legacy_into_marked_form():
    assert notes.serialize_notes(notes.parse_notes("bare")) == "{} bare".format(MEMO)


# --- /setnote argument parsing ---------------------------------------------------


def test_split_note_field_defaults_to_memo():
    assert notes.split_note_field("some note") == ("memo", "some note")


def test_split_note_field_recognises_prob_keywords():
    assert notes.split_note_field("prob 15%") == ("prob", "15%")
    assert notes.split_note_field("probability 15%") == ("prob", "15%")
    assert notes.split_note_field("PROB 15%") == ("prob", "15%")


def test_split_note_field_prob_with_no_text():
    assert notes.split_note_field("prob") == ("prob", "")


def test_split_note_field_preserves_memo_line_breaks():
    """/setnote reads the raw message text so multi-line notes survive."""
    assert notes.split_note_field("line one\nline two") == ("memo", "line one\nline two")


def test_split_note_field_does_not_treat_a_word_starting_with_prob_as_the_keyword():
    assert notes.split_note_field("probably not a keyword")[0] == "memo"
