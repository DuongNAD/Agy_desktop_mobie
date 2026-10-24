#!/usr/bin/env bash
# ==============================================================================
# AgyMobile - 1-Click Startup Launcher for macOS & Linux
# Antigravity 2.0 & Desktop Workspace Mobile Remote Platform
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

HOST="0.0.0.0"
PORT="8765"
OPEN_BROWSER=1
EXTRA_ARGS=()

for arg in "$@"; do
    if [ "$arg" = "--no-browser" ]; then
        OPEN_BROWSER=0
    elif [ "$arg" = "--install" ]; then
        "$SCRIPT_DIR/install.sh"
        exit 0
    else
        EXTRA_ARGS+=("$arg")
    fi
done

# Check if .venv exists
VENV_PY="$SCRIPT_DIR/.venv/bin/python"
if [ ! -f "$VENV_PY" ]; then
    echo "[AgyMobile] Môi trường chưa được cài đặt. Đang tự động chạy bộ cài đặt..."
    "$SCRIPT_DIR/install.sh"
    exit 0
fi

echo ""
echo "=============================================================================="
echo "  AgyMobile - Khởi động Antigravity Desktop Mobile Controller"
echo "=============================================================================="
echo ""

# Auto open browser in background after 1.5 seconds
if [ "$OPEN_BROWSER" -eq 1 ]; then
    (
        sleep 1.5
        if [ "$(uname)" = "Darwin" ]; then
            open "http://localhost:$PORT" >/dev/null 2>&1 || true
        elif command -v xdg-open &>/dev/null; then
            xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || true
        fi
    ) &
fi

# Run FastAPI Server
exec "$VENV_PY" -m uvicorn backend.main:create_app --factory --host "$HOST" --port "$PORT" "${EXTRA_ARGS[@]}"
