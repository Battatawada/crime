#!/usr/bin/env bash
# Start FlowKit agent (expects FlowKit installed at /opt/flowkit)
set -euo pipefail
FLOWKIT_DIR="${FLOWKIT_DIR:-/opt/flowkit}"
export DISPLAY="${DISPLAY:-:99}"

if ! pgrep -x Xvfb >/dev/null 2>&1; then
  Xvfb "$DISPLAY" -screen 0 1280x720x24 &
  sleep 2
fi

cd "$FLOWKIT_DIR"
source venv/bin/activate 2>/dev/null || true
nohup python -m agent.main >> /var/log/flowkit-agent.log 2>&1 &
echo "FlowKit agent starting — check http://127.0.0.1:8100/health"
