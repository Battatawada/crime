#!/usr/bin/env bash
# Install Chrome for Testing ~2 months old (FlowKit v1.1.0 compatible).
# Does NOT modify FlowKit. Keeps system google-chrome; adds /opt/chrome-flowkit.
set -euo pipefail

CHROME_DIR="/opt/chrome-flowkit"
# Default: Chrome 131 — FlowKit v1.1.0 tested era; override with CHROME_VERSION=
CHROME_VERSION="${CHROME_VERSION:-131.0.6778.264}"

mkdir -p "$CHROME_DIR"
cd /tmp
ZIP="chrome-linux64.zip"
URL="https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip"

echo "Downloading Chrome for Testing ${CHROME_VERSION}..."
curl -fL "$URL" -o "$ZIP"
rm -rf "$CHROME_DIR"/*
unzip -q -o "$ZIP" -d "$CHROME_DIR"
mv "$CHROME_DIR/chrome-linux64"/* "$CHROME_DIR/"
rmdir "$CHROME_DIR/chrome-linux64" 2>/dev/null || true
chmod +x "$CHROME_DIR/chrome"

cat > /usr/local/bin/chrome-flowkit <<'EOF'
#!/usr/bin/env bash
# Oracle/Ubuntu 24 AppArmor blocks Chrome sandbox — safe for isolated VPS + VNC use.
CHROME_DIR="/opt/chrome-flowkit"
exec "${CHROME_DIR}/chrome" \
  --no-sandbox \
  --disable-setuid-sandbox \
  --disable-dev-shm-usage \
  "$@"
EOF
chmod +x /usr/local/bin/chrome-flowkit

echo "Installed: chrome-flowkit ($("${CHROME_DIR}/chrome" --version 2>/dev/null || echo ${CHROME_VERSION}))"
echo "Use in VNC: chrome-flowkit --user-data-dir=\$HOME/.config/chrome-flowkit"
