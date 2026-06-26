#!/usr/bin/env bash
# Usage: vps-resume-run.sh RUN_ID SCENES_JSON [ENTITIES_JSON]
set -euo pipefail
RUN_ID="${1:?run_id}"
SCENES="${2:?scenes.json path}"
ENTITIES="${3:-}"
RUN_DIR="/opt/niche/runs/${RUN_ID}"
mkdir -p "$RUN_DIR"

python3 <<PY
import json
from pathlib import Path
scenes = json.loads(Path("$SCENES").read_text())
entities = []
ent = Path("$ENTITIES")
if ent.exists() and ent.stat().st_size:
    entities = json.loads(ent.read_text())
Path("$RUN_DIR/scenes.json").write_text(json.dumps({"scenes": scenes, "entities": entities}, indent=2))
PY

chown niche:niche "$RUN_DIR/scenes.json"
SECRET=$(sudo grep '^WEBHOOK_SECRET=' /opt/niche/.env | cut -d= -f2-)
curl -sf -X POST "http://127.0.0.1:8765/runs/${RUN_ID}/resume" \
  -H "Authorization: Bearer ${SECRET}"
echo
sleep 2
curl -sf "http://127.0.0.1:8765/runs/${RUN_ID}/status" \
  -H "Authorization: Bearer ${SECRET}" | head -c 400
echo
