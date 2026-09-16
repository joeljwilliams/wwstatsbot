"""The data layer: the stats API client, Postgres, and the record kept of every lookup.

`api.py` is the only module that talks to tgwerewolf.com and `playerdata.py` is the only
module allowed to call its fetchers — a rule tests/test_playerdata.py checks rather than
trusts. `achvlist.py` and `rulelist.py` are seed sources for two tables, not runtime data:
a fresh database is populated from them and a running bot reads the tables.
"""
