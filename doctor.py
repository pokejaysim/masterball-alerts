#!/usr/bin/env python3
"""Local health checks for MasterBall Alerts."""

import argparse
import importlib
import json
import os
import sqlite3

from database import DB_PATH, init_db
from monitor import check_product
from product_utils import StockResult, normalize_url, retailer_from_url
from settings import load_config
from walmart_protected import (
    check_walmart_lightweight,
    validate_walmart_candidate,
    walmart_proxy_ready,
    walmart_settings_from_config,
)


REQUIRED_IMPORTS = [
    "requests",
    "bs4",
    "curl_cffi",
    "playwright.sync_api",
]

OPTIONAL_IMPORTS = [
    "tweepy",
    "camoufox.sync_api",
]


def ok(message):
    print(f"✅ {message}")


def warn(message):
    print(f"⚠️  {message}")


def fail(message):
    print(f"❌ {message}")


def check_imports():
    for module in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module)
            ok(f"Python package available: {module}")
        except Exception as e:
            fail(f"Missing required package {module}: {e}")

    for module in OPTIONAL_IMPORTS:
        try:
            importlib.import_module(module)
            ok(f"Optional package available: {module}")
        except Exception:
            warn(f"Optional package not installed: {module}")


def check_config():
    config = load_config()
    products = config.get("products", [])
    ok(f"Loaded config with {len(products)} seed products")
    if config.get("telegram_bot_token") and config.get("telegram_chat_id"):
        ok("Telegram owner credentials configured")
    else:
        warn("Telegram owner credentials missing; alerts/review commands will not send")
    if config.get("telegram_channel_id"):
        ok("Telegram channel configured")
    else:
        warn("Telegram channel missing; stock alerts will fall back to owner DM")
    return config


def check_database():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    required = {"stock_status", "alert_history", "discovery_candidates", "bot_state"}
    missing = required - tables
    if missing:
        fail(f"Database missing tables: {', '.join(sorted(missing))}")
    else:
        ok(f"SQLite ready: {DB_PATH}")


def check_playwright_browser():
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        ok("Playwright Chromium launches")
    except Exception as e:
        fail(f"Playwright Chromium launch failed: {e}")
        print("   Try: python -m playwright install chromium")


def check_walmart_setup(config, url=None):
    settings = walmart_settings_from_config(config)
    if settings.get("enabled", True):
        ok("Walmart protected lane enabled")
    else:
        warn("Walmart protected lane disabled")

    if walmart_proxy_ready():
        ok("Walmart proxy configured")
    else:
        warn("Walmart proxy missing; Walmart checks will stay blocked/idle until walmart_proxy.local.json is configured")
        return

    product_url = url
    if not product_url:
        for product in config.get("products", []):
            if "walmart.ca" in product.get("url", "").lower() and product.get("enabled", True):
                product_url = product["url"]
                break
    if not product_url:
        warn("No enabled Walmart product URL available for live check")
        return

    result = check_walmart_lightweight(product_url)
    print(json.dumps({
        "retailer": "walmart",
        "url": normalize_url(product_url),
        "lightweight_result": result.as_dict(),
    }, indent=2))
    candidate = {
        "retailer": "walmart",
        "name": "Pokemon TCG Walmart doctor product",
        "url": product_url,
    }
    valid, reason, validation_result = validate_walmart_candidate(candidate, config=config)
    print(json.dumps({
        "validation_passed": valid,
        "reason": reason,
        "validation_result": validation_result.as_dict() if validation_result else None,
    }, indent=2))


def check_retailer_smoke(config):
    seen = set()
    for product in config.get("products", []):
        if not product.get("enabled", True):
            continue
        retailer = retailer_from_url(product.get("url", ""))
        if retailer in seen:
            continue
        seen.add(retailer)
        try:
            result = check_product(product)
            if isinstance(result, bool):
                result = StockResult.in_stock() if result else StockResult.out_of_stock(reason="legacy boolean")
            print(json.dumps({
                "retailer": retailer,
                "product": product.get("name"),
                "url": normalize_url(product.get("url", "")),
                "result": result.as_dict(),
            }, indent=2))
        except Exception as e:
            warn(f"{retailer} smoke check failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Check local MasterBall Alerts setup.")
    parser.add_argument("--retailers", action="store_true", help="Run one live stock check per enabled retailer.")
    parser.add_argument("--walmart", action="store_true", help="Run Walmart proxy/protected-lane checks.")
    parser.add_argument("--walmart-url", default=None, help="Optional Walmart product URL for --walmart.")
    args = parser.parse_args()

    print("MasterBall Alerts Doctor")
    print("=" * 28)
    check_imports()
    config = check_config()
    check_database()
    check_playwright_browser()
    if args.walmart:
        check_walmart_setup(config, args.walmart_url)
    if args.retailers:
        check_retailer_smoke(config)
    else:
        print("Retailer live checks skipped. Use --retailers to run them.")


if __name__ == "__main__":
    main()
