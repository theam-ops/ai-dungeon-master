"""Fetch a private copy of PostgreSQL, for running the DND_MODE=prod tests on Windows.

    python tools/fetch_postgres.py               download and unpack (330 MB download)
    python tools/fetch_postgres.py --zip FILE    unpack a zip you already have

Unpacks into tools/bin/postgresql-17.6/ (git-ignored), where tests/pgcluster.py looks.
Nothing is installed: no service, no installer, no administrator rights. The tests start
the server themselves, on a free localhost port, in a temporary directory, and stop it
when they finish.

Only the tests need this - playing never does, and a real deployment uses whatever
Postgres it is given in DATABASE_URL. On Linux or macOS install the server from the
package manager instead (`apt install postgresql`, `brew install postgresql`), or point
DND_TEST_DATABASE_URL at any Postgres you already run.

The zip is EnterpriseDB's, which is where postgresql.org sends Windows downloads. Its
checksum is pinned below: a different file is refused, not unpacked.
"""

import argparse
import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "17.6"
URL = f"https://get.enterprisedb.com/postgresql/postgresql-{VERSION}-1-windows-x64-binaries.zip"
SHA256 = "d378882abd001a186735acd6f6ba716bca6ccd192e800412d4fd15ed25376b3e"
TARGET = os.path.join(HERE, "bin", f"postgresql-{VERSION}")
# the server and the tools to start it; not pgAdmin, StackBuilder, the docs or headers,
# which are three quarters of the archive
KEEP = ("pgsql/bin/", "pgsql/lib/", "pgsql/share/")


def download(path):
    print(f"Downloading {URL}\n  (about 330 MB - this takes a while)")
    digest = hashlib.sha256()
    req = urllib.request.Request(URL, headers={"User-Agent": "ai-dungeon-master/fetch_postgres"})
    with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
        while chunk := r.read(1 << 20):
            digest.update(chunk)
            f.write(chunk)
    return digest.hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def unpack(zip_path, target=TARGET):
    with zipfile.ZipFile(zip_path) as z:
        members = [m for m in z.namelist() if m.startswith(KEEP)]
        for m in members:
            # a zip names its own paths; make sure none of them climbs out of the target
            dest = os.path.realpath(os.path.join(target, m))
            if not dest.startswith(os.path.realpath(target) + os.sep):
                raise SystemExit(f"refusing a path outside the target: {m}")
        z.extractall(target, members)
    return os.path.join(target, "pgsql", "bin")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zip", help="a postgresql-*-windows-x64-binaries.zip already on disk")
    args = ap.parse_args()
    if os.name != "nt" and not args.zip:
        raise SystemExit("These are Windows binaries. On Linux or macOS install postgresql "
                         "from your package manager; the tests find it there.")
    if os.path.exists(os.path.join(TARGET, "pgsql", "bin", "initdb.exe")):
        print(f"Already there: {TARGET}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = args.zip or os.path.join(tmp, "postgresql.zip")
        got = sha256(zip_path) if args.zip else download(zip_path)
        if got != SHA256:
            raise SystemExit(f"That is not the expected file (sha256 {got}). Not unpacked.")
        shutil.rmtree(TARGET, ignore_errors=True)
        print(f"Unpacked: {unpack(zip_path)}")


if __name__ == "__main__":
    main()
