#!/usr/bin/env bash
# Patch FlowKit extension for Chrome 131+ (DNR modifyHeaders + Origin header parse fix).
set -euo pipefail

EXT_DIR="${1:-/opt/flowkit/extension}"

python3 - "$EXT_DIR" <<'PY'
import json
import sys
from pathlib import Path

ext = Path(sys.argv[1])
manifest_path = ext / "manifest.json"
rules_path = ext / "rules.json"

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
perms = list(manifest.get("permissions", []))
if "declarativeNetRequestWithHostAccess" not in perms:
    perms.append("declarativeNetRequestWithHostAccess")
manifest["permissions"] = perms
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

rules = json.loads(rules_path.read_text(encoding="utf-8"))
for rule in rules:
    action = rule.get("action", {})
    if action.get("type") == "modifyHeaders":
        action["requestHeaders"] = [
            h for h in action.get("requestHeaders", [])
            if h.get("header", "").lower() != "origin"
        ]
        cond = rule.setdefault("condition", {})
        if "urlFilter" in cond and not cond["urlFilter"].startswith("||"):
            cond["urlFilter"] = f"||{cond['urlFilter']}^"
        cond.setdefault("requestDomains", ["aisandbox-pa.googleapis.com"])
rules_path.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
print(f"Patched {manifest_path} and {rules_path}")
PY

echo "Reload extension: chrome://extensions -> Remove -> Load unpacked -> $EXT_DIR"
