#!/usr/bin/env bash
set -euo pipefail
curl -sf http://127.0.0.1:8765/health
echo
curl -sf http://127.0.0.1:8100/health
echo
code=$(curl -s -o /tmp/smoke_out.json -w '%{http_code}' -X POST http://127.0.0.1:8100/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"vps-smoke-test","story":null}')
echo "POST /api/projects -> HTTP $code"
cat /tmp/smoke_out.json
echo
