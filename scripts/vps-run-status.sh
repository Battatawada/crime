#!/usr/bin/env bash
RUN_ID="${1:?run_id}"
SECRET=$(sudo grep '^WEBHOOK_SECRET=' /opt/niche/.env | cut -d= -f2-)
curl -sf "http://127.0.0.1:8765/runs/${RUN_ID}/status" \
  -H "Authorization: Bearer ${SECRET}"
