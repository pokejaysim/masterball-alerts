#!/bin/bash
# Pokemon Monitor Control Script

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${MASTERBALL_VENV:-$SCRIPT_DIR/../pokemon-monitor-env}"
PYTHON="$VENV_DIR/bin/python3"
MONITOR="$SCRIPT_DIR/monitor.py"
PID_FILE="$SCRIPT_DIR/monitor.pid"
LAUNCH_LABEL="com.masterball.alerts"
LAUNCH_PLIST="$HOME/Library/LaunchAgents/$LAUNCH_LABEL.plist"
LAUNCH_LOG="$HOME/Library/Logs/masterball-alerts.log"
STATUS_LABEL="com.masterball.status"
STATUS_PLIST="$HOME/Library/LaunchAgents/$STATUS_LABEL.plist"
STATUS_LOG="$HOME/Library/Logs/masterball-status.log"
STATUS_ERR_LOG="$HOME/Library/Logs/masterball-status.err.log"
STATUS_HOST="${MASTERBALL_STATUS_HOST:-127.0.0.1}"
STATUS_PORT="${MASTERBALL_STATUS_PORT:-8787}"

if [ ! -x "$PYTHON" ]; then
    if [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
        PYTHON="$SCRIPT_DIR/.venv/bin/python3"
    else
        PYTHON="python3"
    fi
fi

cd "$SCRIPT_DIR"

install_status_launchagent() {
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    /usr/bin/python3 - "$STATUS_PLIST" "$SCRIPT_DIR" "$PYTHON" "$STATUS_HOST" "$STATUS_PORT" "$STATUS_LOG" "$STATUS_ERR_LOG" <<'PY'
import plistlib
import os
import sys

plist_path, script_dir, python_path, host, port, stdout_path, stderr_path = sys.argv[1:]
command = f'cd "{script_dir}" && exec "{python_path}" -u status_page.py --host "{host}" --port "{port}"'
data = {
    "Label": "com.masterball.status",
    "ProgramArguments": ["/bin/zsh", "-lc", command],
    "RunAtLoad": True,
    "KeepAlive": True,
    "WorkingDirectory": os.path.expanduser("~"),
    "StandardOutPath": stdout_path,
    "StandardErrorPath": stderr_path,
    "EnvironmentVariables": {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONUNBUFFERED": "1",
    },
}
with open(plist_path, "wb") as handle:
    plistlib.dump(data, handle)
PY
}

case "$1" in
    start)
        if [ -f "$LAUNCH_PLIST" ]; then
            echo "🚀 Starting MasterBall LaunchAgent..."
            launchctl bootstrap "gui/$(id -u)" "$LAUNCH_PLIST" >/dev/null 2>&1 || true
            launchctl kickstart -k "gui/$(id -u)/$LAUNCH_LABEL"
            echo "✅ LaunchAgent started"
            echo "📝 Logs: tail -f $LAUNCH_LOG"
            exit 0
        fi

        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p $PID > /dev/null 2>&1; then
                echo "❌ Monitor is already running (PID: $PID)"
                exit 1
            fi
        fi
        
        echo "🚀 Starting Pokemon Monitor..."
        nohup "$PYTHON" -u "$MONITOR" > monitor.log 2>&1 &
        echo $! > "$PID_FILE"
        echo "✅ Monitor started (PID: $!)"
        echo "📝 Logs: tail -f $SCRIPT_DIR/monitor.log"
        ;;
        
    stop)
        if launchctl print "gui/$(id -u)/$LAUNCH_LABEL" >/dev/null 2>&1; then
            echo "⏹️  Stopping MasterBall LaunchAgent..."
            launchctl bootout "gui/$(id -u)" "$LAUNCH_PLIST" >/dev/null 2>&1 || true
            rm -f "$PID_FILE"
            echo "✅ LaunchAgent stopped"
            exit 0
        fi

        if [ ! -f "$PID_FILE" ]; then
            echo "❌ Monitor is not running"
            exit 1
        fi
        
        PID=$(cat "$PID_FILE")
        echo "⏹️  Stopping Pokemon Monitor (PID: $PID)..."
        kill $PID
        rm "$PID_FILE"
        echo "✅ Monitor stopped"
        ;;
        
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;

    bootstrap)
        "$SCRIPT_DIR/bootstrap.sh"
        ;;
        
    status)
        if launchctl print "gui/$(id -u)/$LAUNCH_LABEL" >/dev/null 2>&1; then
            STATE=$(launchctl print "gui/$(id -u)/$LAUNCH_LABEL" 2>/dev/null | awk -F' = ' '/state =/ {print $2; exit}')
            PID=$(launchctl print "gui/$(id -u)/$LAUNCH_LABEL" 2>/dev/null | awk -F' = ' '/pid =/ {print $2; exit}')
            if [ "$STATE" = "running" ]; then
                echo "✅ Monitor LaunchAgent is running (PID: ${PID:-unknown})"
                exit 0
            fi
            echo "⚠️  Monitor LaunchAgent exists but state is: ${STATE:-unknown}"
            exit 1
        fi

        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p $PID > /dev/null 2>&1; then
                echo "✅ Monitor is running (PID: $PID)"
                exit 0
            else
                echo "❌ PID file exists but process is not running"
                rm "$PID_FILE"
                exit 1
            fi
        else
            echo "❌ Monitor is not running"
            exit 1
        fi
        ;;
        
    logs)
        if [ -f "$LAUNCH_LOG" ]; then
            tail -f "$LAUNCH_LOG"
        else
            tail -f "$SCRIPT_DIR/monitor.log"
        fi
        ;;

    dashboard-start)
        install_status_launchagent
        echo "🚦 Starting MasterBall status page..."
        launchctl bootstrap "gui/$(id -u)" "$STATUS_PLIST" >/dev/null 2>&1 || true
        launchctl kickstart -k "gui/$(id -u)/$STATUS_LABEL"
        echo "✅ Status page started: http://$STATUS_HOST:$STATUS_PORT"
        echo "📝 Logs: tail -f $STATUS_LOG"
        ;;

    dashboard-stop)
        if launchctl print "gui/$(id -u)/$STATUS_LABEL" >/dev/null 2>&1; then
            echo "⏹️  Stopping MasterBall status page..."
            launchctl bootout "gui/$(id -u)" "$STATUS_PLIST" >/dev/null 2>&1 || true
            echo "✅ Status page stopped"
        else
            echo "❌ Status page is not running"
            exit 1
        fi
        ;;

    dashboard-status)
        if launchctl print "gui/$(id -u)/$STATUS_LABEL" >/dev/null 2>&1; then
            STATE=$(launchctl print "gui/$(id -u)/$STATUS_LABEL" 2>/dev/null | awk -F' = ' '/state =/ {print $2; exit}')
            PID=$(launchctl print "gui/$(id -u)/$STATUS_LABEL" 2>/dev/null | awk -F' = ' '/pid =/ {print $2; exit}')
            if [ "$STATE" = "running" ]; then
                echo "✅ Status page is running (PID: ${PID:-unknown})"
                echo "🌐 http://$STATUS_HOST:$STATUS_PORT"
                exit 0
            fi
            echo "⚠️  Status page exists but state is: ${STATE:-unknown}"
            exit 1
        fi
        echo "❌ Status page is not running"
        exit 1
        ;;

    dashboard-open)
        "$0" dashboard-start
        open "http://$STATUS_HOST:$STATUS_PORT"
        ;;

    dashboard-logs)
        tail -f "$STATUS_LOG"
        ;;

    status-json)
        "$PYTHON" "$SCRIPT_DIR/status_page.py" --json
        ;;

    discover-now)
        echo "🔍 Discovering new Pokemon TCG products..."
        shift
        "$PYTHON" "$SCRIPT_DIR/discover.py" "$@"
        ;;

    discover-dry-run)
        echo "🔍 Dry-run discovery (no queue writes, no Telegram)..."
        shift
        "$PYTHON" "$SCRIPT_DIR/discover.py" --dry-run "$@"
        ;;

    discover-auto-add)
        echo "🔍 Discovering and auto-adding high-confidence Pokemon TCG products..."
        shift
        "$PYTHON" "$SCRIPT_DIR/discover.py" --auto-approve "$@"
        ;;

    doctor)
        "$PYTHON" "$SCRIPT_DIR/doctor.py"
        ;;

    doctor-retailers)
        "$PYTHON" "$SCRIPT_DIR/doctor.py" --retailers
        ;;

    test-product)
        if [ -z "$2" ]; then
            echo "Usage: $0 test-product <product-url>"
            exit 1
        fi
        "$PYTHON" "$MONITOR" --test-product "$2"
        ;;
        
    test)
        echo "🧪 Running setup doctor..."
        "$PYTHON" "$SCRIPT_DIR/doctor.py"
        ;;
        
    *)
        echo "Usage: $0 {bootstrap|start|stop|restart|status|logs|dashboard-start|dashboard-stop|dashboard-status|dashboard-open|dashboard-logs|status-json|discover-now|discover-dry-run|discover-auto-add|doctor|doctor-retailers|test-product|test}"
        echo ""
        echo "Commands:"
        echo "  bootstrap        - Create/update venv, install dependencies, initialize DB"
        echo "  start   - Start the monitor in background"
        echo "  stop    - Stop the monitor"
        echo "  restart - Restart the monitor"
        echo "  status  - Check if monitor is running"
        echo "  logs    - View live logs"
        echo "  dashboard-start  - Start the private local status page"
        echo "  dashboard-stop   - Stop the status page"
        echo "  dashboard-status - Check if the status page is running"
        echo "  dashboard-open   - Start and open the status page"
        echo "  dashboard-logs   - View live status page logs"
        echo "  status-json      - Print one machine-readable health snapshot"
        echo "  discover-now     - Scan retailers and send Telegram review queue"
        echo "  discover-dry-run - Scan retailers without saving or sending Telegram"
        echo "  discover-auto-add - Auto-approve high-confidence discovered products"
        echo "  doctor           - Check setup and local dependencies"
        echo "  doctor-retailers - Check setup plus one live check per retailer"
        echo "  test-product     - Check one product URL and print parsed result"
        echo "  test             - Alias for doctor"
        exit 1
        ;;
esac
