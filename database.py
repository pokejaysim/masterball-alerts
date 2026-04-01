"""
MasterBall Alerts — SQLite Database Layer
Thread-safe database for stock status, alert history, timestamps, and cooldowns.
Replaces JSON files that can corrupt under parallel writes.
"""

import sqlite3
import os
import json
import time
import threading
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masterball.db")

# Thread-local storage for connections
_local = threading.local()


def get_conn():
    """Get a thread-local database connection."""
    if not hasattr(_local, 'conn') or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stock_status (
            product_name TEXT PRIMARY KEY,
            in_stock INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            alert_type TEXT NOT NULL,  -- 'in_stock' or 'out_of_stock'
            timestamp TEXT NOT NULL,
            retailer TEXT,
            url TEXT,
            price TEXT
        );

        CREATE TABLE IF NOT EXISTS check_timestamps (
            product_name TEXT PRIMARY KEY,
            last_checked TEXT
        );

        CREATE TABLE IF NOT EXISTS alert_cooldowns (
            product_name TEXT PRIMARY KEY,
            last_alert_time REAL
        );

        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT NOT NULL,
            vote_type TEXT NOT NULL,  -- 'got' or 'missed'
            voted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS product_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            submitted_at TEXT,
            status TEXT DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS error_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            retailer TEXT,
            error_type TEXT,
            message TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            alerts_sent INTEGER DEFAULT 0,
            checks_total INTEGER DEFAULT 0,
            checks_failed INTEGER DEFAULT 0,
            captchas_hit INTEGER DEFAULT 0
        );
    """)
    conn.commit()


# --- Stock Status ---

def get_stock_status(product_name=None):
    conn = get_conn()
    if product_name:
        row = conn.execute("SELECT in_stock FROM stock_status WHERE product_name = ?", (product_name,)).fetchone()
        return bool(row['in_stock']) if row else False
    else:
        rows = conn.execute("SELECT product_name, in_stock FROM stock_status").fetchall()
        return {r['product_name']: bool(r['in_stock']) for r in rows}


def set_stock_status(product_name, in_stock):
    conn = get_conn()
    conn.execute("""
        INSERT INTO stock_status (product_name, in_stock, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(product_name) DO UPDATE SET in_stock = ?, updated_at = ?
    """, (product_name, int(in_stock), datetime.now().isoformat(), int(in_stock), datetime.now().isoformat()))
    conn.commit()


# --- Alert History ---

def add_alert(product_name, alert_type, retailer=None, url=None, price=None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO alert_history (product_name, alert_type, timestamp, retailer, url, price)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (product_name, alert_type, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), retailer, url, price))
    conn.commit()


def get_alerts(limit=50, alert_type=None):
    conn = get_conn()
    if alert_type:
        rows = conn.execute(
            "SELECT * FROM alert_history WHERE alert_type = ? ORDER BY id DESC LIMIT ?",
            (alert_type, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM alert_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_alerts_today():
    conn = get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM alert_history WHERE timestamp LIKE ? AND alert_type = 'in_stock'",
        (f"{today}%",)
    ).fetchone()
    return row['cnt'] if row else 0


def get_total_alerts():
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM alert_history WHERE alert_type = 'in_stock'").fetchone()
    return row['cnt'] if row else 0


# --- Timestamps ---

def update_timestamp(product_name):
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO check_timestamps (product_name, last_checked) VALUES (?, ?)
        ON CONFLICT(product_name) DO UPDATE SET last_checked = ?
    """, (product_name, now, now))
    conn.commit()


def get_timestamps():
    conn = get_conn()
    rows = conn.execute("SELECT product_name, last_checked FROM check_timestamps").fetchall()
    return {r['product_name']: r['last_checked'] for r in rows}


# --- Cooldowns ---

def check_cooldown(product_name, cooldown_seconds=600):
    conn = get_conn()
    row = conn.execute("SELECT last_alert_time FROM alert_cooldowns WHERE product_name = ?", (product_name,)).fetchone()
    if not row:
        return True
    return (time.time() - row['last_alert_time']) > cooldown_seconds


def set_cooldown(product_name):
    conn = get_conn()
    conn.execute("""
        INSERT INTO alert_cooldowns (product_name, last_alert_time) VALUES (?, ?)
        ON CONFLICT(product_name) DO UPDATE SET last_alert_time = ?
    """, (product_name, time.time(), time.time()))
    conn.commit()


# --- Votes ---

def add_vote(alert_id, vote_type):
    conn = get_conn()
    conn.execute("""
        INSERT INTO votes (alert_id, vote_type, voted_at) VALUES (?, ?, ?)
    """, (alert_id, vote_type, datetime.now().isoformat()))
    conn.commit()
    return get_vote_counts(alert_id)


def get_vote_counts(alert_id):
    conn = get_conn()
    rows = conn.execute("SELECT vote_type, COUNT(*) as cnt FROM votes WHERE alert_id = ? GROUP BY vote_type", (alert_id,)).fetchall()
    result = {"got": 0, "missed": 0}
    for r in rows:
        result[r['vote_type']] = r['cnt']
    return result


def get_all_votes():
    conn = get_conn()
    rows = conn.execute("SELECT alert_id, vote_type, COUNT(*) as cnt FROM votes GROUP BY alert_id, vote_type").fetchall()
    result = {}
    for r in rows:
        if r['alert_id'] not in result:
            result[r['alert_id']] = {"got": 0, "missed": 0}
        result[r['alert_id']][r['vote_type']] = r['cnt']
    return result


# --- Product Requests ---

def add_request(url):
    conn = get_conn()
    conn.execute("""
        INSERT INTO product_requests (url, submitted_at) VALUES (?, ?)
    """, (url, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


# --- Error Tracking ---

def log_error(retailer, error_type, message):
    conn = get_conn()
    conn.execute("""
        INSERT INTO error_log (timestamp, retailer, error_type, message) VALUES (?, ?, ?, ?)
    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), retailer, error_type, message))
    conn.commit()


def get_error_counts(minutes=60):
    """Get error counts in the last N minutes."""
    conn = get_conn()
    cutoff = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute("""
        SELECT retailer, error_type, COUNT(*) as cnt
        FROM error_log
        WHERE timestamp > datetime(?, '-' || ? || ' minutes')
        GROUP BY retailer, error_type
    """, (cutoff, minutes)).fetchall()
    return [dict(r) for r in rows]


# --- Daily Stats ---

def increment_daily_stat(field):
    ALLOWED_FIELDS = {'alerts_sent', 'checks_total', 'checks_failed', 'captchas_hit'}
    if field not in ALLOWED_FIELDS:
        return
    conn = get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute(f"""
        INSERT INTO daily_stats (date, {field}) VALUES (?, 1)
        ON CONFLICT(date) DO UPDATE SET {field} = {field} + 1
    """, (today,))
    conn.commit()


def get_daily_stats(date=None):
    conn = get_conn()
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (date,)).fetchone()
    return dict(row) if row else {"date": date, "alerts_sent": 0, "checks_total": 0, "checks_failed": 0, "captchas_hit": 0}


# --- Migration: Import existing JSON data ---

def migrate_from_json():
    """One-time migration from JSON files to SQLite."""
    monitor_dir = os.path.dirname(os.path.abspath(__file__))

    # Stock status
    status_file = os.path.join(monitor_dir, "stock_status.json")
    if os.path.exists(status_file):
        try:
            with open(status_file) as f:
                data = json.load(f)
            for name, in_stock in data.items():
                set_stock_status(name, in_stock)
            print(f"  Migrated {len(data)} stock status entries")
        except:
            pass

    # Votes
    dashboard_dir = os.path.join(os.path.dirname(monitor_dir), "masterball-dashboard")
    votes_file = os.path.join(dashboard_dir, "votes.json")
    if os.path.exists(votes_file):
        try:
            with open(votes_file) as f:
                data = json.load(f)
            for alert_id, counts in data.items():
                for _ in range(counts.get("got", 0)):
                    add_vote(alert_id, "got")
                for _ in range(counts.get("missed", 0)):
                    add_vote(alert_id, "missed")
            print(f"  Migrated {len(data)} vote entries")
        except:
            pass

    print("  Migration complete!")


if __name__ == "__main__":
    print("Initializing MasterBall database...")
    init_db()
    print("  Tables created.")
    print("Migrating existing data...")
    migrate_from_json()
