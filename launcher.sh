#!/usr/bin/env bash
# launcher.sh — Auto-ATS Service Launcher
# Starts the local Flask web UI for resume tailoring

set -e

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
ENV_FILE="${PROJECT_ROOT}/.env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck source=/dev/null
    set -a
    source "$ENV_FILE"
    set +a
fi

WEB_PIDFILE="${WEB_PIDFILE:-/tmp/auto-ats-webui.pid}"

header() {
    echo -e "\e[1;34m========================================\e[0m"
    echo -e "\e[1;34m  auto-ATS Service Launcher\e[0m"
    echo -e "\e[1;34m========================================\e[0m"
}

info() { echo -e "\e[32m[INFO]\e[0m $1"; }
ok()   { echo -e "\e[32m[OK]\e[0m $1"; }
error(){ echo -e "\e[31m[ERROR]\e[0m $1"; }

is_running() {
    local pidfile="$1"
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

kill_pidfile() {
    local pidfile="$1"
    local label="$2"
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile" 2>/dev/null)
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null && ok "Stopped $label (PID: $pid)" || error "Failed to stop $label"
        fi
        rm -f "$pidfile"
    else
        ok "$label was not running"
    fi
}

cmd_start_webui() {
    header
    if is_running "$WEB_PIDFILE"; then
        info "Web UI already running (PID: $(cat "$WEB_PIDFILE"))"
    else
        cd "$PROJECT_ROOT"
        info "Starting Web UI on port ${LOCAL_WEBUI_PORT:-5000}..."
        if [ -f "server.py" ]; then
            python3 server.py &
            echo $! > "$WEB_PIDFILE"
            ok "Web UI started (PID: $(cat "$WEB_PIDFILE"))"
        else
            error "server.py not found in $PROJECT_ROOT"
        fi
    fi
    echo
}

cmd_stop() {
    header
    kill_pidfile "$WEB_PIDFILE" "Web UI"
    ok "Web UI stopped."
    echo
}

cmd_status() {
    header
    echo "Web UI: $(if is_running "$WEB_PIDFILE"; then echo "Running (PID: $(cat "$WEB_PIDFILE"))"; else echo "Stopped"; fi)"
    echo
}

case "${1:-}" in
    start)
        cmd_start_webui
        ;;
    stop)
        cmd_stop
        ;;
    status)
        cmd_status
        ;;
    restart)
        cmd_stop
        sleep 1
        cmd_start_webui
        ;;
    *)
        header
        echo "Usage: $0 {start|stop|status|restart}"
        echo
        echo "Services:"
        echo "  Web UI - Local Flask interface (port ${LOCAL_WEBUI_PORT:-5000})"
        exit 1
        ;;
esac
