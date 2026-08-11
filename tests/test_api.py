"""The stats API client, and the guarantee that no test reaches the real one.

`api.py` holds one shared `httpx.AsyncClient` and six thin fetchers over
tgwerewolf.com. The fetchers are barely worth asserting on individually; what *is*
worth pinning is the wiring around them, because two properties fail silently:

* the shared client must be resolved **through the module** (`api.client`), so the
  test suite's MockTransport swap is actually seen. `from api import client` would
  bind the original object and the suite would make real requests while passing;
* every fetcher must send `pid` and `json=true`, since the API returns HTML rather
  than JSON without the latter — a mistake that surfaces as a JSON decode error far
  from its cause.
"""

import httpx
import pytest

import api

PID = 4242


async def test_every_fetcher_targets_its_own_endpoint(stats_api):
    """Guards against two fetchers being transposed — each would still return valid
    JSON, so nothing else in the suite would notice."""
    await api.get_stats(PID)
    await api.get_kills(PID)
    await api.get_killed_by(PID)
    await api.get_deaths(PID)
    await api.get_achievements(PID)

    assert [r.url.path for r in stats_api.requests] == [
        "/Stats/PlayerStats/",
        "/Stats/PlayerKills/",
        "/Stats/PlayerKilledBy/",
        "/Stats/PlayerDeaths/",
        "/Stats/PlayerAchievements/",
    ]


async def test_every_request_asks_for_json(stats_api):
    """Without json=true the API serves HTML and .json() blows up downstream."""
    await api.get_stats(PID)
    await api.get_kills(PID)
    await api.get_achievements(PID)
    assert all(r.url.params.get("json") == "true" for r in stats_api.requests)


async def test_every_request_carries_the_player_id(stats_api):
    await api.get_stats(PID)
    assert stats_api.requests[0].url.params.get("pid") == str(PID)


async def test_achievement_count_returns_a_count_not_the_list(stats_api):
    """The one fetcher that isn't a passthrough."""
    assert await api.get_achievement_count(PID) == 2


async def test_fetchers_return_the_decoded_payload(stats_api):
    stats = await api.get_stats(PID)
    assert stats["gamesPlayed"] == 100
    assert [k["name"] for k in await api.get_kills(PID)] == ["Bob", "Al & Sons"]


async def test_all_fetchers_share_one_client(stats_api):
    """One pooled client, not one per call — so all requests land on the fake."""
    await api.get_stats(PID)
    await api.get_kills(PID)
    assert len(stats_api.requests) == 2


# --- The network guard ------------------------------------------------------------


async def test_a_request_without_the_stats_api_fixture_is_refused():
    """The backstop for `stats_api`'s patch target going stale.

    Without `stats_api`, the autouse `forbid_real_network` fixture is in force, so any
    request raises instead of leaving the machine. If this ever stops raising, the suite
    can silently start talking to tgwerewolf.com.
    """
    with pytest.raises(AssertionError, match="real HTTP request attempted"):
        await api.get_stats(PID)


async def test_the_guard_names_the_url_it_blocked():
    """So the failure explains itself rather than just asserting False."""
    with pytest.raises(AssertionError, match="PlayerKills"):
        await api.get_kills(PID)


def test_the_client_is_an_async_client():
    assert isinstance(api.client, httpx.AsyncClient)


def test_base_url_is_the_public_stats_endpoint():
    assert api.BASE == "https://www.tgwerewolf.com/Stats"
