"""Run the real server, in its own process, with a stand-in DM.

    python -m tests.serve_stub PORT

For tests that need more than one server process - `DND_MODE=prod` behind a load
balancer - where the in-process test harness cannot reach. Configured entirely by
environment, like a deployment. The DM answers every turn without calling any model,
and signs its narration with `DND_SERVER_NAME`, so a test can see which process ran it.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game import providers                                   # noqa: E402
from tests.stub_backend import StubBackend, install_stub     # noqa: E402


class Narrator(StubBackend):
    async def stream(self, system_blocks, messages, tools, images=None):
        if not self.script:
            self.reply(f"[{os.environ.get('DND_SERVER_NAME', '?')}] The tide turns.")
        async for chunk in super().stream(system_blocks, messages, tools, images):
            yield chunk


if __name__ == "__main__":
    install_stub(providers, Narrator())
    import uvicorn

    import server
    uvicorn.run(server.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
