"""The game's own rules, with no Telegram in them.

`session.py` is the stand-in game session as pure state, `roles.py` the role registry, and
`feasibility.py` answers which achievements a revealed composition can still produce. What
makes the rules of a game testable without a bot is that nothing here renders or sends
anything — handlers/gamesession.py is where that happens.
"""
