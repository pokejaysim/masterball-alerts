#!/bin/bash
# Daily backup of MasterBall config and database
MONITOR_DIR="<repo>"
BACKUP_DIR="$MONITOR_DIR/backups"
DATE=$(date +%Y-%m-%d)

mkdir -p "$BACKUP_DIR"

# Backup config
cp "$MONITOR_DIR/config.json" "$BACKUP_DIR/config_$DATE.json" 2>/dev/null

# Backup database (safe copy using sqlite3)
sqlite3 "$MONITOR_DIR/masterball.db" ".backup '$BACKUP_DIR/masterball_$DATE.db'" 2>/dev/null

# Backup stock status JSON
cp "$MONITOR_DIR/stock_status.json" "$BACKUP_DIR/stock_status_$DATE.json" 2>/dev/null

# Delete backups older than 14 days
find "$BACKUP_DIR" -name "*.json" -mtime +14 -delete 2>/dev/null
find "$BACKUP_DIR" -name "*.db" -mtime +14 -delete 2>/dev/null

echo "[$(date)] Backup complete" >> "$MONITOR_DIR/backup.log"
