#!/usr/bin/env bash
# Start xray on VPS so Chrome (Flow) exits via residential/VPN IP.
# Usage:
#   sudo mkdir -p /opt/niche/xray
#   sudo nano /opt/niche/xray/config.json   # paste your xray client config
#   sudo bash /opt/niche/scripts/vps-xray-proxy.sh start
#   echo 'CHROME_PROXY=socks5://127.0.0.1:10808' | sudo tee -a /opt/niche/.env
# Then in VNC: pkill -f google-chrome; start-chrome-flowkit
set -euo pipefail

XRAY_CONFIG="${XRAY_CONFIG:-/opt/niche/xray/config.json}"
XRAY_BIN="${XRAY_BIN:-/usr/local/bin/xray}"
PID_FILE="/run/xray-niche.pid"
LOG_FILE="/var/log/xray-niche.log"

socks_port() {
  python3 - <<'PY' "$XRAY_CONFIG" 2>/dev/null || echo "10808"
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    raise SystemExit
data = json.loads(p.read_text(encoding="utf-8"))
for inbound in data.get("inbounds", []):
    if inbound.get("protocol") in {"socks", "http"}:
        port = inbound.get("port")
        if port:
            print(port)
            raise SystemExit
print("10808")
PY
}

install_hint() {
  echo "xray not found. Install (as root):"
  echo "  bash -c \"\$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install"
  echo "Then copy your client config to ${XRAY_CONFIG}"
}

cmd_start() {
  if [[ ! -f "$XRAY_CONFIG" ]]; then
    echo "Missing ${XRAY_CONFIG} — paste your xray client JSON there first."
    exit 1
  fi
  if [[ ! -x "$XRAY_BIN" ]]; then
    install_hint
    exit 1
  fi
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "xray already running (pid $(cat "$PID_FILE"))"
    exit 0
  fi
  nohup "$XRAY_BIN" run -c "$XRAY_CONFIG" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  sleep 1
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "xray failed to start — tail ${LOG_FILE}"
    exit 1
  fi
  PORT=$(socks_port)
  echo "xray running (pid $(cat "$PID_FILE"))"
  echo "Suggested /opt/niche/.env line:"
  echo "  CHROME_PROXY=socks5://127.0.0.1:${PORT}"
}

cmd_stop() {
  if [[ -f "$PID_FILE" ]]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  pkill -f "xray run -c ${XRAY_CONFIG}" 2>/dev/null || true
  echo "xray stopped"
}

cmd_status() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "xray: running (pid $(cat "$PID_FILE"))"
  else
    echo "xray: not running"
  fi
  PORT=$(socks_port)
  echo "config: ${XRAY_CONFIG}"
  echo "CHROME_PROXY=socks5://127.0.0.1:${PORT}"
  if [[ -n "${CHROME_PROXY:-}" ]]; then
    echo "CHROME_PROXY env: ${CHROME_PROXY}"
  fi
}

case "${1:-status}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *)
    echo "Usage: $0 {start|stop|status}"
    exit 1
    ;;
esac
