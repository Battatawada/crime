#!/usr/bin/env bash
# Usage: vps-resume-run.sh RUN_ID [SCENES_JSON] [ENTITIES_JSON]
# If SCENES_JSON omitted, uses /opt/niche/runs/RUN_ID/scenes.json when present.
set -euo pipefail
RUN_ID="${1:?run_id}"
SCENES="${2:-}"
ENTITIES="${3:-}"
RUN_DIR="/opt/niche/runs/${RUN_ID}"
mkdir -p "$RUN_DIR"

if [[ -z "$SCENES" ]]; then
  SCENES="$RUN_DIR/scenes.json"
fi

if [[ ! -f "$SCENES" ]]; then
  echo "ERROR: scenes file not found: $SCENES" >&2
  exit 1
fi

python3 <<PY
import json
from pathlib import Path

src = Path("""$SCENES""")
data = json.loads(src.read_text(encoding="utf-8"))
if isinstance(data, dict) and "scenes" in data:
    scenes = data["scenes"]
    entities = data.get("entities", [])
elif isinstance(data, list):
    scenes = data
    entities = []
else:
    raise SystemExit(f"Unrecognized scenes JSON format in {src}")

entities_path = """$ENTITIES"""
if entities_path:
    ent = Path(entities_path)
    if ent.is_file() and ent.stat().st_size:
        entities = json.loads(ent.read_text(encoding="utf-8"))

Path("""$RUN_DIR/scenes.json""").write_text(
    json.dumps({"scenes": scenes, "entities": entities}, indent=2),
    encoding="utf-8",
)
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
