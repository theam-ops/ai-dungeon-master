"""What the game does, independent of HTTP and of what it is stored in.

A service is handed an `Adapters` (see `game/adapters/`) and reaches storage, the event
bus, locks and background work through it alone. It never imports `game.store`, an
adapter module, sqlite3, redis, or `server` - `tests/test_boundaries.py` holds it to
that, because the day one does, `DND_MODE=prod` quietly stops working.
"""
