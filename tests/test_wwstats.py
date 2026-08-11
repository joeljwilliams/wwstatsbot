"""The /achievements report — the one Markdown output path.

Achievements are bucketed three ways (missing-but-attainable, not-via-playing,
inactive) and each bucket is chunked at 30 entries per Telegram message, with the
MISSING total line repeated on every chunk so a message never loses its context.
"""

from conftest import ACHIEVEMENTS

import db
import wwstats


def test_chunks_splits_evenly():
    assert list(wwstats.chunks([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]


def test_chunks_keeps_a_short_final_chunk():
    assert list(wwstats.chunks([1, 2, 3], 2)) == [[1, 2], [3]]


def test_chunks_of_an_empty_list():
    assert list(wwstats.chunks([], 30)) == []


def test_section_produces_one_message_for_under_thirty_items():
    items = [{"name": "A", "desc": "d"}]
    msgs = wwstats._section(items, "MAIN", "HEADER")
    assert len(msgs) == 1
    assert msgs[0] == "MAINHEADER`- A`\n>>> _d_\n"


def test_section_splits_at_thirty_and_repeats_the_context_lines():
    """Each chunk must carry the total line and section header, not just the first."""
    items = [{"name": "A{}".format(i), "desc": "d"} for i in range(31)]
    msgs = wwstats._section(items, "MAIN", "HEADER")
    assert len(msgs) == 2
    assert all(m.startswith("MAINHEADER") for m in msgs)
    assert msgs[0].count("`- A") == 30
    assert msgs[1].count("`- A") == 1


def test_section_of_nothing_produces_nothing():
    assert wwstats._section([], "MAIN", "HEADER") == []


async def test_check_buckets_achievements_and_counts_them(monkeypatch, stats_api):
    """ACHIEVEMENTS_JSON grants 'Welcome to Hell' and 'Busy Night' of the 6 fixtures."""
    monkeypatch.setattr(db, "get_achievements", lambda: ACHIEVEMENTS)
    msgs = await wwstats.check(7, stats_api_client(stats_api))

    attained = msgs[0]
    assert "*ATTAINED (2/6):*" in attained
    assert "- Welcome to Hell" in attained
    assert "- Busy Night" in attained

    body = "".join(msgs[1:])
    # 6 total, 2 attained -> 4 missing, split across the three buckets:
    # O HAI DER! + Liquid Business attainable, Here's Johnny! not-via-playing,
    # Explorer inactive.
    assert "*MISSING (4/6):*" in body
    assert "*MISSING AND ATTAINABLE VIA PLAYING (2/6):*" in body
    assert "*NOT DIRECTLY ATTAINABLE VIA PLAYING (1/6):*" in body
    assert "*INACTIVE (1/6):*" in body
    assert "Here's Johnny!" in body
    assert "Explorer" in body


async def test_check_puts_each_achievement_in_exactly_one_bucket(monkeypatch, stats_api):
    """An inactive achievement must not also be listed as attainable."""
    monkeypatch.setattr(db, "get_achievements", lambda: ACHIEVEMENTS)
    msgs = await wwstats.check(7, stats_api_client(stats_api))
    body = "".join(msgs[1:])
    assert body.count("`- Explorer`") == 1
    assert body.count("`- Here's Johnny!`") == 1


def stats_api_client(api):
    """wwstats.check takes the httpx client as an argument rather than importing it."""
    import httpx

    return httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
