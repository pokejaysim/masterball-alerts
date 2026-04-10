#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import json
import os
import re
import random
import time
from datetime import datetime
from statistics import median

# Configuration
MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(MONITOR_DIR, "config.json")
MARKET_FILE = os.path.join(MONITOR_DIR, "market_prices.json")

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1',
]

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

def get_ebay_sold_prices(product_name):
    log(f"🔍 Fetching market prices for: {product_name}...")
    
    # Search query: Product name + " pokemon card" + filter for Sold listings
    search_query = f"{product_name} pokemon card".replace(" ", "+")
    url = f"https://www.ebay.com/sch/i.html?_from=R40&_nkw={search_query}&rt=rt_Sold&_H_Sellers=1"
    
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            log(f"  ⚠️  eBay returned {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # eBay sold prices are typically in spans with class 's-item__price'
        # We want to find the price containers and clean the text
        price_elements = soup.find_all('span', class_='s-item__price')
        prices = []
        
        for el in price_elements:
            text = el.get_text()
            # Handle ranges like "$10.00 to $20.00" or "$15.00"
            # Extract all numbers that look like prices
            matches = re.findall(r'\$(\d+(?:\.\d{2})?)', text)
            if matches:
                # If it's a range, take the average of the range. Otherwise take the single price.
                vals = [float(m) for m in matches]
                prices.append(sum(vals) / len(vals))
        
        # Filter out outliers (eBay sometimes has 0.01 or 99999 listings)
        if not prices:
            log(f"  ❌ No sold prices found for {product_name}")
            return None
            
        # Sort and take the middle 3-5 for a robust median
        prices.sort()
        if len(prices) > 5:
            # Trim extremes
            trimmed = prices[len(prices)//4 : (3*len(prices)//4)]
            market_val = median(trimmed)
        else:
            market_val = median(prices)
            
        log(f"  ✅ Found {len(prices)} sold items. Market Value: ${market_val:.2f}")
        return market_val

    except Exception as e:
        log(f"  ❌ Error scraping {product_name}: {e}")
        return None

def main():
    if not os.path.exists(CONFIG_FILE):
        log(f"❌ Config file not found at {CONFIG_FILE}")
        return

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    products = config.get('products', [])
    if not products:
        log("❌ No products found in config.")
        return

    market_data = {}
    
    for p in products:
        name = p['name']
        # We only sync enabled products to save requests
        if p.get('enabled', True):
            val = get_ebay_sold_prices(name)
            if val:
                market_data[name] = {
                    "market_cad": round(val, 2),
                    "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            time.sleep(random.uniform(2, 5)) # Be nice to eBay

    with open(MARKET_FILE, 'w') as f:
        json.dump(market_data, f, indent=2)
    
    log(f"🎉 Market sync complete. Updated {len(market_data)} products.")

if __name__ == "__main__":
    main()
