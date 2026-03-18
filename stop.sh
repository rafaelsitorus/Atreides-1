#!/bin/bash
# stop.sh — Hentikan Atreides-1

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJECT_DIR/.atreides.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    echo "🛑 Stopping Atreides-1 (PID: $PID)..."
    kill $PID 2>/dev/null
    pkill -f "orchestrator.py" 2>/dev/null
    rm -f "$PID_FILE"
    echo "✅ Stopped."
else
    echo "⚠️  No PID file found. Trying pkill..."
    pkill -f "orchestrator.py"
    echo "✅ Done."
fi