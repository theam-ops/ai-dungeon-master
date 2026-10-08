"""A throwaway Postgres for the tests of `DND_MODE=prod`.

Where the server comes from, in order:

- `DND_TEST_DATABASE_URL` - any Postgres you already run. The tests make a schema of
  their own per test and drop it after, so a shared server is fine.
- `DND_PG_BIN`, or `tools/bin/postgresql-*/pgsql/bin` (what `tools/fetch_postgres.py`
  unpacks), or `pg_ctl` on the PATH - binaries to start a private cluster from, in a
  temporary directory, on a free port, for this test run only.

With none of those, the Postgres tests are skipped: they need a database, and playing
the game never does.
"""

import glob
import os
import shutil
import socket
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = ".exe" if os.name == "nt" else ""


def binaries():
    """The directory holding initdb and pg_ctl, or None."""
    candidates = [os.environ.get("DND_PG_BIN", "")]
    candidates += sorted(glob.glob(os.path.join(ROOT, "tools", "bin", "postgresql-*",
                                                "pgsql", "bin")), reverse=True)
    on_path = shutil.which("pg_ctl")
    if on_path:
        candidates.append(os.path.dirname(on_path))
    # Debian and Ubuntu keep the server binaries off the PATH
    candidates += sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True)
    for path in candidates:
        if path and os.path.exists(os.path.join(path, "initdb" + EXE)):
            return path
    return None


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Cluster:
    """initdb into a temporary directory, start on a free port, stop and delete after."""

    def __init__(self, bindir):
        self.bindir = bindir
        self.dir = tempfile.mkdtemp(prefix="dnd-pg-")
        self.data = os.path.join(self.dir, "data")
        self.port = _free_port()
        self.url = f"postgresql://postgres@127.0.0.1:{self.port}/postgres"

    def _run(self, tool, *args):
        # stdout to a file, never a pipe: the server inherits it and outlives pg_ctl,
        # so a pipe would never close and this call would never return
        with open(os.path.join(self.dir, f"{tool}.log"), "ab") as log:
            subprocess.run([os.path.join(self.bindir, tool + EXE), *args], check=True,
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                           timeout=120)

    def start(self):
        self._run("initdb", "-D", self.data, "-U", "postgres", "-A", "trust", "-E", "UTF8",
                  "--no-locale", "--no-sync")
        # fsync off: this cluster is thrown away, and on Windows syncing is most of the
        # time a test run would otherwise spend
        self._run("pg_ctl", "-D", self.data, "-l", os.path.join(self.dir, "server.log"),
                  "-w", "-o", f"-p {self.port} -c listen_addresses=127.0.0.1 -c fsync=off"
                              " -c synchronous_commit=off -c max_connections=300", "start")
        return self

    def stop(self):
        try:
            self._run("pg_ctl", "-D", self.data, "-m", "immediate", "-w", "stop")
        except (subprocess.SubprocessError, OSError):
            pass
        shutil.rmtree(self.dir, ignore_errors=True)
