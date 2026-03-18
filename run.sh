#!/bin/bash
# run.sh — Jalankan Atreides-1 selama 24 jam dengan auto-restart
# Usage: bash run.sh

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/atreides.log"
PID_FILE="$PROJECT_DIR/.atreides.pid"

mkdir -p "$LOG_DIR"

echo "⚔️  Starting Atreides-1 Trading System..."
echo "📁 Project: $PROJECT_DIR"
echo "📄 Log: $LOG_FILE"
echo ""

# Aktifkan virtual environment
source "$PROJECT_DIR/.venv/bin/activate"

# Loop restart otomatis
RESTART_COUNT=0
while true; do
    RESTART_COUNT=$((RESTART_COUNT + 1))
    START_TIME=$(date '+%Y-%m-%d %H:%M:%S')

    echo "[$START_TIME] 🚀 Starting run #$RESTART_COUNT..." | tee -a "$LOG_FILE"

    # Jalankan orchestrator, simpan PID
    python "$PROJECT_DIR/orchestrator.py" 2>&1 | tee -a "$LOG_FILE" &
    BOT_PID=$!
    echo $BOT_PID > "$PID_FILE"

    echo "[$START_TIME] PID: $BOT_PID" | tee -a "$LOG_FILE"

    # Tunggu process selesai
    wait $BOT_PID
    EXIT_CODE=$?

    END_TIME=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$END_TIME] ⚠️  Process exited with code $EXIT_CODE. Restarting in 10s..." | tee -a "$LOG_FILE"

    sleep 10
done

# Tambahkan ke .env jika belum ada:
# TRADING_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
# RR_RATIO=2.0