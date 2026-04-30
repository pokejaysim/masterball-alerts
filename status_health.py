#!/usr/bin/env python3
"""Health snapshot helpers for the local MasterBall status page."""

from __future__ import annotations

from collections import Counter, deque
from datetime import datetime
import os
import re
import sqlite3
import subprocess
import time

from database import DB_PATH
from settings import MONITOR_DIR, load_config
from walmart_protected import walmart_proxy_ready, walmart_settings_from_config


DEFAULT_LABEL = "com.masterball.alerts"
DEFAULT_STALE_MINUTES = 5
LOG_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)$")

RETAILER_KEYWORDS = {
    "amazon": ("amazon", "amazon.ca"),
    "bestbuy": ("best buy", "bestbuy"),
    "costco": ("costco",),
    "ebgames": ("eb games", "gamestop"),
    "walmart": ("walmart",),
    "pokemoncenter": ("pokemon center",),
    "telegram": ("telegram",),
}

BLOCKED_WORDS = ("blocked", "captcha", "403", "429", "503")
ERROR_WORDS = ("error checking", "failed", "timed out", "timeout", "no response")
SUCCESS_WORDS = (" in stock", "sold by:", "trusted", "telegram message sent")


def parse_log_timestamp(line: str) -> datetime | None:
    match = LOG_TIMESTAMP_RE.match(line or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def resolve_log_path(log_path: str | None = None) -> str | None:
    candidates = [
        log_path,
        os.environ.get("MASTERBALL_LOG_PATH"),
        os.path.expanduser("~/Library/Logs/masterball-alerts.log"),
        os.path.join(MONITOR_DIR, "monitor.log"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def read_recent_log_lines(log_path: str | None = None, max_lines: int = 600) -> tuple[str | None, list[str]]:
    path = resolve_log_path(log_path)
    if not path:
        return None, []

    lines: deque[str] = deque(maxlen=max_lines)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line.rstrip("\n"))
    except OSError:
        return path, []
    return path, list(lines)


def latest_log_time(lines: list[str]) -> datetime | None:
    for line in reversed(lines):
        parsed = parse_log_timestamp(line)
        if parsed:
            return parsed
    return None


def latest_line_containing(lines: list[str], *needles: str) -> str | None:
    lowered_needles = tuple(n.lower() for n in needles)
    for line in reversed(lines):
        lowered = line.lower()
        if any(needle in lowered for needle in lowered_needles):
            return line
    return None


def _line_retailers(line: str) -> list[str]:
    lowered = line.lower()
    return [
        retailer
        for retailer, keywords in RETAILER_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    ]


def summarize_retailer_health(lines: list[str]) -> list[dict]:
    stats = {
        retailer: Counter({"blocked": 0, "errors": 0, "success": 0})
        for retailer in RETAILER_KEYWORDS
    }

    for line in lines:
        lowered = line.lower()
        retailers = _line_retailers(line)
        if not retailers:
            continue
        for retailer in retailers:
            if any(word in lowered for word in BLOCKED_WORDS):
                stats[retailer]["blocked"] += 1
            if any(word in lowered for word in ERROR_WORDS):
                stats[retailer]["errors"] += 1
            if any(word in lowered for word in SUCCESS_WORDS):
                stats[retailer]["success"] += 1

    result = []
    display_names = {
        "amazon": "Amazon",
        "bestbuy": "Best Buy",
        "costco": "Costco",
        "ebgames": "EB Games",
        "walmart": "Walmart",
        "pokemoncenter": "Pokemon Center",
        "telegram": "Telegram",
    }
    for retailer, counts in stats.items():
        blocked = counts["blocked"]
        errors = counts["errors"]
        success = counts["success"]
        if blocked >= 3:
            status = "degraded"
            note = "Blocks/CAPTCHA seen recently"
        elif errors >= 3:
            status = "degraded"
            note = "Repeated errors recently"
        elif success > 0:
            status = "ok"
            note = "Recent successful activity"
        elif blocked or errors:
            status = "watch"
            note = "Intermittent issue seen"
        else:
            status = "unknown"
            note = "No recent signal"

        result.append({
            "key": retailer,
            "name": display_names[retailer],
            "status": status,
            "blocked": blocked,
            "errors": errors,
            "success": success,
            "note": note,
        })
    return result


def launchagent_status(label: str = DEFAULT_LABEL, runner=None) -> dict:
    runner = runner or subprocess.run
    target = f"gui/{os.getuid()}/{label}"
    try:
        result = runner(
            ["launchctl", "print", target],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except FileNotFoundError:
        return {"label": label, "state": "unavailable", "running": None, "pid": None, "detail": "launchctl not found"}
    except Exception as exc:
        return {"label": label, "state": "unknown", "running": None, "pid": None, "detail": str(exc)}

    if result.returncode != 0:
        return {"label": label, "state": "not_loaded", "running": False, "pid": None, "detail": result.stderr.strip()}

    state = None
    pid = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("state = ") and state is None:
            state = stripped.split(" = ", 1)[1]
        elif stripped.startswith("pid = "):
            pid = stripped.split(" = ", 1)[1]

    return {
        "label": label,
        "state": state or "unknown",
        "running": state == "running",
        "pid": pid,
        "detail": "LaunchAgent loaded",
    }


def _count_rows(conn: sqlite3.Connection, table: str, where: str | None = None) -> int:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone():
        return 0
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return int(conn.execute(sql).fetchone()[0])


def database_summary(db_path: str = DB_PATH) -> dict:
    if not os.path.exists(db_path):
        return {"available": False, "path": db_path, "error": "database file missing"}

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        discovery = {
            "approved": _count_rows(conn, "discovery_candidates", "status = 'approved'"),
            "pending": _count_rows(conn, "discovery_candidates", "status = 'pending'"),
            "ignored": _count_rows(conn, "discovery_candidates", "status = 'ignored'"),
            "expired": _count_rows(conn, "discovery_candidates", "status = 'expired'"),
        }
        walmart_discovery = {
            "approved": _count_rows(conn, "discovery_candidates", "retailer = 'walmart' AND status = 'approved'"),
            "pending": _count_rows(conn, "discovery_candidates", "retailer = 'walmart' AND status = 'pending'"),
            "ignored": _count_rows(conn, "discovery_candidates", "retailer = 'walmart' AND status = 'ignored'"),
            "pending_validation": _count_rows(
                conn,
                "discovery_candidates",
                "retailer = 'walmart' AND status = 'pending' AND COALESCE(reason, '') LIKE 'walmart validation%'",
            ),
        }
        stock = {
            "tracked": _count_rows(conn, "stock_status"),
            "in_stock": _count_rows(conn, "stock_status", "in_stock = 1"),
        }
        alerts_today = _count_rows(
            conn,
            "alert_history",
            "alert_type = 'in_stock' AND timestamp >= date('now', 'localtime')",
        )
        recent_alerts = []
        if _count_rows(conn, "alert_history"):
            rows = conn.execute("""
                SELECT product_name, alert_type, timestamp, retailer, price
                FROM alert_history
                ORDER BY id DESC
                LIMIT 8
            """).fetchall()
            recent_alerts = [dict(row) for row in rows]
        conn.close()
        return {
            "available": True,
            "path": db_path,
            "discovery": discovery,
            "walmart_discovery": walmart_discovery,
            "stock": stock,
            "alerts_today": alerts_today,
            "recent_alerts": recent_alerts,
        }
    except sqlite3.Error as exc:
        return {"available": False, "path": db_path, "error": str(exc)}


def product_summary(config: dict | None = None, db: dict | None = None) -> dict:
    config = config if config is not None else load_config()
    db = db or {}
    seed_enabled = sum(1 for product in config.get("products", []) if product.get("enabled", True))
    approved_dynamic = int(db.get("discovery", {}).get("approved", 0))
    return {
        "seed_enabled": seed_enabled,
        "approved_dynamic": approved_dynamic,
        "active_total": seed_enabled + approved_dynamic,
        "check_interval": int(config.get("check_interval", 30)),
    }


def walmart_health_summary(config: dict, db: dict, lines: list[str]) -> dict:
    settings = walmart_settings_from_config(config)
    seed_active = sum(
        1
        for product in config.get("products", [])
        if product.get("enabled", True) and "walmart.ca" in product.get("url", "").lower()
    )
    approved_dynamic = int(db.get("walmart_discovery", {}).get("approved", 0))
    pending = int(db.get("walmart_discovery", {}).get("pending", 0))
    pending_validation = int(db.get("walmart_discovery", {}).get("pending_validation", 0))
    last_success = latest_line_containing(lines, "Walmart: In stock", "browser confirmed")
    last_block = latest_line_containing(lines, "Walmart CAPTCHA", "Walmart blocked", "Walmart proxy not configured", "Walmart returned 402", "Walmart returned 403", "Walmart returned 429")
    proxy_configured = walmart_proxy_ready()

    if not settings.get("enabled", True):
        lane_state = "idle"
    elif not proxy_configured:
        lane_state = "blocked"
    elif last_block and not last_success:
        lane_state = "blocked"
    elif last_block:
        lane_state = "degraded"
    elif last_success:
        lane_state = "ok"
    elif seed_active + approved_dynamic + pending > 0:
        lane_state = "degraded"
    else:
        lane_state = "idle"

    return {
        "enabled": bool(settings.get("enabled", True)),
        "lane_state": lane_state,
        "proxy_configured": proxy_configured,
        "active_product_count": seed_active + approved_dynamic,
        "seed_active_count": seed_active,
        "approved_dynamic_count": approved_dynamic,
        "pending_count": pending,
        "pending_validation_count": pending_validation,
        "last_successful_check": last_success,
        "last_block": last_block,
    }


def classify_snapshot(service: dict, last_log_age_seconds: float | None, stale_seconds: int, retailers: list[dict]) -> tuple[str, str]:
    if service.get("running") is False:
        return "down", "Monitor LaunchAgent is not running"
    if last_log_age_seconds is None:
        return "down", "No monitor log activity found"
    if last_log_age_seconds > stale_seconds:
        minutes = int(last_log_age_seconds // 60)
        return "down", f"No monitor log activity for {minutes} minutes"

    degraded = [r["name"] for r in retailers if r["status"] == "degraded"]
    if degraded:
        return "degraded", f"Retailer issues: {', '.join(degraded[:3])}"
    if service.get("running") is None:
        return "watch", "Monitor activity is fresh, but process state could not be verified"
    return "ok", "Monitor is running and logs are fresh"


def build_snapshot(
    config: dict | None = None,
    db_path: str = DB_PATH,
    log_path: str | None = None,
    stale_minutes: int = DEFAULT_STALE_MINUTES,
    service_label: str = DEFAULT_LABEL,
    service: dict | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    try:
        config = config if config is not None else load_config()
    except Exception as exc:
        config = {"_config_error": str(exc)}

    db = database_summary(db_path)
    products = product_summary(config, db)
    resolved_log_path, lines = read_recent_log_lines(log_path)
    last_log_at = latest_log_time(lines)
    last_log_age_seconds = (now - last_log_at).total_seconds() if last_log_at else None
    retailers = summarize_retailer_health(lines)
    walmart = walmart_health_summary(config, db, lines)
    service = service or launchagent_status(service_label)
    stale_seconds = int(stale_minutes * 60)
    overall, message = classify_snapshot(service, last_log_age_seconds, stale_seconds, retailers)

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": overall,
        "message": message,
        "service": service,
        "products": products,
        "database": db,
        "log": {
            "path": resolved_log_path,
            "last_seen_at": last_log_at.strftime("%Y-%m-%d %H:%M:%S") if last_log_at else None,
            "last_seen_age_seconds": last_log_age_seconds,
            "last_cycle": latest_line_containing(lines, "cycle done"),
            "last_discovery": latest_line_containing(lines, "auto-discovery finished", "discovery complete"),
            "tail": lines[-40:],
        },
        "retailers": retailers,
        "walmart": walmart,
        "actions": action_suggestions(overall, walmart),
    }


def action_suggestions(overall: str, walmart: dict | None = None) -> list[str]:
    walmart = walmart or {}
    if walmart.get("lane_state") in {"blocked", "degraded"}:
        return [
            "./control.sh doctor-walmart",
            "./control.sh discover-walmart-dry-run",
            "./control.sh logs",
        ]
    if overall == "down":
        return [
            "./control.sh status",
            "./control.sh start",
            "./control.sh logs",
        ]
    if overall == "degraded":
        return [
            "./control.sh logs",
            "./control.sh doctor-retailers",
            "./control.sh discover-dry-run",
        ]
    if overall == "watch":
        return [
            "./control.sh status",
            "./control.sh logs",
        ]
    return [
        "./control.sh status",
        "./control.sh logs",
    ]


def snapshot_for_cli(**kwargs) -> dict:
    return build_snapshot(now=datetime.now(), **kwargs)
