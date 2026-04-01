#!/bin/bash
# Pokemon Monitor Control Script

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/../pokemon-monitor-env"
PYTHON="$VENV_DIR/bin/python3"
MONITOR="$SCRIPT_DIR/monitor.py"
PID_FILE="$SCRIPT_DIR/monitor.pid"

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
        
    test)
        echo "🧪 Running test check..."
        "$PYTHON" "$MONITOR"
        ;;
        
    *)
        echo "Usage: $0 {start|stop|restart|status|logs|test}"
        echo ""
        echo "Commands:"
        echo "  start   - Start the monitor in background"
        echo "  stop    - Stop the monitor"
        echo "  restart - Restart the monitor"
        echo "  status  - Check if monitor is running"
        echo "  logs    - View live logs"
        echo "  test    - Run a test check (foreground)"
        exit 1
        ;;
esac
