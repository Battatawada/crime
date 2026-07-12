#!/usr/bin/env bash
# Criminally Drawn Chrome on VNC :2 — FlowKit preloaded (no Load unpacked needed)
export DISPLAY="${DISPLAY:-:2}"
export GNOME_KEYRING_CONTROL=""
export SSH_AUTH_SOCK=""
PROFILE="${HOME}/.config/google-chrome-crime"
mkdir -p "$PROFILE"

if [[ "${CHROME_NETWORK_MODE:-direct}" == "proxy" ]]; then
  : "${CHROME_PROXY:=socks5://127.0.0.1:10808}"
  export CHROME_PROXY
else
  unset CHROME_PROXY || true
fi

CHROME_BIN="/usr/bin/google-chrome-stable"
[[ -x "$CHROME_BIN" ]] || CHROME_BIN="/usr/bin/google-chrome"

PROXY_ARGS=()
if [[ -n "${CHROME_PROXY:-}" ]]; then
  PROXY_ARGS+=(--proxy-server="${CHROME_PROXY}")
  PROXY_ARGS+=(--proxy-bypass-list="127.0.0.1;localhost;<-loopback>")
fi

exec "$CHROME_BIN" \
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
  --disable-background-networking \
  --renderer-process-limit=2 \
  --js-flags="--max-old-space-size=192" \
  --disk-cache-size=1 \
  --media-cache-size=1 \
  --enable-extensions \
  --load-extension=/opt/flowkit/extension \
  --user-data-dir="$PROFILE" \
  "${PROXY_ARGS[@]}" \
  "$@"
