#!/usr/bin/env python3
"""Discover new Canada-first Pokemon TCG sealed products for review."""

import argparse
import json
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from database import (
    add_or_update_candidate,
    expire_old_candidates,
    get_approved_products,
    init_db,
    set_candidate_status,
)
from product_utils import (
    candidate_id,
    default_priority,
    escape_html,
    is_pokemon_tcg_sealed_candidate,
    normalize_url,
    product_identifier,
    product_name_for_candidate,
    retailer_display_name,
    retailer_from_url,
)
from settings import load_config


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_AUTO_APPROVE_RETAILERS = {"walmart", "costco", "bestbuy", "ebgames"}
DEFAULT_AUTO_APPROVE_MIN_CONFIDENCE = 0.82


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def send_telegram(bot_token, chat_id, message):
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
    except Exception as e:
        log(f"  ⚠️ Telegram review message failed: {e}")


def fetch(url, prefer_cffi=False, timeout=20):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-CA,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if prefer_cffi:
        try:
            from curl_cffi import requests as cffi_requests

            return cffi_requests.get(url, impersonate="chrome131", headers=headers, timeout=timeout)
        except Exception:
            pass
    return requests.get(url, headers=headers, timeout=timeout)


def html_links(html, base_url, include_hosts=None):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"]).split("?")[0]
        if include_hosts and not any(host in href for host in include_hosts):
            continue
        text = anchor.get_text(" ", strip=True)
        links.append((href, text))
    return links


def build_candidate(url, raw_name="", source="", confidence=0.7, reason=None):
    retailer = retailer_from_url(url)
    if retailer == "generic":
        return None
    name = product_name_for_candidate(raw_name, url)
    if not is_pokemon_tcg_sealed_candidate(name, url):
        return None

    normalized = normalize_url(url)
    return {
        "id": candidate_id(normalized),
        "retailer": retailer,
        "name": name,
        "url": normalized,
        "product_id": product_identifier(normalized),
        "source": source,
        "confidence": confidence,
        "status": "pending",
        "priority": default_priority(name, normalized, source),
        "reason": reason,
    }


def dedupe(candidates):
    seen = set()
    unique = []
    for candidate in candidates:
        if not candidate:
            continue
        key = normalize_url(candidate["url"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def discover_walmart():
    urls = [
        "https://www.walmart.ca/en/browse/toys/trading-cards/pokemon-cards/10011_31745_6000204969672",
        "https://www.walmart.ca/search?q=pokemon%20tcg",
        "https://www.walmart.ca/search?q=pokemon%20booster%20bundle",
        "https://www.walmart.ca/search?q=pokemon%20elite%20trainer%20box",
    ]
    candidates = []
    for page_url in urls:
        try:
            response = fetch(page_url, prefer_cffi=True)
            if response.status_code != 200:
                log(f"  ⚠️ Walmart discovery status {response.status_code}: {page_url}")
                continue
            html = response.text
            for href, text in html_links(html, page_url, include_hosts=["walmart.ca"]):
                if "/en/ip/" in href:
                    candidates.append(build_candidate(href, text, "walmart_search", 0.78))

            json_match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if json_match:
                data_str = json.dumps(json.loads(json_match.group(1)))
                for path in set(re.findall(r'(/en/ip/[^"?#]+)', data_str)):
                    candidates.append(build_candidate(urljoin("https://www.walmart.ca", path), "", "walmart_next_data", 0.82))
        except Exception as e:
            log(f"  ⚠️ Walmart discovery error: {e}")
        time.sleep(1)
    return dedupe(candidates)


def discover_costco():
    urls = [
        "https://www.costco.ca/CatalogSearch?keyword=pokemon+tcg",
        "https://www.costco.ca/CatalogSearch?keyword=pokemon+cards",
        "https://www.costco.ca/CatalogSearch?keyword=pokemon+elite+trainer",
    ]
    candidates = []
    for page_url in urls:
        try:
            response = fetch(page_url, prefer_cffi=True)
            if response.status_code != 200:
                log(f"  ⚠️ Costco discovery status {response.status_code}: {page_url}")
                continue
            for href, text in html_links(response.text, page_url, include_hosts=["costco.ca"]):
                if "/p/" in href or ".product." in href:
                    candidates.append(build_candidate(href, text, "costco_search", 0.82))
        except Exception as e:
            log(f"  ⚠️ Costco discovery error: {e}")
        time.sleep(1)
    return dedupe(candidates)


def discover_bestbuy():
    urls = [
        "https://www.bestbuy.ca/en-ca/search?search=pokemon%20tcg",
        "https://www.bestbuy.ca/en-ca/search?search=pokemon%20elite%20trainer%20box",
        "https://www.bestbuy.ca/en-ca/search?search=pokemon%20booster%20bundle",
    ]
    candidates = []
    for page_url in urls:
        try:
            response = fetch(page_url)
            if response.status_code != 200:
                log(f"  ⚠️ Best Buy discovery status {response.status_code}: {page_url}")
                continue
            for href, text in html_links(response.text, page_url, include_hosts=["bestbuy.ca"]):
                if "/en-ca/product/" in href:
                    candidates.append(build_candidate(href, text, "bestbuy_search", 0.88))
        except Exception as e:
            log(f"  ⚠️ Best Buy discovery error: {e}")
        time.sleep(1)
    return dedupe(candidates)


def discover_ebgames():
    urls = [
        "https://www.ebgames.ca/SearchResult/QuickSearch?q=pokemon%20tcg",
        "https://www.ebgames.ca/SearchResult/QuickSearch?q=pokemon%20trading%20card",
        "https://www.gamestop.ca/SearchResult/QuickSearch?q=pokemon%20tcg",
    ]
    candidates = []
    for page_url in urls:
        try:
            response = fetch(page_url, prefer_cffi=True)
            if response.status_code != 200:
                log(f"  ⚠️ EB Games discovery status {response.status_code}: {page_url}")
                continue
            for href, text in html_links(response.text, page_url, include_hosts=["ebgames.ca", "gamestop.ca"]):
                if "/Games/" in href or "/Trading%20Cards/" in href:
                    candidates.append(build_candidate(href, text, "ebgames_search", 0.82))
        except Exception as e:
            log(f"  ⚠️ EB Games discovery error: {e}")
        time.sleep(1)
    return dedupe(candidates)


def discover_amazon():
    urls = [
        "https://www.amazon.ca/s?k=pokemon+tcg+elite+trainer+box",
        "https://www.amazon.ca/s?k=pokemon+tcg+booster+bundle",
        "https://www.amazon.ca/s?k=pokemon+tcg+ultra+premium+collection",
    ]
    candidates = []
    for page_url in urls:
        try:
            response = fetch(page_url)
            if response.status_code != 200:
                log(f"  ⚠️ Amazon discovery status {response.status_code}: {page_url}")
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for item in soup.select("[data-asin]"):
                asin = item.get("data-asin", "").strip()
                if not re.fullmatch(r"[A-Z0-9]{10}", asin):
                    continue
                title_el = item.select_one("h2 span")
                title = title_el.get_text(" ", strip=True) if title_el else f"Pokemon TCG {asin}"
                candidates.append(build_candidate(f"https://www.amazon.ca/dp/{asin}", title, "amazon_search", 0.65))
        except Exception as e:
            log(f"  ⚠️ Amazon discovery error: {e}")
        time.sleep(1)
    return dedupe(candidates)


def discover_pokemon_center():
    urls = [
        "https://www.pokemoncenter.com/en-ca/category/trading-card-game?category=tcg-cards",
        "https://www.pokemoncenter.com/en-ca/search/pokemon%20tcg",
    ]
    candidates = []
    for page_url in urls:
        try:
            response = fetch(page_url)
            if response.status_code != 200:
                log(f"  ⚠️ Pokemon Center discovery status {response.status_code}: {page_url}")
                continue
            for href, text in html_links(response.text, page_url, include_hosts=["pokemoncenter.com"]):
                if "/en-ca/product/" in href:
                    candidates.append(build_candidate(href, text, "pokemon_center_discovery_only", 0.7, reason="Manual review only"))
        except Exception as e:
            log(f"  ⚠️ Pokemon Center discovery error: {e}")
        time.sleep(1)
    return dedupe(candidates)


DISCOVERERS = [
    ("Walmart", discover_walmart),
    ("Costco", discover_costco),
    ("Best Buy", discover_bestbuy),
    ("EB Games", discover_ebgames),
    ("Amazon", discover_amazon),
    ("Pokemon Center", discover_pokemon_center),
]


def existing_urls_from_config(config):
    urls = {normalize_url(p["url"]) for p in config.get("products", []) if p.get("url")}
    try:
        urls.update(normalize_url(p["url"]) for p in get_approved_products())
    except Exception:
        pass
    return urls


def send_review_messages(bot_token, chat_id, candidates):
    if not bot_token or not chat_id or not candidates:
        return
    batch = candidates[:10]
    lines = ["🔍 <b>New Pokemon TCG products found</b>", ""]
    for candidate in batch:
        lines.extend([
            f"<b>{candidate['id']}</b> | {retailer_display_name(candidate['retailer'])}",
            escape_html(candidate["name"]),
            f"{escape_html(candidate['priority'])} priority | confidence {candidate['confidence']:.2f}",
            escape_html(candidate["url"]),
            f"/approve {candidate['id']}  |  /ignore {candidate['id']}",
            "",
        ])
    if len(candidates) > len(batch):
        lines.append(f"...and {len(candidates) - len(batch)} more. Run /pending after reviewing these.")
    send_telegram(bot_token, chat_id, "\n".join(lines).strip())


def send_auto_approve_summary(bot_token, chat_id, approved):
    if not bot_token or not chat_id or not approved:
        return
    batch = approved[:10]
    lines = ["✅ <b>Auto-added Pokemon TCG products</b>", ""]
    for candidate in batch:
        lines.extend([
            f"<b>{candidate['id']}</b> | {retailer_display_name(candidate['retailer'])}",
            escape_html(candidate["name"]),
            f"{escape_html(candidate['priority'])} priority | confidence {float(candidate.get('confidence') or 0):.2f}",
            escape_html(candidate["url"]),
            "",
        ])
    if len(approved) > len(batch):
        lines.append(f"...and {len(approved) - len(batch)} more auto-added.")
    lines.append("These will be picked up by the monitor on its next product reload.")
    send_telegram(bot_token, chat_id, "\n".join(lines).strip())


def _parse_retailers(value):
    if value is None:
        return set(DEFAULT_AUTO_APPROVE_RETAILERS)
    if isinstance(value, str):
        return {part.strip().lower() for part in value.split(",") if part.strip()}
    return {str(part).strip().lower() for part in value if str(part).strip()}


def should_auto_approve(candidate, min_confidence, retailers):
    if candidate["retailer"] not in retailers:
        return False
    if float(candidate.get("confidence") or 0) < float(min_confidence):
        return False
    if candidate["retailer"] == "pokemoncenter":
        return False
    return True


def run_discovery(
    dry_run=False,
    send_review=True,
    print_limit=50,
    auto_approve=None,
    auto_min_confidence=None,
    auto_retailers=None,
):
    config = load_config()
    discovery_config = config.get("discovery", {})
    if auto_approve is None:
        auto_approve = bool(discovery_config.get("auto_approve", False))
    if auto_min_confidence is None:
        auto_min_confidence = discovery_config.get(
            "auto_approve_min_confidence",
            DEFAULT_AUTO_APPROVE_MIN_CONFIDENCE,
        )
    if auto_retailers is None:
        auto_retailers = discovery_config.get("auto_approve_retailers")
    auto_retailers = _parse_retailers(auto_retailers)

    if not dry_run:
        init_db()
        expire_old_candidates()
    existing_urls = existing_urls_from_config(config)

    all_candidates = []
    for label, discoverer in DISCOVERERS:
        log(f"Scanning {label}...")
        candidates = discoverer()
        log(f"  {label}: {len(candidates)} candidate products")
        all_candidates.extend(candidates)

    all_candidates = dedupe(all_candidates)
    review_candidates = [c for c in all_candidates if normalize_url(c["url"]) not in existing_urls]
    new_candidates = []
    auto_approved = []

    if dry_run:
        shown = review_candidates[:print_limit]
        log(f"Dry run found {len(review_candidates)} review candidates; showing {len(shown)}:")
        for candidate in shown:
            log(f"  {candidate['id']} | {candidate['name']} | {candidate['url']}")
        if len(review_candidates) > len(shown):
            log(f"  ...{len(review_candidates) - len(shown)} more hidden. Use --limit to show more.")
        if auto_approve:
            would_auto = [
                c for c in review_candidates
                if should_auto_approve(c, auto_min_confidence, auto_retailers)
            ]
            log(
                f"  Auto-add mode would approve {len(would_auto)} candidates "
                f"(min confidence {float(auto_min_confidence):.2f}; retailers {', '.join(sorted(auto_retailers))})."
            )
        return review_candidates

    for candidate in review_candidates:
        stored, inserted = add_or_update_candidate(candidate)
        if stored.get("status") == "pending" and auto_approve and should_auto_approve(stored, auto_min_confidence, auto_retailers):
            approved = set_candidate_status(stored["id"], "approved", reason="Auto-approved by discovery")
            if approved:
                auto_approved.append(approved)
            continue
        if inserted and stored.get("status") == "pending":
            new_candidates.append(stored)

    log("Discovery complete")
    log(f"  Total candidates: {len(all_candidates)}")
    log(f"  Not already monitored: {len(review_candidates)}")
    log(f"  Auto-approved: {len(auto_approved)}")
    log(f"  Newly queued: {len(new_candidates)}")

    if send_review and auto_approved:
        send_auto_approve_summary(
            config.get("telegram_bot_token", ""),
            config.get("telegram_chat_id", ""),
            auto_approved,
        )
        log("  Telegram auto-add summary sent")

    if send_review and new_candidates:
        send_review_messages(
            config.get("telegram_bot_token", ""),
            config.get("telegram_chat_id", ""),
            new_candidates,
        )
        log("  Telegram review message sent")

    return auto_approved + new_candidates


def main():
    parser = argparse.ArgumentParser(description="Discover new Pokemon TCG products for review.")
    parser.add_argument("--dry-run", action="store_true", help="Print candidates without writing the queue or sending Telegram.")
    parser.add_argument("--no-telegram", action="store_true", help="Do not send Telegram review messages.")
    parser.add_argument("--limit", type=int, default=50, help="Dry-run candidate print limit.")
    parser.add_argument("--auto-approve", action="store_true", help="Automatically approve high-confidence candidates.")
    parser.add_argument("--auto-min-confidence", type=float, default=None, help="Minimum confidence for auto-approval.")
    parser.add_argument("--auto-retailers", default=None, help="Comma-separated retailers allowed for auto-approval.")
    args = parser.parse_args()
    run_discovery(
        dry_run=args.dry_run,
        send_review=not args.no_telegram,
        print_limit=args.limit,
        auto_approve=args.auto_approve or None,
        auto_min_confidence=args.auto_min_confidence,
        auto_retailers=args.auto_retailers,
    )


if __name__ == "__main__":
    main()
