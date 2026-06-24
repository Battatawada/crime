#!/usr/bin/env bash
CHROME_DIR="/opt/chrome-flowkit"
exec "${CHROME_DIR}/chrome" \
  --no-sandbox \
  --disable-setuid-sandbox \
  --disable-dev-shm-usage \
  "$@"
