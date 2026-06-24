#!/usr/bin/env bash
# Chrome 131 for FlowKit — Oracle VPS / TigerVNC safe launcher.
CHROME_DIR="/opt/chrome-flowkit"
export DISPLAY="${DISPLAY:-:1}"
export GNOME_KEYRING_CONTROL=""
export SSH_AUTH_SOCK=""
exec "${CHROME_DIR}/chrome" \
  --no-sandbox \
  --disable-setuid-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --disable-gpu-compositing \
  --use-gl=swiftshader \
  --password-store=basic \
  --disable-breakpad \
  --no-first-run \
  --no-default-browser-check \
  "$@"
