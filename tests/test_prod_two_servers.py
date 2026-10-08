"""Two real server processes, one Postgres, one table - what DND_MODE=prod is for.

Server A answers browsers and never runs a DM turn (`DND_RUN_JOBS=0`); server B does
both. So every turn a player asks for on A has to cross to B through the job queue,
and its narration has to cross back to A's browsers through the event bus. The two
players sit on different servers, each watching the other's, the way a load balancer
would seat them.

Skipped without a Postgres - see tests/pgcluster.py.
"""

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servers(pg_url, pg_schema, tmp_path):
    env = {**os.environ, "DND_MODE": "prod", "DATABASE_URL": pg_url,
           "DND_PG_SCHEMA": pg_schema, "SESSION_SECRET": "shared-by-both-servers",
           "APP_PASSWORD": "", "DND_MEDIA": str(tmp_path / "media"),
           "DND_KEYS": str(tmp_path / "keys.json"), "MAX_TURNS_PER_MIN": "1000",
           "PYTHONIOENCODING": "utf-8"}
    procs, urls = [], {}
    for name, jobs in (("A", "0"), ("B", "1")):
        port = _port()
        log = open(tmp_path / f"server-{name}.log", "wb")
        procs.append((subprocess.Popen(
            [sys.executable, "-m", "tests.serve_stub", str(port)], cwd=ROOT,
            env={**env, "DND_SERVER_NAME": name, "DND_RUN_JOBS": jobs},
            stdout=log, stderr=subprocess.STDOUT), log))
        urls[name] = f"http://127.0.0.1:{port}"
    try:
        for name, url in urls.items():
            for _ in range(300):
                try:
                    if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError(f"server {name} did not start:\n"
                                     + (tmp_path / f"server-{name}.log").read_text())
        yield urls
    finally:
        for proc, log in procs:
            proc.terminate()
            proc.wait(10)
            log.close()


class Watcher:
    """One browser's event stream, read on a thread into a queue."""

    def __init__(self, client, url):
        self.events = queue.Queue()
        self._client = client
        self._thread = threading.Thread(target=self._read, args=(url,), daemon=True)
        self._thread.start()

    def _read(self, url):
        try:
            with self._client.stream("GET", url, timeout=None) as stream:
                for line in stream.iter_lines():
                    if line.startswith("data: "):
                        self.events.put(json.loads(line[6:]))
        except httpx.HTTPError:
            pass

    def until(self, test, timeout=20):
        """Events up to and including the first that passes `test`."""
        seen, deadline = [], time.time() + timeout
        while time.time() < deadline:
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            seen.append(event)
            if test(event):
                return seen
        raise AssertionError(f"never arrived; saw {[e.get('kind') for e in seen]}")


def test_a_table_split_across_two_servers_plays_as_one(servers):
    a, b = servers["A"], servers["B"]
    host, guest = httpx.Client(timeout=10), httpx.Client(timeout=10)
    try:
        # the host makes the campaign on A...
        assert host.post(a + "/api/login", json={"password": ""}).status_code == 200
        campaign = host.post(a + "/api/campaigns", json={
            "name": "The Salt Road", "lang": "en",
            "character": {"name": "Vess", "race": "Elf", "class": "Rogue"}}).json()
        cid = campaign["id"]

        # ...and the guest joins it on B: one database, so B knows the code
        assert guest.post(b + "/api/login", json={"password": ""}).status_code == 200
        assert guest.post(b + "/api/campaigns/join",
                          json={"code": campaign["code"]}).json()["id"] == cid
        assert guest.post(b + f"/api/campaigns/{cid}/characters", json={
            "character": {"name": "Bran", "race": "Dwarf", "class": "Cleric"}}
        ).status_code == 200

        # each watches the *other* server; a session signed on one is good on both
        host_sees = Watcher(host, b + f"/api/campaigns/{cid}/stream?since=0")
        guest_sees = Watcher(guest, a + f"/api/campaigns/{cid}/stream?since=0")
        for w in (host_sees, guest_sees):
            w.until(lambda e: e["kind"] == "ready")

        # Begin, asked of A - which runs no turns, so B must
        assert host.post(a + f"/api/campaigns/{cid}/begin").status_code == 200
        on_a = guest_sees.until(lambda e: e["kind"] == "narration")
        assert on_a[-1]["text"].startswith("[B]")
        # the unlogged events crossed too: A's browser saw the DM start thinking and the
        # story stream in, though both happened on B and neither is in the log
        kinds = [e["kind"] for e in on_a]
        assert "thinking" in kinds and "delta" in kinds
        assert host_sees.until(lambda e: e["kind"] == "narration")

        # turns from both sides, alternating servers
        for n, (who, where) in enumerate([(guest, b), (host, a), (guest, a), (host, b)]):
            text = f"move {n}"
            assert who.post(where + f"/api/campaigns/{cid}/act",
                            json={"text": text}).status_code == 200
            for w in (host_sees, guest_sees):
                w.until(lambda e, t=text: e["kind"] == "player" and e["text"] == t)
                w.until(lambda e: e["kind"] == "narration")

        # A second time Begin is refused, whichever server is asked: the claim is shared
        assert guest.post(b + f"/api/campaigns/{cid}/begin").status_code == 400

        # what both browsers were shown is the log, in order, nothing twice
        log = host.get(a + f"/api/campaigns/{cid}/events").json()["events"]
        told = [e["text"] for e in log if e["kind"] == "narration"]
        assert len(told) == 5
        assert all(t.startswith("[B]") for t in told), "A ran a turn, though told not to"
        history = host.get(b + f"/api/campaigns/{cid}").json()
        assert history["started"] and history["last_seq"] == log[-1]["seq"]
    finally:
        host.close()
        guest.close()


def test_prod_mode_will_not_start_without_a_shared_session_key(pg_url, pg_schema, tmp_path):
    env = {**os.environ, "DND_MODE": "prod", "DATABASE_URL": pg_url,
           "DND_PG_SCHEMA": pg_schema, "DND_MEDIA": str(tmp_path / "media")}
    env.pop("SESSION_SECRET", None)
    r = subprocess.run([sys.executable, "-c", "import server"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "SESSION_SECRET" in r.stderr
