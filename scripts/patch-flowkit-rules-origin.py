#!/usr/bin/env python3
"""
Remove Origin header from FlowKit rules.json — Chrome rejects 'set' on Origin at load time.
Referer rule unchanged. Backs up original first. Does not touch any other FlowKit files.
"""
import json
import shutil
import sys
from pathlib import Path

rules_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/flowkit/extension/rules.json")
backup = rules_path.with_suffix(".json.bak")

if not rules_path.exists():
    sys.exit(f"Not found: {rules_path}")

rules = json.loads(rules_path.read_text(encoding="utf-8"))
if not backup.exists():
    shutil.copy2(rules_path, backup)
    print(f"Backup: {backup}")

changed = False
for rule in rules:
    action = rule.get("action", {})
    if action.get("type") != "modifyHeaders":
        continue
    headers = action.get("requestHeaders", [])
    new_headers = [h for h in headers if h.get("header", "").lower() != "origin"]
    if len(new_headers) != len(headers):
        action["requestHeaders"] = new_headers
        changed = True

if not changed:
    print("Origin rule already removed")
else:
    rules_path.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
    print(f"Patched: {rules_path} (Origin removed, Referer kept)")

print(json.dumps(rules, indent=2))
