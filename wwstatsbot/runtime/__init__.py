"""Process plumbing: configuration, logging, the health server, the webhook, persistence.

None of it knows anything about the game. `settings.py` is the one place a setting is
resolved, and consumers must reach it through the module (``settings.SUPERUSER_ID``) —
see its docstring for why a `from` import breaks both tests and agreement between modules.
"""
