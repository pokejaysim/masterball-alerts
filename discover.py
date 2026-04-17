#!/usr/bin/env python3
"""
MasterBall Alerts — New Product Discovery
Scans Walmart.ca and Costco.ca for new Pokemon TCG listings
and alerts via Telegram when new products are found.
"""

import json
import os
import re
import time
import requests
from datetime import datetime

from settings import MONITOR_DIR, load_config

KNOWN_FILE = os.path.join(MONITOR_DIR, "known_products.json")

def load_known():
    if os.path.exists(KNOWN_FILE):
        with open(KNOWN_FILE) as f:
            return json.load(f)
    return {"urls": []}

def save_known(data):
    with open(KNOWN_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def send_telegram(bot_token, chat_id, message):
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(url, data={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML', 'disable_web_page_preview': False}, timeout=10)
    except:
        pass

# ── Walmart.ca Discovery ──

def discover_walmart():
    """Scan Walmart.ca Pokemon TCG category pages for products."""
    from curl_cffi import requests as cffi_requests

    category_urls = [
        "https://www.walmart.ca/en/browse/toys/trading-cards/pokemon-cards/10011_31745_6000204969672",
        "https://www.walmart.ca/en/browse/toys/trading-cards/pokemon-cards/pokemon-booster-blister-packs/10011_31745_6000204969672_6000203427077",
        "https://www.walmart.ca/en/browse/toys/trading-cards/pokemon-cards/pokemon-box-sets/10011_31745_6000204969672_6000204969783",
    ]

    products = []
    for cat_url in category_urls:
        try:
            r = cffi_requests.get(cat_url, impersonate="chrome131", timeout=15)
            if r.status_code != 200:
                continue

            html = r.text

            # Extract __NEXT_DATA__ for product listings
            json_match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                data_str = json.dumps(data)

                # Find product URLs - Walmart uses /en/ip/SLUG/PRODUCT_ID pattern
                url_matches = re.findall(r'/en/ip/[^"]+', data_str)
                for url_path in url_matches:
                    full_url = f"https://www.walmart.ca{url_path}"
                    # Clean tracking params
                    full_url = full_url.split('?')[0]
                    if full_url not in products:
                        products.append(full_url)

            # Also try extracting from raw HTML links
            link_matches = re.findall(r'href="(/en/ip/[^"?]+)"', html)
            for path in link_matches:
                full_url = f"https://www.walmart.ca{path}"
                if full_url not in products:
                    products.append(full_url)

        except Exception as e:
            log(f"  ⚠️ Walmart discovery error: {e}")

    # Filter to Pokemon-related only
    pokemon_products = [u for u in products if any(kw in u.lower() for kw in ['pokemon', 'pok-mon', 'pokmon', 'prismatic', 'ascended', 'tcg'])]

    log(f"  Walmart: Found {len(pokemon_products)} Pokemon products")
    return pokemon_products


def discover_costco():
    """Scan Costco.ca for Pokemon TCG products."""
    from curl_cffi import requests as cffi_requests

    search_urls = [
        "https://www.costco.ca/CatalogSearch?keyword=pokemon+tcg",
        "https://www.costco.ca/CatalogSearch?keyword=pokemon+trading+card",
    ]

    products = []
    for search_url in search_urls:
        try:
            r = cffi_requests.get(search_url, impersonate="chrome131", timeout=15)
            if r.status_code != 200:
                continue

            html = r.text

            # Costco product URLs: /p/-/product-name/PRODUCT_ID
            url_matches = re.findall(r'href="(/p/-/[^"]+)"', html)
            for path in url_matches:
                full_url = f"https://www.costco.ca{path}"
                full_url = full_url.split('?')[0]
                if full_url not in products and 'pokemon' in full_url.lower():
                    products.append(full_url)

            # Also check JSON-LD or data attributes
            url_matches2 = re.findall(r'"url"\s*:\s*"(https://www\.costco\.ca/p/-/[^"]+)"', html)
            for url in url_matches2:
                url = url.split('?')[0]
                if url not in products:
                    products.append(url)

        except Exception as e:
            log(f"  ⚠️ Costco discovery error: {e}")

    log(f"  Costco: Found {len(products)} Pokemon products")
    return products


def discover_amazon():
    """Scan Amazon.ca for new Pokemon TCG products via search."""
    search_urls = [
        "https://www.amazon.ca/s?k=pokemon+prismatic+evolutions&rh=n%3A110218011",
        "https://www.amazon.ca/s?k=pokemon+ascended+heroes+tcg",
    ]

    products = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-CA',
    }

    for search_url in search_urls:
        try:
            r = requests.get(search_url, headers=headers, timeout=15)
            if r.status_code != 200:
                continue

            # Extract ASINs from search results
            asins = re.findall(r'data-asin="([A-Z0-9]{10})"', r.text)
            for asin in set(asins):
                if asin:
                    url = f"https://www.amazon.ca/dp/{asin}"
                    if url not in products:
                        products.append(url)
        except Exception as e:
            log(f"  ⚠️ Amazon discovery error: {e}")

    log(f"  Amazon: Found {len(products)} products")
    return products


def run_discovery():
    """Main discovery loop."""
    config = load_config()
    bot_token = config.get('telegram_bot_token', '')
    chat_id = config.get('telegram_chat_id', '')

    # Get existing monitored URLs
    existing_urls = set()
    for p in config.get('products', []):
        # Normalize URL — strip tracking params
        url = p['url'].split('?')[0]
        existing_urls.add(url)

    known = load_known()
    known_urls = set(known.get('urls', []))

    log("🔍 MasterBall Product Discovery Started")
    log(f"  Existing monitored: {len(existing_urls)} products")
    log(f"  Previously known: {len(known_urls)} URLs")

    # Discover from each retailer
    all_discovered = []

    log("Scanning Walmart.ca...")
    all_discovered.extend([(u, "Walmart.ca") for u in discover_walmart()])

    time.sleep(2)

    log("Scanning Costco.ca...")
    all_discovered.extend([(u, "Costco.ca") for u in discover_costco()])

    time.sleep(2)

    log("Scanning Amazon.ca...")
    all_discovered.extend([(u, "Amazon.ca") for u in discover_amazon()])

    # Find truly new products
    new_products = []
    for url, retailer in all_discovered:
        clean_url = url.split('?')[0]
        if clean_url not in existing_urls and clean_url not in known_urls:
            new_products.append((url, retailer))
            known_urls.add(clean_url)

    log(f"\n📊 Discovery Results:")
    log(f"  Total found: {len(all_discovered)}")
    log(f"  Already monitored: {len(all_discovered) - len(new_products)}")
    log(f"  🆕 NEW products: {len(new_products)}")

    # Alert on new products
    if new_products and bot_token and chat_id:
        msg = "🔍 <b>MasterBall — New Products Discovered!</b>\n\n"
        for url, retailer in new_products[:10]:  # Max 10 per alert
            msg += f"🆕 <b>{retailer}</b>\n{url}\n\n"
        msg += f"Total: {len(new_products)} new products found.\n"
        msg += "Reply to Peter on Discord to add them to monitoring!"
        send_telegram(bot_token, chat_id, msg)
        log("📱 Telegram alert sent!")

    # Save known URLs
    known['urls'] = list(known_urls)
    known['last_scan'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_known(known)

    log("✅ Discovery complete!")
    return new_products


if __name__ == "__main__":
    run_discovery()
