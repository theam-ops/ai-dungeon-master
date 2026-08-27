"""Put the running game on the public web through a Cloudflare quick tunnel.

The point of doing it this way rather than deploying: the DM stays on *this* machine,
so a Claude Pro subscription keeps running the table. A deployed copy cannot do that -
the subscription backend drives the Claude Code installed here, and there is none in a
datacentre - so a host needs an API key instead.

The trade is that it only lives while this window is open, and a quick tunnel's URL is
different every time. Both are fine for "we're playing tonight"; neither is fine for a
permanent address, which is what deploying is for.

Used by `launch.py --share`; also runnable on its own against an already-running game:

    python tools/share.py --port 8000
"""

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(HERE, "bin")

# cloudflared publishes a static binary per platform; no install, no account, no signup
DOWNLOADS = {
    ("Windows", "AMD64"): ("cloudflared.exe",
                           "https://github.com/cloudflare/cloudflared/releases/latest/"
                           "download/cloudflared-windows-amd64.exe"),
    ("Darwin", "arm64"):  ("cloudflared",
                           "https://github.com/cloudflare/cloudflared/releases/latest/"
                           "download/cloudflared-darwin-arm64.tgz"),
    ("Linux", "x86_64"):  ("cloudflared",
                           "https://github.com/cloudflare/cloudflared/releases/latest/"
                           "download/cloudflared-linux-amd64"),
}

URL_RE = re.compile(rb"https://[-a-z0-9]+\.trycloudflare\.com")


def find_cloudflared():
    """An installed cloudflared, or one we fetched earlier."""
    found = shutil.which("cloudflared")
    if found:
        return found
    for name in ("cloudflared.exe", "cloudflared"):
        local = os.path.join(BIN_DIR, name)
        if os.path.exists(local):
            return local
    return ""


def fetch_cloudflared(say=print):
    """Download the one binary this needs, next to the game rather than system-wide."""
    key = (platform.system(), platform.machine())
    entry = DOWNLOADS.get(key)
    if not entry:
        say(f"  No cloudflared build listed for {key[0]} {key[1]}.")
        say("  Install it yourself from https://developers.cloudflare.com/cloudflare-one/"
            "connections/connect-networks/downloads/ and run this again.")
        return ""
    name, url = entry
    if name.endswith(".tgz") or url.endswith(".tgz"):
        say("  Automatic download only covers Windows and Linux; on macOS run:")
        say("      brew install cloudflared")
        return ""

    os.makedirs(BIN_DIR, exist_ok=True)
    target = os.path.join(BIN_DIR, name)
    say("  Fetching cloudflared (about 20MB, once)…")
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(target, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:
        say(f"  That download failed: {type(e).__name__}: {e}")
        return ""
    os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC)
    return target


def start(port, say=print, on_url=None):
    """Open a quick tunnel to `port`. Returns the process, or None.

    cloudflared writes the URL to stderr among its own logging, so the output is read
    in a thread and only the interesting line is passed on.
    """
    exe = find_cloudflared() or fetch_cloudflared(say)
    if not exe:
        return None

    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def watch():
        seen = False
        for line in proc.stderr:
            if seen:
                continue
            found = URL_RE.search(line)
            if found:
                seen = True
                url = found.group(0).decode()
                say("")
                say(f"  Public link:  {url}")
                say("  Anyone with that link and the password can join.")
                say("  It dies when this window closes, and is different next time.")
                say("")
                if on_url:
                    on_url(url)
    threading.Thread(target=watch, daemon=True).start()
    return proc


def main():
    ap = argparse.ArgumentParser(description="Share a running game on the public web.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = ap.parse_args()

    proc = start(args.port)
    if not proc:
        return 1
    print(f"  Tunnelling http://localhost:{args.port} — Ctrl+C to stop.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
