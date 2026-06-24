#!/usr/bin/env bash
# Upload reference PNGs from local machine to VPS
set -euo pipefail

KEY="${1:-ssh-key-2026-06-24.key}"
HOST="${2:-ubuntu@140.245.245.123}"
REFS_DIR="$(cd "$(dirname "$0")/../config/references" && pwd)"

echo "Uploading references to $HOST:/opt/niche/config/references/"
scp -i "$KEY" "$REFS_DIR"/*.png "$REFS_DIR/manifest.json" "$HOST:/tmp/niche-refs/"
ssh -i "$KEY" "$HOST" "sudo mkdir -p /opt/niche/config/references && sudo cp /tmp/niche-refs/* /opt/niche/config/references/ && sudo chown -R niche:niche /opt/niche/config/references && ls -la /opt/niche/config/references/"
