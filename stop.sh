#!/bin/bash
# Stop Trident Protect Web UI

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.web.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Not running (no PID file)"
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping PID $PID..."
    kill "$PID"
    sleep 2
    if kill -0 "$PID" 2>/dev/null; then
        echo "Force killing..."
        kill -9 "$PID"
    fi
    echo "Stopped"
else
    echo "Process $PID not running"
fi
rm -f "$PID_FILE"
