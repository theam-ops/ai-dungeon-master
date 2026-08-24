"""Start the game and open it in a browser. The thing the desktop icon points at.

Doing this by hand means installing the requirements, remembering the uvicorn command,
and typing the address in. This does all three, and tells you plainly if something is
missing rather than dumping a traceback.

    python launch.py                 start, and open a browser
    python launch.py --no-browser    start, but don't open one
    python launch.py --port 9000     use a particular port

Close the window to stop the game.
"""

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS = os.path.join(HERE, "requirements.txt")
DEFAULT_PORT = int(os.environ.get("PORT", 8000))

BANNER = r"""
   ___    _____   ____                                  __  ___         __
  / _ |  /  _/ | / / /_ _____  ___ ____ ___  ___    /  |/  /__ ____ / /____ ____
 / __ | _/ / | |/ / / // / _ \/ _ `/ -_) _ \/ _ \  / /|_/ / _ `(_-</ __/ -_) __/
/_/ |_|/___/ |___/_/\_,_/_//_/\_, /\__/\___/_//_/ /_/  /_/\_,_/___/\__/\__/_/
                             /___/
"""


def say(msg):
    print(msg, flush=True)


def missing_packages():
    """Which requirements aren't importable. Import names differ from package names."""
    checks = {"fastapi": "fastapi", "uvicorn": "uvicorn", "anthropic": "anthropic",
              "itsdangerous": "itsdangerous", "PIL": "Pillow",
              "multipart": "python-multipart"}
    missing = []
    for module, package in checks.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing


def install_requirements():
    say("First run: installing what the game needs. This takes a minute.\n")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS])
    if result.returncode != 0:
        say("\nThat install failed. Try running this by hand to see why:\n"
            f"    {sys.executable} -m pip install -r requirements.txt")
        return False
    say("")
    return True


def free_port(preferred):
    """The preferred port, or the next free one - a stale server shouldn't block start."""
    for port in range(preferred, preferred + 20):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def open_when_ready(url, port, timeout=40):
    """Wait for the server to answer before opening a tab, so nobody sees a dead page."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.3)


def dm_status():
    """A word on whether anything can actually run the table, before the browser opens."""
    try:
        sys.path.insert(0, HERE)
        from game import providers
    except Exception as e:
        return f"  (couldn't check which AI is available: {type(e).__name__}: {e})"
    usable = [b for b in providers.BACKENDS if b.available()]
    if not usable:
        return ("  No AI is set up yet, so the DM can't narrate. The game will show you\n"
                "  where to get a free key. Quickest options: a free Gemini key from\n"
                "  aistudio.google.com/apikey, or Claude Code if you have Claude Pro.")
    return f"  Dungeon Master: {usable[0].label}"


def main():
    parser = argparse.ArgumentParser(description="Start AI Dungeon Master.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host", default="0.0.0.0",
                        help="0.0.0.0 lets other devices on your network join")
    args = parser.parse_args()

    os.chdir(HERE)
    say(BANNER)

    if missing_packages() and not install_requirements():
        input("\nPress Enter to close.")
        return 1
    if missing_packages():
        say("Something is still missing after installing. Run it by hand to see why:\n"
            f"    {sys.executable} -m pip install -r requirements.txt")
        input("\nPress Enter to close.")
        return 1

    port = free_port(args.port)
    url = f"http://localhost:{port}"
    if port != args.port:
        say(f"  Port {args.port} was busy, using {port} instead.")
    say(dm_status())
    say(f"\n  Playing at:  {url}")
    say("  On your phone: see 'Playing from your phone' in README.md")
    say("\n  Close this window to stop the game.\n")

    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, port), daemon=True).start()

    import uvicorn
    try:
        uvicorn.run("server:app", host=args.host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    say("\n  Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
