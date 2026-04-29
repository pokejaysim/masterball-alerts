#!/bin/bash
# Pokemon Monitor Control Script

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${MASTERBALL_VENV:-$SCRIPT_DIR/../pokemon-monitor-env}"
PYTHON="$VENV_DIR/bin/python3"
MONITOR="$SCRIPT_DIR/monitor.py"
PID_FILE="$SCRIPT_DIR/monitor.pid"

if [ ! -x "$PYTHON" ]; then
    if [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
        PYTHON="$SCRIPT_DIR/.venv/bin/python3"
    else
        PYTHON="python3"
    fi
fi

cd "$SCRIPT_DIR"

case "$1" in
    start)
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
        tail -f "$SCRIPT_DIR/monitor.log"
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
        echo "Usage: $0 {bootstrap|start|stop|restart|status|logs|discover-now|discover-dry-run|doctor|doctor-retailers|test-product|test}"
        echo ""
        echo "Commands:"
        echo "  bootstrap        - Create/update venv, install dependencies, initialize DB"
        echo "  start   - Start the monitor in background"
        echo "  stop    - Stop the monitor"
        echo "  restart - Restart the monitor"
        echo "  status  - Check if monitor is running"
        echo "  logs    - View live logs"
        echo "  discover-now     - Scan retailers and send Telegram review queue"
        echo "  discover-dry-run - Scan retailers without saving or sending Telegram"
        echo "  doctor           - Check setup and local dependencies"
        echo "  doctor-retailers - Check setup plus one live check per retailer"
        echo "  test-product     - Check one product URL and print parsed result"
        echo "  test             - Alias for doctor"
        exit 1
        ;;
esac
