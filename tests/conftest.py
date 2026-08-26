"""Test fixtures: a throwaway database, a throwaway media folder, and a fake DM.

Every path the app writes to is redirected into a tmp directory *before* `server` is
imported, because `store.DB_PATH`, `media.MEDIA_DIR` and `providers.KEY_FILE` are all
resolved at module import time. Importing the app first and pointing it somewhere else
afterwards would quietly use the real `campaign.db`.
"""

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Must happen before the first `import server`.
_SANDBOX = tempfile.mkdtemp(prefix="dnd-tests-")
os.environ["DND_DB"] = os.path.join(_SANDBOX, "campaign.db")
os.environ["DND_MEDIA"] = os.path.join(_SANDBOX, "media")
os.environ["DND_KEYS"] = os.path.join(_SANDBOX, "keys.json")
os.environ["SESSION_SECRET"] = "test-secret-not-a-real-one"
os.environ["APP_PASSWORD"] = ""
os.environ["MAX_TURNS_PER_MIN"] = "1000"      # the rate limit gets its own test
# Keep the real providers out of the picture entirely: no key is set here, and a stray
# one in the developer's environment would otherwise make `failover_order` reach out.
for _name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY",
              "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(_name, None)


@pytest.fixture(scope="session")
def sandbox():
    return _SANDBOX


@pytest.fixture
def app_client(monkeypatch):
    """A TestClient with a fresh database and the stub DM registered.

    Yields (client, stub). The stub is `tests.stub_backend.StubBackend`; queue replies
    on it with `stub.reply(...)` before making a request that runs a turn.
    """
    from starlette.testclient import TestClient

    import server
    from game import providers, store

    from .stub_backend import StubBackend, install_stub

    _reset_db(store)

    stub = install_stub(providers)
    monkeypatch.setattr(providers, "DEFAULT_ID", stub.id, raising=False)

    with TestClient(server.app) as client:
        client.post("/api/login", json={"password": ""})
        yield client, stub

    _reset_db(store)


def _reset_db(store):
    """Drop every row. Cheaper and more reliable than a new file per test - the module
    holds one connection and reopening it would need `store._conn` surgery anyway."""
    conn = store.db()
    for table in ("events", "media", "lore", "characters", "campaigns"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
