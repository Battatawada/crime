#!/usr/bin/env python3
"""Add declarativeNetRequestWithHostAccess — required for modifyHeaders rules (Chrome 101+). Does not touch rules.json."""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/flowkit/extension/manifest.json")
manifest = json.loads(path.read_text(encoding="utf-8"))
perms = list(manifest.get("permissions", []))
if "declarativeNetRequestWithHostAccess" not in perms:
    perms.append("declarativeNetRequestWithHostAccess")
    manifest["permissions"] = perms
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Added declarativeNetRequestWithHostAccess")
else:
    print("Already present")
print(json.dumps(manifest["permissions"], indent=2))
