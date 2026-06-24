#!/usr/bin/env bash
# Oracle VPS bootstrap — run as root on Ubuntu 22.04/24.04
set -euo pipefail

NICHE_ROOT="${NICHE_ROOT:-/opt/niche}"
NICHE_REPO_URL="${NICHE_REPO_URL:-https://github.com/Battatawada/youtube.git}"
SWAP_GB="${SWAP_GB:-4}"
NICHE_USER="${NICHE_USER:-niche}"

echo "==> Swap (${SWAP_GB}G)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l "${SWAP_GB}G" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-pip python3-venv ffmpeg xvfb curl git ufw

echo "==> Service user"
if ! id "$NICHE_USER" &>/dev/null; then
  useradd -r -m -d /var/lib/niche -s /bin/bash "$NICHE_USER"
fi

echo "==> Clone/update Niche repo"
mkdir -p "$(dirname "$NICHE_ROOT")"
if [[ -d "$NICHE_ROOT/.git" ]]; then
  git -C "$NICHE_ROOT" pull --ff-only
elif [[ ! -d "$NICHE_ROOT" ]] || [[ -z "$(ls -A "$NICHE_ROOT" 2>/dev/null)" ]]; then
  rm -rf "$NICHE_ROOT"
  git clone "$NICHE_REPO_URL" "$NICHE_ROOT"
else
  echo "ERROR: $NICHE_ROOT exists but is not a git repo (often niche user home dir)."
  echo "Fix: sudo rm -rf $NICHE_ROOT && re-run this script"
  exit 1
fi
chown -R "$NICHE_USER:$NICHE_USER" "$NICHE_ROOT"

echo "==> Python venv (VPS worker)"
sudo -u "$NICHE_USER" python3 -m venv "$NICHE_ROOT/.venv"
sudo -u "$NICHE_USER" "$NICHE_ROOT/.venv/bin/pip" install -U pip
sudo -u "$NICHE_USER" "$NICHE_ROOT/.venv/bin/pip" install -r "$NICHE_ROOT/vps/requirements.txt"

echo "==> Firewall (port 8765 for GitHub Actions)"
ufw allow OpenSSH
ufw allow 8765/tcp
ufw --force enable

echo "==> FlowKit (manual steps after this script)"
cat <<'EOF'
1. git clone https://github.com/crisng95/flowkit.git /opt/flowkit && cd /opt/flowkit && ./setup.sh
2. Load extension/ in Chrome (Developer Mode) — needs GUI or VNC for first login
3. Sign in at https://labs.google/fx/tools/flow
4. Place reference PNGs in /opt/niche/config/references/character_A.png
5. Copy /opt/niche/deploy/niche-image-worker.service to /etc/systemd/system/
6. Create /opt/niche/.env (see .env.example) with WEBHOOK_SECRET
7. systemctl daemon-reload && systemctl enable --now niche-image-worker
8. curl http://140.245.245.123:8765/health
EOF

echo "Done base setup at $NICHE_ROOT"
