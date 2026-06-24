#!/usr/bin/env bash
set -euo pipefail
pkill -f "agent.main" 2>/dev/null || true
echo "FlowKit agent stopped"
