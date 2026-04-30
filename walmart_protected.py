#!/usr/bin/env python3
"""Protected Walmart.ca helpers for discovery validation and stock checks."""

import json
import re
from urllib.parse import urlparse

from product_utils import (
    STOCK_BLOCKED,
    STOCK_IN,
    STOCK_MARKETPLACE,
    STOCK_OUT,
    STOCK_PREORDER,
    STOCK_UNKNOWN,
    StockResult,
    is_pokemon_tcg_sealed_candidate,
    product_identifier,
)
from settings import load_config, load_json_with_local_override


TRUSTED_WALMART_SELLERS = ("walmart", "walmart canada", "walmart.ca")

DEFAULT_WALMART_CONFIG = {
    "enabled": True,
    "auto_add_after_validation": True,
    "coverage": "all_tcg",
    "require_trusted_seller": True,
    "require_proxy": True,
    "lightweight_interval_minutes": 10,
    "browser_confirm_interval_minutes": 30,
    "max_browser_pages": 1,
    "max_products_per_cycle": 3,
    "block_backoff_minutes": 30,
    "owner_degraded_alerts": True,
}

WALMART_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def walmart_settings_from_config(config=None):
    if config is None:
        config = load_config()
    settings = dict(DEFAULT_WALMART_CONFIG)
    settings.update(config.get("walmart", {}) or {})
    settings["lightweight_interval_seconds"] = max(60, int(settings["lightweight_interval_minutes"]) * 60)
    settings["browser_confirm_interval_seconds"] = max(300, int(settings["browser_confirm_interval_minutes"]) * 60)
    settings["block_backoff_seconds"] = max(300, int(settings["block_backoff_minutes"]) * 60)
    settings["max_products_per_cycle"] = max(1, int(settings.get("max_products_per_cycle", 3)))
    return settings


def walmart_proxy_settings():
    try:
        return load_json_with_local_override("walmart_proxy.json", {})
    except Exception:
        return {}


def walmart_proxy_ready():
    proxy = walmart_proxy_settings()
    return bool(proxy.get("enabled") and proxy.get("proxy_url"))


def walmart_cffi_proxies():
    proxy = walmart_proxy_settings()
    if not (proxy.get("enabled") and proxy.get("proxy_url")):
        return None
    return {"http": proxy["proxy_url"], "https": proxy["proxy_url"]}


def walmart_playwright_proxy_config():
    proxy = walmart_proxy_settings()
    proxy_url = proxy.get("proxy_url", "") if proxy.get("enabled") else ""
    match = re.match(r"^(https?://)?([^:]+):([^@]+)@([^:]+):(\d+)$", proxy_url)
    if not match:
        return None
    scheme = match.group(1) or "http://"
    return {
        "server": f"{scheme}{match.group(4)}:{match.group(5)}",
        "username": match.group(2),
        "password": match.group(3),
    }


def is_trusted_walmart_seller(seller):
    seller_text = str(seller or "").lower()
    return bool(seller_text and any(token in seller_text for token in TRUSTED_WALMART_SELLERS))


def has_walmart_product_id(url):
    return bool(product_identifier(url))


def _json_blob_from_html(html):
    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.DOTALL)
    if not match:
        return None
    try:
        return json.dumps(json.loads(match.group(1)))
    except json.JSONDecodeError:
        return None


def _extract_value(data_str, key):
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', data_str or "")
    return match.group(1) if match else None


def _extract_price(data_str):
    patterns = [
        r'"currentPrice"\s*:\s*\{[^{}]*"price"\s*:\s*([0-9.]+)',
        r'"price"\s*:\s*([0-9.]+)',
        r'"displayPrice"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, data_str or "")
        if match:
            return match.group(1)
    return None


def parse_walmart_stock_html(html):
    text = html or ""
    lowered = text.lower()
    if not text:
        return StockResult.blocked(reason="empty Walmart response")
    if len(text) < 15000 and ("captcha" in lowered or "px-captcha" in lowered):
        return StockResult.blocked(reason="Walmart CAPTCHA")

    data_str = _json_blob_from_html(text)
    if data_str:
        seller = _extract_value(data_str, "sellerName")
        price = _extract_price(data_str)
        availability = _extract_value(data_str, "availabilityStatus")
        availability_text = str(availability or "").upper()

        if availability_text == "IN_STOCK":
            if seller and not is_trusted_walmart_seller(seller):
                return StockResult.marketplace(price=price, seller=seller, reason="marketplace seller")
            if seller and is_trusted_walmart_seller(seller):
                return StockResult.in_stock(price=price, seller=seller, reason="walmart metadata")
            return StockResult.unknown(price=price, reason="No trusted Walmart seller detected")

        if availability_text in {"OUT_OF_STOCK", "UNAVAILABLE", "NOT_AVAILABLE"}:
            return StockResult.out_of_stock(price=price, seller=seller, reason=availability_text.lower())

        if availability_text in {"PRE_ORDER", "PREORDER"}:
            return StockResult.preorder(price=price, reason=availability_text.lower())

        if seller and not is_trusted_walmart_seller(seller):
            return StockResult.marketplace(price=price, seller=seller, reason="marketplace seller")

    if "add to cart" in lowered and not any(token in lowered for token in ["marketplace seller", "sold by third-party"]):
        return StockResult.unknown(reason="Walmart add-to-cart signal needs seller confirmation")
    if any(token in lowered for token in ["out of stock", "currently unavailable", "not available"]):
        return StockResult.out_of_stock(reason="rendered out-of-stock signal")
    if "pre-order" in lowered or "preorder" in lowered:
        return StockResult.preorder(reason="rendered preorder signal")
    return StockResult.unknown(reason="Walmart stock metadata not found")


def fetch_walmart_html(url, timeout=15, allow_direct=False):
    proxies = walmart_cffi_proxies()
    if not proxies and not allow_direct:
        return None, "Walmart proxy not configured"
    try:
        from curl_cffi import requests as cffi_requests

        response = cffi_requests.get(
            url,
            impersonate="chrome131",
            headers=WALMART_HEADERS,
            proxies=proxies,
            timeout=timeout,
        )
        return response, None
    except Exception as exc:
        return None, f"Walmart proxy request failed: {exc}"


def check_walmart_lightweight(url, timeout=15, allow_direct=False):
    response, error = fetch_walmart_html(url, timeout=timeout, allow_direct=allow_direct)
    if error:
        return StockResult.blocked(reason=error)
    if not response:
        return StockResult.blocked(reason="Walmart no response")
    if response.status_code in (402, 403, 429, 503, 520, 530):
        return StockResult.blocked(reason=f"Walmart returned {response.status_code}")
    if response.status_code != 200:
        return StockResult.unknown(reason=f"Walmart returned {response.status_code}")
    return parse_walmart_stock_html(response.text)


def validate_walmart_candidate(candidate, config=None, checker=None):
    settings = walmart_settings_from_config(config)
    name = candidate.get("name", "")
    url = candidate.get("url", "")

    if not settings.get("enabled", True):
        return False, "walmart validation disabled", StockResult.blocked(reason="walmart disabled")
    if urlparse(url).netloc and "walmart.ca" not in urlparse(url).netloc.lower():
        return False, "walmart validation skipped: not Walmart", StockResult.unknown(reason="not Walmart")
    if not has_walmart_product_id(url):
        return False, "walmart validation failed: missing product id", StockResult.unknown(reason="missing product id")
    if not is_pokemon_tcg_sealed_candidate(name, url):
        return False, "walmart validation failed: not Pokemon TCG sealed", StockResult.unknown(reason="not Pokemon TCG sealed")

    checker = checker or check_walmart_lightweight
    result = checker(url)
    if result.status in {STOCK_IN, STOCK_OUT, STOCK_PREORDER}:
        return True, f"walmart validation passed: {result.status}", result
    if result.status == STOCK_MARKETPLACE:
        seller = f" ({result.seller})" if result.seller else ""
        return False, f"walmart validation marketplace seller{seller}", result
    if result.status == STOCK_BLOCKED:
        return False, f"walmart validation blocked: {result.reason or 'blocked'}", result
    if result.status == STOCK_UNKNOWN:
        return False, f"walmart validation unknown: {result.reason or 'unknown'}", result
    return False, f"walmart validation failed: {result.status}", result

