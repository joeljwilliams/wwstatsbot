"""Encoding for achievement notes.

Notes are stored in a single TEXT column but hold up to two sub-fields, each on its own
line prefixed by a marker emoji (added automatically on write):

    📝 <memo>        the main note (may span multiple lines)
    🎲 <probability> the odds of attaining the achievement

parse_notes/serialize_notes are the only encoders; storage stays schema-free and
human-readable in the /db console. Fields are emitted in the _NOTE_MARKERS order.

Every read path normalises through parse then serialize, so display is canonical
(markers present and ordered) even for legacy notes written before the scheme existed or
rows edited by hand through the /db console.

This module is deliberately dependency-free — pure string handling, no Telegram, no
database — so it can be read and tested in isolation.
"""

NOTE_MEMO = "\N{MEMO}"
NOTE_DIE = "\N{GAME DIE}"
_NOTE_MARKERS = [("memo", NOTE_MEMO), ("prob", NOTE_DIE)]
_PROB_KEYWORDS = {"prob", "probability"}


def parse_notes(raw):
    """Split a stored notes blob into {'memo': ..., 'prob': ...}.

    A line starting with a field marker begins that field; any text before the
    first marker is treated as the memo (back-compat with old plain notes).
    """
    marker_to_key = {marker: key for key, marker in _NOTE_MARKERS}
    buf = {key: [] for key, _ in _NOTE_MARKERS}
    current = "memo"
    for line in (raw or "").splitlines():
        stripped = line.lstrip()
        marker = next((m for m in marker_to_key if stripped.startswith(m)), None)
        if marker:
            current = marker_to_key[marker]
            buf[current].append(stripped[len(marker) :].lstrip())
        else:
            buf[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in buf.items()}


def serialize_notes(fields):
    """Render a {'memo', 'prob'} dict back to the marker-prefixed storage form,
    omitting empty fields. Returns '' when both are empty."""
    parts = []
    for key, marker in _NOTE_MARKERS:
        value = fields.get(key, "").strip()
        if value:
            parts.append("{} {}".format(marker, value))
    return "\n".join(parts)


def split_note_field(arg):
    """Return (field_key, text) for a /setnote argument. A leading 'prob' /
    'probability' keyword selects the probability field; otherwise it's the memo
    and the whole argument is the text (line breaks preserved)."""
    tokens = arg.split(None, 1)
    if tokens and tokens[0].lower() in _PROB_KEYWORDS:
        return "prob", (tokens[1].strip() if len(tokens) > 1 else "")
    return "memo", arg


def is_prob_keyword(word):
    """True if `word` names the probability field (used by /clearnote's argument)."""
    return word in _PROB_KEYWORDS
