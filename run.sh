#!/bin/bash
# Start Trident Protect Web UI (manual mode)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.web.pid"
LOG_FILE="$SCRIPT_DIR/logs/web.log"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Already running (PID $(cat "$PID_FILE"))"
    echo "Stop first with: ./stop.sh"
    exit 1
fi

if [ ! -f config.yaml ]; then
    echo "ERROR: config.yaml not found. Run ./install.sh first."
    exit 1
fi

# Use venv python if available, otherwise system python3
if [ -x .venv/bin/python3 ]; then
    PYTHON=".venv/bin/python3"
else
    PYTHON="python3"
    echo "WARNING: .venv not found, falling back to system python3"
fi

if [ ! -x bin/tridentprotect-ctl ]; then
    echo "ERROR: bin/tridentprotect-ctl not found. Run ./install.sh first."
    exit 1
fi

HOST=$($PYTHON -c "from app.config import Config; print(Config.instance().app.get('host', '0.0.0.0'))" 2>/dev/null || echo "0.0.0.0")
PORT=$($PYTHON -c "from app.config import Config; print(Config.instance().app.get('port', 8080))" 2>/dev/null || echo "8080")

mkdir -p logs
echo "Starting Trident Protect Web UI on http://${HOST}:${PORT}"
nohup $PYTHON -m app.main > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Started (PID $(cat "$PID_FILE"))"
    echo "Logs: tail -f $LOG_FILE"
    echo "Stop: ./stop.sh"
else
    echo "Failed to start. Check log:"
    tail -20 "$LOG_FILE"
    exit 1
fi
