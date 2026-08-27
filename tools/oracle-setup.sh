#!/usr/bin/env bash
# Set up the game on a fresh Oracle Cloud Always Free VM (Ampere A1, Ubuntu).
#
# Why this host, out of everything: it is the only genuinely free option with a disk
# that survives AND enough of a machine to run Claude Code - so a Pro subscription can
# keep being the DM on a server that is always on. Every hosted alternative forces the
# game onto an API key.
#
# The repo is private, so the VM cannot clone it without credentials and a
# curl-pipe-bash one-liner would just 404. Get the code onto the box first, either way:
#
#   from your PC:   scp -r ai-dungeon-master-main ubuntu@<vm-ip>:~/ai-dungeon-master
#   or on the VM:   gh auth login && gh repo clone theam-ops/ai-dungeon-master
#
# then:             bash ~/ai-dungeon-master/tools/oracle-setup.sh
#
# It installs Python and the game, Claude Code, and cloudflared; then registers a
# systemd service so the table comes back by itself after a reboot. It does NOT sign
# you into anything - the last step is yours, and it tells you what to run.

set -euo pipefail

REPO="${REPO:-https://github.com/theam-ops/ai-dungeon-master.git}"
APP_DIR="${APP_DIR:-$HOME/ai-dungeon-master}"
SERVICE="ai-dungeon-master"
PORT="${PORT:-8000}"

say() { printf '\n\033[1;33m==\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as your normal user (the 'ubuntu' one), not root.
   Claude Code signs in per-user, and the service has to run as whoever signed in."

# --------------------------------------------------------------------------- #
say "Installing what the game needs"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git curl

# --------------------------------------------------------------------------- #
say "Finding the game"
# Run from inside a copy of the repo, which is the normal case: it is private, so it
# arrived here by scp or by an authenticated clone rather than by this script fetching it.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$HERE/server.py" ]; then
  APP_DIR="$HERE"
  echo "   using $APP_DIR"
elif [ -f "$APP_DIR/server.py" ]; then
  echo "   using $APP_DIR"
elif git clone --depth 1 "$REPO" "$APP_DIR" 2>/dev/null; then
  echo "   cloned into $APP_DIR"
else
  die "Could not find the game, and could not clone it either - the repo is private.
   Copy it to this machine first, then run this script from inside it:
       scp -r ai-dungeon-master-main ubuntu@<this-vm>:~/ai-dungeon-master
       bash ~/ai-dungeon-master/tools/oracle-setup.sh"
fi

# A venv rather than --break-system-packages: Ubuntu's Python is managed by apt, and
# pip installing over it is how you end up with an unbootable box.
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# --------------------------------------------------------------------------- #
say "Installing Claude Code"
# Ampere A1 is arm64 and the installer handles that; the app finds it on PATH.
if ! command -v claude >/dev/null 2>&1; then
  curl -fsSL https://claude.ai/install.sh | bash
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v claude >/dev/null 2>&1 \
  && echo "   claude: $(command -v claude)" \
  || echo "   claude: not on PATH yet - open a new shell, or add ~/.local/bin to PATH"

# --------------------------------------------------------------------------- #
say "Installing cloudflared"
# A tunnel rather than opening a port. Oracle puts a security list in front of the VM
# *and* iptables on it, and getting a certificate is a third job; a tunnel makes all
# three unnecessary because nothing inbound is ever opened.
if ! command -v cloudflared >/dev/null 2>&1; then
  ARCH="$(dpkg --print-architecture)"
  curl -fsSL -o /tmp/cloudflared.deb \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
  sudo dpkg -i /tmp/cloudflared.deb >/dev/null
  rm -f /tmp/cloudflared.deb
fi

# --------------------------------------------------------------------------- #
say "Writing the password file"
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
# Read by the service. Keep this file off the internet - it is your table's password.
APP_PASSWORD=change-me-before-you-share-the-link
# Pinned so a restart does not sign everyone out.
SESSION_SECRET=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
# Only needed if you want a fallback for when the subscription is rate limited.
# GEMINI_API_KEY=
EOF
  chmod 600 "$ENV_FILE"
  echo "   wrote $ENV_FILE"
else
  echo "   $ENV_FILE already exists, left alone"
fi

# --------------------------------------------------------------------------- #
say "Registering the service"
sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<EOF
[Unit]
Description=AI Dungeon Master
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
# Claude Code lives in ~/.local/bin and the app looks for it on PATH
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE"

# --------------------------------------------------------------------------- #
cat <<EOF

  Installed. Two things left, both yours to do:

  1. Sign Claude Code in, as this user:

         claude auth login

     It prints a link. Open it on your laptop, approve, paste the code back.
     Then check it took:  claude auth status

  2. Set the table's password, then restart:

         nano $APP_DIR/.env          # change APP_PASSWORD
         sudo systemctl restart $SERVICE

  Then put it on the web. For a link that changes each time:

         cloudflared tunnel --url http://localhost:$PORT

  For a permanent address you need a domain on Cloudflare DNS, then:

         cloudflared tunnel login
         cloudflared tunnel create dnd
         cloudflared tunnel route dns dnd dnd.yourdomain.com
         sudo cloudflared service install    # survives reboot

  Useful afterwards:
      systemctl status $SERVICE
      journalctl -u $SERVICE -f

  One warning worth reading. Oracle reclaims Always Free instances that look idle -
  under 20% CPU, network AND memory at the 95th percentile across 7 days. A game
  server used a few evenings a week is squarely inside that. Upgrading the account to
  Pay As You Go exempts it, and Always Free resources stay free after you do; that is
  the fix. People also run CPU-wasters to fake the numbers - this script deliberately
  does not ship one, because it burns power to lie to your host.

EOF
