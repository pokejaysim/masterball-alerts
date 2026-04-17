#!/usr/bin/env python3
"""
MasterBall Alerts — Market Price Sync
Fetches secondary market prices from PriceCharting and converts to CAD.
Run daily via cron.
"""

import urllib.request
import urllib.parse
import json
import os
import time
from datetime import datetime

from settings import load_config, repo_path

MARKET_PRICES_FILE = repo_path("market_prices.json")

# Product name (as used in config.json) → PriceCharting product ID
# Only products with confirmed accurate matches
PRODUCT_MAP = {
    # Prismatic Evolutions
    "PE ETB - Amazon.ca": 8256647,
    "PE ETB - Walmart.ca": 8256647,
    "PE ETB - Best Buy": 8256647,
    "PE ETB - EB Games": 8256647,
    "PE Pokemon Center ETB - Amazon.ca": 8256647,
    "PE Booster Bundle - Amazon.ca": 8256648,
    "PE Booster Bundle - Walmart.ca": 8256648,
    "PE Booster Bundle - Best Buy": 8256648,
    "PE Binder Collection - Amazon.ca": 8256649,
    "PE Binder Collection - Walmart.ca": 8256649,
    "PE Binder Collection - Best Buy": 8256649,
    "PE Surprise Box - Amazon.ca": 8256651,
    "PE Surprise Box - Walmart.ca": 8256651,
    "PE Surprise Box - Best Buy": 8256651,

    "PE Tech Sticker Collection - Amazon.ca": 8256653,
    "PE Tech Sticker Collection - Walmart.ca": 8256653,
    "PE Tech Sticker Collection - Best Buy": 8256653,
    # Costco bundles (use ETB price as proxy since bundles include ETB)
    "PE SPC + ETB Bundle - Costco.ca": 8256647,
    "PE ETB 2-Pack - Costco.ca": 8256647,
}

# Static manual prices for products NOT on PriceCharting (in CAD)
# Update these manually as needed
STATIC_PRICES_CAD = {
    "AH ETB - Amazon.ca": 120,
    "AH ETB - Walmart.ca": 120,
    "AH ETB - Best Buy": 120,
    "AH ETB - EB Games": 120,
    "AH Tech Sticker Collection - Amazon.ca": 40,
    "AH Tech Sticker Collection - Walmart.ca": 40,
    "AH Tech Sticker - EB Games": 40,
    "AH Booster Pack - Amazon.ca": 8,
    "AH Mini Tin - Amazon.ca": 15,
    "AH Mini Tin - EB Games": 15,
    "First Partner Illustration Collection Series 1 - Amazon.ca": 110,
    "First Partner Illustration Series 1 - Walmart.ca": 110,
    # PE SPC not on PriceCharting correctly — manual based on eBay sold
    "PE SPC - Amazon.ca": 250,
    "PE SPC - Walmart.ca": 250,
    "PE SPC - Best Buy": 250,
    # PE additional products
    "PE Accessory Pouch - Amazon.ca": 80,
    "PE Accessory Pouch - Walmart.ca": 80,
    "PE Booster Pack - Amazon.ca": 8,
    "PE Booster Bundle - EB Games": 102,
    "PE Two-Booster Blister - Amazon.ca": 15,
    "PE Mini Tin - EB Games": 20,
    "PE Mini Tin Espeon - Best Buy": 20,
    "PE Poster Collection - Best Buy": 55,
    "PE Poster Collection - Walmart.ca": 55,
    "PE Premium Figure Collection - Amazon.ca": 80,
    "PE Premium Figure Collection - EB Games": 80,
    "PE Premium Figure Collection - Walmart.ca": 80,
    "PE Lucario/Tyranitar Premium Collection - Amazon.ca": 100,
    "PE Surprise Box - EB Games": 78,
    "PE ETB + Booster Box Bundle - Costco.ca": 500,
    # AH additional products
    "AH 2-Pack Blister - Amazon.ca": 15,
    "AH 2-Pack Blister - Walmart.ca": 15,
    "AH 2-Pack Blister - EB Games": 15,
    "AH Booster Bundle - Amazon.ca": 55,
    "AH Deluxe Pin Collection - EB Games": 50,
    "AH First Partners Deluxe Pin Collection - Walmart.ca": 50,
    "AH Poster Collection - EB Games": 40,
    "AH Premium Poster Collection (Mega Lucario) - Amazon.ca": 55,
    "AH Premium Poster Collection - Walmart.ca": 55,
    # Other
    "Mega Charizard X ex UPC - Costco.ca": 200,
    "Topps Basketball Hanger Box - Walmart.ca": 30,
    "Topps Basketball Mega Box - Walmart.ca": 50,
    "Topps Basketball Value Box - Walmart.ca": 25,
}


def get_usd_to_cad_rate():
    """Fetch current USD to CAD exchange rate"""
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.Request(url, headers={'User-Agent': 'MasterBall/1.0'})
        response = urllib.request.urlopen(req, timeout=10)
        data = json.loads(response.read())
        if data.get('result') == 'success':
            rate = data['rates'].get('CAD', 1.44)
            print(f"✅ Exchange rate: 1 USD = {rate:.4f} CAD")
            return rate
    except Exception as e:
        print(f"⚠️  Exchange rate fetch failed: {e}, using 1.44")
    return 1.44  # Fallback


def get_pricecharting_token():
    token = os.environ.get("PRICECHARTING_TOKEN", "").strip()
    if token:
        return token

    try:
        return str(load_config().get("pricecharting_token", "")).strip()
    except FileNotFoundError:
        return ""


def fetch_pricecharting(product_id, token):
    """Fetch price from PriceCharting API"""
    try:
        url = f"https://www.pricecharting.com/api/product?t={token}&id={product_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'MasterBall/1.0'})
        response = urllib.request.urlopen(req, timeout=10)
        data = json.loads(response.read())
        
        if data.get('status') == 'success':
            # Use loose-price (most common for sealed products)
            loose = data.get('loose-price', 0)
            if loose:
                return loose / 100  # Convert from pennies to dollars
    except Exception as e:
        print(f"⚠️  PriceCharting error for ID {product_id}: {e}")
    return None


def load_existing_prices():
    """Load existing market prices file"""
    if os.path.exists(MARKET_PRICES_FILE):
        with open(MARKET_PRICES_FILE) as f:
            return json.load(f)
    return {}


def sync_prices():
    """Main sync function — fetches all prices and saves to JSON"""
    print(f"🟣 MasterBall Market Price Sync — {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    print("=" * 50)
    
    # Get exchange rate
    usd_to_cad = get_usd_to_cad_rate()
    pricecharting_token = get_pricecharting_token()

    # Fetch PriceCharting prices (deduplicate IDs to minimize API calls)
    unique_ids = set(PRODUCT_MAP.values())
    pc_prices_usd = {}

    if pricecharting_token:
        print(f"\n📊 Fetching {len(unique_ids)} unique products from PriceCharting...")
        for product_id in unique_ids:
            price = fetch_pricecharting(product_id, pricecharting_token)
            if price:
                pc_prices_usd[product_id] = price
                print(f"  ✅ ID {product_id}: ${price:.2f} USD (${price * usd_to_cad:.2f} CAD)")
            else:
                print(f"  ❌ ID {product_id}: No price data")
            time.sleep(1.1)  # Rate limit: 1 call/second
    else:
        print("\n⚠️  No PriceCharting token configured; using manual market prices only.")
    
    # Build market prices dict
    market_prices = {
        "_meta": {
            "updated": datetime.now().isoformat(),
            "usd_to_cad": usd_to_cad,
            "source": "PriceCharting + manual"
        }
    }
    
    # Add PriceCharting prices (converted to CAD)
    for product_name, product_id in PRODUCT_MAP.items():
        usd_price = pc_prices_usd.get(product_id)
        if usd_price:
            cad_price = round(usd_price * usd_to_cad, 2)
            market_prices[product_name] = {
                "market_cad": cad_price,
                "market_usd": usd_price,
                "source": "pricecharting",
                "pc_id": product_id
            }
    
    # Add static/manual prices
    for product_name, cad_price in STATIC_PRICES_CAD.items():
        market_prices[product_name] = {
            "market_cad": cad_price,
            "market_usd": round(cad_price / usd_to_cad, 2),
            "source": "manual"
        }
    
    # Save
    with open(MARKET_PRICES_FILE, 'w') as f:
        json.dump(market_prices, f, indent=2)
    
    total = len([k for k in market_prices if not k.startswith('_')])
    print(f"\n✅ Saved {total} product prices to {MARKET_PRICES_FILE}")
    print(f"📁 File: {MARKET_PRICES_FILE}")
    
    return market_prices


if __name__ == "__main__":
    sync_prices()
