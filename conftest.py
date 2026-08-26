"""Test setup.

This file sits at the repo root for two reasons: pytest puts its directory on
`sys.path`, which is what lets the tests `import server`, and everything here has to
happen *before* `game.store` and `game.media` are imported, because both read their
paths out of the environment at import time.

Nothing in the suite talks to a model. There are no keys on the machine that runs it,
so any test that reached for one would fail rather than cost money.
"""

import os
import shutil
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="ai-dm-test-")

# a database and a media folder of this run's own, so a test never touches the
# campaign.db someone is actually playing
os.environ["DND_DB"] = os.path.join(_SANDBOX, "campaign.db")
os.environ["DND_MEDIA"] = os.path.join(_SANDBOX, "media")
os.environ["DND_KEYS"] = os.path.join(_SANDBOX, "keys.json")
os.environ["SESSION_SECRET"] = "test-secret"


def pytest_unconfigure(config):
    shutil.rmtree(_SANDBOX, ignore_errors=True)
