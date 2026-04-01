#!/usr/bin/env python3
"""
MasterBall Alerts — Pokemon TCG Restock Monitor v3
- Async parallel checking
- CAPTCHA fallback profiles
- Amazon UA rotation
- Alert deduplication (10 min cooldown)
- Per-product timestamps
- Health watchdog
"""

import requests
from bs4 import BeautifulSoup
import time
import subprocess
import json
import os
import re
import random
import hashlib
import signal
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import tweepy
except ImportError:
    tweepy = None

# Graceful shutdown
_shutdown = False
def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# Configuration
MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(MONITOR_DIR, "config.json")
STOCK_STATUS_FILE = os.path.join(MONITOR_DIR, "stock_status.json")
TIMESTAMPS_FILE = os.path.join(MONITOR_DIR, "check_timestamps.json")
ALERT_COOLDOWNS_FILE = os.path.join(MONITOR_DIR, "alert_cooldowns.json")

ALERT_COOLDOWN_SECONDS = 1800  # 30 minutes

# Import database layer
try:
    from database import (init_db, get_stock_status, set_stock_status,
                          add_alert, update_timestamp as db_update_timestamp,
                          check_cooldown as db_check_cooldown, set_cooldown as db_set_cooldown,
                          log_error as db_log_error, increment_daily_stat)
    USE_DB = True
except ImportError:
    USE_DB = False

# User-Agent rotation pool for Amazon
AMAZON_USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
]

# curl_cffi fallback profiles for CAPTCHA bypass
CFFI_PROFILES = ["chrome131", "chrome", "safari", "safari_ios"]

# Shared session
session = requests.Session()
session.headers.update({
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-CA,en;q=0.9',
})


def send_telegram(bot_token, chat_id, message, retries=2):
    for attempt in range(retries):
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML', 'disable_web_page_preview': False}
            response = requests.post(url, data=data, timeout=10)
            if response.status_code == 200:
                result = response.json()
                message_id = result.get('result', {}).get('message_id')
                log("✅ Telegram message sent")
                return message_id  # Return message_id for editing later
            else:
                log(f"⚠️  Telegram attempt {attempt+1} failed: {response.status_code}")
                if USE_DB:
                    db_log_error('telegram', 'send_failed', f"Status {response.status_code}")
        except Exception as e:
            log(f"⚠️  Telegram attempt {attempt+1} error: {e}")
            if USE_DB:
                db_log_error('telegram', 'exception', str(e))
        if attempt < retries - 1:
            time.sleep(1)
    log(f"❌ Telegram send failed after {retries} attempts")
    return None


def edit_telegram(bot_token, chat_id, message_id, new_text):
    """Edit an existing Telegram message"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
        data = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': new_text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            log(f"✅ Telegram message edited (ID: {message_id})")
            return True
        else:
            log(f"⚠️  Edit failed: {response.status_code}")
            return False
    except Exception as e:
        log(f"⚠️  Edit error: {e}")
        return False


def send_notification(title, message):
    try:
        escaped_msg = message.replace('"', '\\"').replace("'", "\\'")
        escaped_title = title.replace('"', '\\"').replace("'", "\\'")
        subprocess.run(['osascript', '-e', f'display notification "{escaped_msg}" with title "{escaped_title}" sound name "Glass"'], capture_output=True)
    except:
        pass


def twitter_post(product_name, url, price=None):
    """Post a restock tweet after a 3-minute delay (Telegram users get first dibs)."""
    if tweepy is None:
        log("⚠️  tweepy not installed — skipping Twitter post")
        return

    if 'amazon.ca' in url: retailer = 'Amazon CA'
    elif 'walmart.ca' in url: retailer = 'Walmart CA'
    elif 'bestbuy.ca' in url: retailer = 'Best Buy CA'
    elif 'costco.ca' in url: retailer = 'Costco CA'
    elif 'ebgames.ca' in url: retailer = 'EB Games CA'
    else: retailer = 'Canadian Retailer'

    def _post():
        try:
            env_file = os.path.join(MONITOR_DIR, '.env.twitter')
            keys = {}
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        k, v = line.split('=', 1)
                        keys[k.strip()] = v.strip()

            client = tweepy.Client(
                consumer_key=keys['TWITTER_CONSUMER_KEY'],
                consumer_secret=keys['TWITTER_CONSUMER_SECRET'],
                access_token=keys['TWITTER_ACCESS_TOKEN'],
                access_token_secret=keys['TWITTER_ACCESS_SECRET'],
            )

            name_part = product_name if len(product_name) <= 60 else product_name[:57] + '...'
            price_line = f"💰 ${str(price).lstrip('$')} CAD\n" if price else ""
            
            # Add market price comparison
            market_line = ""
            mp = _market_prices.get(product_name)
            if mp and mp.get('market_cad') and price:
                try:
                    retail_num = float(re.sub(r'[^\d.]', '', str(price)))
                    market_cad = mp['market_cad']
                    if market_cad > retail_num:
                        pct = int(((market_cad - retail_num) / market_cad) * 100)
                        market_line = f"📊 {pct}% below resale (~${market_cad:.0f} CAD)\n"
                except:
                    pass
            
            # Build set-specific hashtags
            hashtags = "#PokemonTCG"
            name_lower = product_name.lower()
            if 'prismatic' in name_lower or name_lower.startswith('pe '):
                hashtags += " #PrismaticEvolutions"
            elif 'astral haze' in name_lower or name_lower.startswith('ah '):
                hashtags += " #AstralHaze"
            elif 'topps' in name_lower:
                hashtags += " #ToppsSports"
            elif 'charizard' in name_lower:
                hashtags += " #Charizard"
            if 'etb' in name_lower:
                hashtags += " #ETB"
            hashtags += " #PokemonCanada"
            
            tweet = (
                f"🟣 {name_part} is IN STOCK at {retailer}! 🇨🇦\n\n"
                f"{price_line}"
                f"{market_line}"
                f"🔗 {url}\n\n"
                f"{hashtags}\n\n"
                f"⚡ Telegram users got this alert 3 min ago → masterballalerts.ca"
            )
            client.create_tweet(text=tweet)
            log(f"🐦 Tweet posted: {product_name}")
        except FileNotFoundError:
            log("⚠️  Twitter: .env.twitter not found — skipping")
        except Exception as e:
            log(f"⚠️  Twitter post failed: {e}")

    t = threading.Timer(180, _post)
    t.daemon = True
    t.start()
    log(f"🐦 Tweet queued (3 min delay): {product_name}")


def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return {}


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def check_alert_cooldown(product_name):
    if USE_DB:
        return db_check_cooldown(product_name, ALERT_COOLDOWN_SECONDS)
    cooldowns = load_json(ALERT_COOLDOWNS_FILE) if os.path.exists(ALERT_COOLDOWNS_FILE) else {}
    return (time.time() - cooldowns.get(product_name, 0)) > ALERT_COOLDOWN_SECONDS


def set_alert_cooldown(product_name):
    if USE_DB:
        db_set_cooldown(product_name)
        return
    cooldowns = load_json(ALERT_COOLDOWNS_FILE) if os.path.exists(ALERT_COOLDOWNS_FILE) else {}
    cooldowns[product_name] = time.time()
    save_json(ALERT_COOLDOWNS_FILE, cooldowns)


def update_timestamp(product_name):
    if USE_DB:
        db_update_timestamp(product_name)
        return
    timestamps = load_json(TIMESTAMPS_FILE) if os.path.exists(TIMESTAMPS_FILE) else {}
    timestamps[product_name] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(TIMESTAMPS_FILE, timestamps)


def take_screenshot(url, name):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = name.replace(' ', '_').replace('/', '-')[:40]
        filepath = os.path.join(MONITOR_DIR, f"screenshots/{safe_name}_{timestamp}.png")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(url, timeout=15000, wait_until='domcontentloaded')
            time.sleep(3)
            page.screenshot(path=filepath)
            browser.close()
        log(f"  📸 Screenshot saved: {filepath}")
    except Exception as e:
        log(f"  ⚠️  Screenshot failed: {e}")


# Track detected prices and "still live" follow-ups
_detected_prices = {}  # product_name -> price string
_followup_queue = []   # list of (check_time, product, url, alert_time) for "still live" follow-ups
_message_ids = {}      # product_name -> (channel_message_id, dm_message_id)
FOLLOWUP_DELAY = 150   # 2.5 minutes
FOLLOWUP_STATE_FILE = os.path.join(MONITOR_DIR, "followup_state.json")


def save_followup_state():
    """Persist followup queue and message IDs to disk"""
    try:
        state = {
            "queue": [],
            "message_ids": {}
        }
        for queued_time, product, url, alert_time in _followup_queue:
            state["queue"].append({
                "queued_time": queued_time,
                "product_name": product["name"],
                "product_url": product["url"],
                "url": url,
                "alert_time": alert_time
            })
        for name, (ch_id, dm_id) in _message_ids.items():
            state["message_ids"][name] = {"channel": ch_id, "dm": dm_id}
        with open(FOLLOWUP_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"⚠️  Failed to save followup state: {e}")


def load_followup_state(config):
    """Restore followup queue and message IDs from disk on startup"""
    global _followup_queue, _message_ids
    if not os.path.exists(FOLLOWUP_STATE_FILE):
        return
    try:
        with open(FOLLOWUP_STATE_FILE) as f:
            state = json.load(f)
        
        # Build product lookup from config
        products_by_name = {}
        for p in config.get("products", []):
            products_by_name[p["name"]] = p
        
        now = time.time()
        restored_queue = 0
        for item in state.get("queue", []):
            product_name = item["product_name"]
            if product_name in products_by_name:
                # Only restore if not too old (< 10 minutes)
                if now - item["queued_time"] < 600:
                    product = products_by_name[product_name]
                    _followup_queue.append((
                        item["queued_time"],
                        product,
                        item["url"],
                        item["alert_time"]
                    ))
                    restored_queue += 1
        
        for name, ids in state.get("message_ids", {}).items():
            # Only restore if not too old
            _message_ids[name] = (ids.get("channel"), ids.get("dm"))
        
        if restored_queue > 0 or state.get("message_ids"):
            log(f"📂 Restored {restored_queue} follow-ups and {len(_message_ids)} message IDs from disk")
        
        # Clean up the state file after loading
        os.remove(FOLLOWUP_STATE_FILE)
    except Exception as e:
        log(f"⚠️  Failed to load followup state: {e}")


def load_market_prices():
    """Load cached market prices"""
    mp_file = os.path.join(MONITOR_DIR, "market_prices.json")
    if os.path.exists(mp_file):
        try:
            with open(mp_file) as f:
                return json.load(f)
        except:
            pass
    return {}

_market_prices = load_market_prices()

def build_alert_message(product, price=None):
    name = product['name']
    url = product['url']

    if 'amazon.ca' in url:
        retailer = "Amazon CA"
        asin_match = re.search(r'/dp/([A-Z0-9]+)', url)
        asin = asin_match.group(1) if asin_match else 'N/A'
        cart_link = f"https://www.amazon.ca/gp/aws/cart/add.html?ASIN.1={asin}&amp;Quantity.1=1"
        links = f'<a href="{url}">Product Page</a> | <a href="{cart_link}">Add to Cart</a>'
        extra = f"\n<b>ASIN</b>\n{asin}\n\n<i>Note: May be Amazon Warehouse (damaged box, sealed product inside). Check listing before purchasing.</i>"
    elif 'bestbuy.ca' in url:
        retailer = "Best Buy CA"
        sku_match = re.search(r'/(\d{8,})', url)
        links = f'<a href="{url}">Product Page</a>'
        extra = f"\n<b>SKU</b>\n{sku_match.group(1) if sku_match else 'N/A'}"
    elif 'walmart.ca' in url:
        retailer = "Walmart CA"
        links = f'<a href="{url}">Product Page</a>'
        extra = ""
    elif 'costco.ca' in url:
        retailer = "Costco CA"
        links = f'<a href="{url}">Product Page</a>'
        extra = ""
    elif 'ebgames.ca' in url:
        retailer = "EB Games CA"
        links = f'<a href="{url}">Product Page</a>'
        extra = ""
    else:
        retailer = "Unknown"
        links = f'<a href="{url}">Product Page</a>'
        extra = ""

    hot_items = ['SPC', 'ETB', 'Costco']
    tag = "🔥 HOT DROP" if any(h in name for h in hot_items) else "⚡ Restock"

    # Add price if available
    price_str = ""
    retail_price_num = None
    if price:
        p = str(price).lstrip('$')
        price_str = f"\n<b>Price</b>\n${p} CAD\n"
        try:
            retail_price_num = float(re.sub(r'[^\d.]', '', p))
        except:
            pass
    elif name in _detected_prices:
        p = str(_detected_prices[name]).lstrip('$')
        price_str = f"\n<b>Price</b>\n${p} CAD\n"
        try:
            retail_price_num = float(re.sub(r'[^\d.]', '', p))
        except:
            pass

    # Market price comparison
    market_str = ""
    mp = _market_prices.get(name)
    if mp and mp.get('market_cad'):
        market_cad = mp['market_cad']
        market_str = f"\n📊 <b>Market Value:</b> ~${market_cad:.0f} CAD"
        if retail_price_num and market_cad > retail_price_num:
            savings = market_cad - retail_price_num
            pct = int((savings / market_cad) * 100)
            market_str += f"\n💸 <b>Save {pct}%</b> vs secondary market (${savings:.0f} below resale)"

    return (
        f"🟣 <b>MasterBall Alerts | {retailer}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{name}</b>\n\n"
        f"<b>Type</b>\n{tag}\n"
        f"{price_str}"
        f"{market_str}\n"
        f"{extra}\n\n"
        f"<b>Links</b>\n{links}\n\n"
        f"⚡ <i>Act fast — limited stock!</i>"
    )


def process_followups(stock_status, bot_token, channel_id):
    """Check queued follow-ups — edit original message with sold out status."""
    global _followup_queue, _message_ids
    now = time.time()
    remaining = []
    
    if _followup_queue:
        log(f"📋 Processing {len(_followup_queue)} queued follow-ups...")
    
    for queued_time, product, url, alert_time in _followup_queue:
        if now - queued_time >= FOLLOWUP_DELAY:
            name = product['name']
            is_still_in_stock = stock_status.get(name, False)
            
            # Get stored message IDs
            channel_msg_id, dm_msg_id = _message_ids.get(name, (None, None))
            
            log(f"  🔍 Checking {name}: in_stock={is_still_in_stock}, msg_id={channel_msg_id}, elapsed={int(now - queued_time)}s")
            
            if not is_still_in_stock and channel_msg_id:
                # Calculate how long it was live
                live_duration_sec = int(now - alert_time)
                live_min = live_duration_sec // 60
                live_sec = live_duration_sec % 60
                
                # Build sold-out message
                retailer = 'Amazon CA' if 'amazon.ca' in url else 'Walmart CA' if 'walmart.ca' in url else 'Best Buy' if 'bestbuy.ca' in url else 'Costco' if 'costco.ca' in url else 'EB Games'
                price_str = _detected_prices.get(name, '')
                price_line = f" | {price_str}" if price_str else ""
                
                time_str = datetime.fromtimestamp(alert_time).strftime('%I:%M %p')
                duration_str = f"{live_min}m {live_sec}s" if live_min > 0 else f"{live_sec}s"
                
                sold_out_msg = (
                    f"🔴 <b>[SOLD OUT]</b> {name}\n\n"
                    f"<b>Retailer:</b> {retailer}{price_line}\n"
                    f"<b>Was live:</b> {time_str} ({duration_str})\n\n"
                    f"<a href=\"{url}\">View Product</a>"
                )
                
                # Edit the channel message
                edit_telegram(bot_token, channel_id, channel_msg_id, sold_out_msg)
                log(f"  ✏️  Edited message: {name} marked as SOLD OUT (live for {duration_str})")
                
                # Clean up
                if name in _message_ids:
                    del _message_ids[name]
            elif is_still_in_stock:
                log(f"  ✅ Follow-up: {name} still in stock after {FOLLOWUP_DELAY}s (keeping alert)")
                # Keep in queue for next check? Or remove?
                # For now, we'll remove it (one follow-up per alert)
            elif not channel_msg_id:
                log(f"  ⚠️  Follow-up skipped: {name} has no stored message_id")
        else:
            remaining.append((queued_time, product, url, alert_time))
    _followup_queue = remaining


# ---------------------------------------------------------------------------
# curl_cffi with fallback profiles
# ---------------------------------------------------------------------------

def load_walmart_cookies():
    """Load Walmart cookies from file (set by Safari CAPTCHA solve)."""
    cookie_file = os.path.join(MONITOR_DIR, "walmart_cookies.json")
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file) as f:
                cookies = json.load(f)
            return "; ".join(f"{k}={v}" for k, v in cookies.items())
        except:
            pass
    return None

def load_walmart_proxy():
    """Load Walmart proxy config (Webshare residential proxy)."""
    proxy_file = os.path.join(MONITOR_DIR, "walmart_proxy.json")
    if os.path.exists(proxy_file):
        try:
            with open(proxy_file) as f:
                proxy_config = json.load(f)
            if proxy_config.get('enabled'):
                return proxy_config.get('proxy_url')
        except:
            pass
    return None

_walmart_cookie_str = load_walmart_cookies()
_walmart_proxy_url = load_walmart_proxy()

def cffi_get_with_fallback(url, timeout=15):
    """Try multiple curl_cffi profiles if the primary one gets CAPTCHAd."""
    from curl_cffi import requests as cffi_requests
    
    # For Walmart, try with proxy + cookies
    if 'walmart.ca' in url:
        headers = {}
        if _walmart_cookie_str:
            headers["Cookie"] = _walmart_cookie_str
        
        # Try with proxy first
        if _walmart_proxy_url:
            try:
                response = cffi_requests.get(url, impersonate="chrome131", 
                                            headers=headers, proxies={"http": _walmart_proxy_url, "https": _walmart_proxy_url}, 
                                            timeout=timeout)
                if response.status_code in (200, 404) and 'captcha' not in response.text.lower()[:5000]:
                    return response
            except Exception as e:
                log(f"  ⚠️  Walmart proxy failed: {e}")
        
        # Fallback to direct with cookies
        if _walmart_cookie_str:
            try:
                response = cffi_requests.get(url, impersonate="chrome131", 
                                             headers=headers, timeout=timeout)
                if response.status_code in (200, 404) and 'captcha' not in response.text.lower()[:5000]:
                    return response
            except:
                pass
    
    for profile in CFFI_PROFILES:
        try:
            response = cffi_requests.get(url, impersonate=profile, timeout=timeout)
            if response.status_code == 200 and 'captcha' not in response.text.lower()[:5000]:
                return response
        except:
            continue
    # Return last attempt even if CAPTCHA
    try:
        return cffi_requests.get(url, impersonate=CFFI_PROFILES[0], timeout=timeout)
    except Exception as e:
        return None


# ---------------------------------------------------------------------------
# Site-specific stock checkers
# ---------------------------------------------------------------------------

def check_amazon(url, product=None):
    TRUSTED_SELLERS = ['amazon', 'amazon.ca', 'warehouse deals', 'the pokemon company', 'pokemon company', 'pokemon center']

    try:
        ua = random.choice(AMAZON_USER_AGENTS)
        headers = {
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-CA,en;q=0.9',
        }
        # Random small delay to avoid pattern detection
        time.sleep(random.uniform(0.2, 0.8))

        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            log(f"  ⚠️  Amazon returned {response.status_code}")
            return False

        html = response.text.lower()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Definite OOS signals
        oos_signals = ['see all buying options', 'currently unavailable',
                       "we don't know when or if this item will be back",
                       'sign up for restock alerts', 'email me when available']
        for signal in oos_signals:
            if signal in html:
                return False

        # Seller check — Amazon uses multiple formats:
        # "Sold by X" spans, "Ships from and sold by Amazon", "Shipper / Seller  X"
        seller_name = None
        sold_by_spans = soup.find_all('span', class_='a-size-small')
        for i, span in enumerate(sold_by_spans):
            span_text = span.get_text(strip=True).lower()
            if 'sold by' in span_text or 'seller' in span_text or 'shipper' in span_text:
                if i + 1 < len(sold_by_spans):
                    seller_name = sold_by_spans[i + 1].get_text(strip=True).lower()
                    break

        # Also check tabular format: "Shipper / Seller  Name"
        if not seller_name:
            for span in soup.find_all(['span', 'td', 'div']):
                text = span.get_text(strip=True)
                if 'Shipper' in text and 'Seller' in text:
                    # The seller name is often the next sibling or in a nearby element
                    next_el = span.find_next_sibling()
                    if next_el:
                        seller_name = next_el.get_text(strip=True).lower()
                        break
                    # Or it might be in the same parent row
                    parent = span.parent
                    if parent:
                        children = parent.find_all(['span', 'td', 'a'])
                        for child in children:
                            child_text = child.get_text(strip=True)
                            if child_text and 'shipper' not in child_text.lower() and 'seller' not in child_text.lower() and len(child_text) > 2:
                                seller_name = child_text.lower()
                                break
                    break

        ships_sold_by_amazon = 'ships from and sold by amazon' in html

        if seller_name:
            if any(t in seller_name for t in TRUSTED_SELLERS):
                log(f"  ✅ Sold by: {seller_name} (trusted)")
            else:
                log(f"  ⚠️  In stock but sold by '{seller_name}' (third-party) — skipping")
                return False
        elif ships_sold_by_amazon:
            log(f"  ✅ Ships from and sold by Amazon.ca")
        else:
            log(f"  ⚠️  No trusted seller detected — skipping")
            return False

        # Extract price
        price_el = soup.find('span', class_='a-price-whole')
        if price_el:
            try:
                price_text = price_el.get_text(strip=True).replace(',', '')
                _detected_prices[product['name'] if product else url] = price_text
                log(f"  💰 Price: ${price_text}")
            except:
                pass

        # In-stock signals
        avail_div = soup.find('div', {'id': 'availability'})
        if avail_div and 'in stock' in avail_div.get_text(strip=True).lower():
            return True

        buy_box = soup.find('input', {'id': 'add-to-cart-button'})
        if buy_box and 'see all buying options' not in html:
            return True

        return False
    except Exception as e:
        log(f"  ❌ Error checking Amazon: {e}")
        return False


# --- Camoufox browser for Walmart ---
_camoufox_browser = None
_camoufox_lock = threading.Lock()

def get_camoufox_browser():
    """Get or create a persistent Camoufox browser instance."""
    global _camoufox_browser
    with _camoufox_lock:
        if _camoufox_browser is None:
            try:
                from camoufox.sync_api import Camoufox
                proxy_config = {
                    "server": "http://proxy.example.com:80",
                    "username": "nvhejsis-rotate",
                    "password": "dh9ywm5aeafx"
                }
                _camoufox_browser = Camoufox(headless=True, proxy=proxy_config, geoip=True)
                _camoufox_browser.__enter__()
                log("🦊 Camoufox browser started for Walmart checks")
            except Exception as e:
                log(f"❌ Failed to start Camoufox: {e}")
                _camoufox_browser = None
        return _camoufox_browser

def check_walmart(url):
    TRUSTED_SELLERS = ['walmart', 'walmart canada', 'walmart.ca']
    try:
        browser = get_camoufox_browser()
        if not browser:
            log(f"  ⚠️  Walmart: Camoufox not available, skipping")
            return False
        
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            
            html = page.content()
            
            # Check for CAPTCHA (only if page is small = blocked)
            if len(html) < 15000 and 'captcha' in html.lower():
                log(f"  ⚠️  Walmart CAPTCHA — Camoufox blocked")
                page.close()
                return False
            
            json_match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if json_match:
                try:
                    data_str = json.dumps(json.loads(json_match.group(1)))
                    seller_match = re.search(r'"sellerName"\s*:\s*"([^"]+)"', data_str)
                    seller_name = seller_match.group(1) if seller_match else ''

                    is_trusted = any(t in seller_name.lower() for t in TRUSTED_SELLERS)
                    is_in_stock = '"availabilityStatus":"IN_STOCK"' in data_str or '"availabilityStatus": "IN_STOCK"' in data_str

                    page.close()
                    
                    if not is_in_stock:
                        return False
                    if is_trusted:
                        log(f"  ✅ Walmart: In stock | Seller: {seller_name} (trusted)")
                        return True
                    else:
                        log(f"  ⚠️  In stock but sold by '{seller_name}' (marketplace) — skipping")
                        return False
                except json.JSONDecodeError:
                    pass
            
            page.close()
            return False
        except Exception as e:
            try:
                page.close()
            except:
                pass
            log(f"  ❌ Walmart page error: {str(e)[:60]}")
            return False
    except Exception as e:
        log(f"  ❌ Error checking Walmart: {e}")
        return False


def check_bestbuy(url):
    TRUSTED_SELLERS = ['best buy', 'bestbuy', 'best buy canada']
    try:
        sku_match = re.search(r'/(\d{8,})(?:\?|$|#)', url)
        if not sku_match:
            return False
        sku = sku_match.group(1)

        api_url = f"https://www.bestbuy.ca/api/v2/json/product/{sku}?lang=en-CA"
        response = session.get(api_url, timeout=15)
        if response.status_code != 200:
            return False

        data = response.json()
        seller = data.get('seller', {})
        seller_name = seller.get('name', '').lower() if seller else ''

        if seller_name and not any(t in seller_name for t in TRUSTED_SELLERS):
            log(f"  ⚠️  In stock but sold by '{seller.get('name', 'unknown')}' (marketplace) — skipping")
            return False

        avail = data.get('availability', {})
        if avail.get('buttonState', '').lower() == 'addtocart' and data.get('isPurchasable') and avail.get('isAvailableOnline'):
            price = data.get('salePrice', data.get('regularPrice', 0))
            log(f"  ✅ Best Buy: In stock | ${price}")
            return True
        return False
    except Exception as e:
        log(f"  ❌ Error checking Best Buy: {e}")
        return False


def check_costco(url):
    try:
        response = cffi_get_with_fallback(url)
        if not response or response.status_code != 200:
            return False

        ld_matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', response.text, re.DOTALL)
        for m in ld_matches:
            try:
                data = json.loads(m)
                if isinstance(data, dict) and 'offers' in data:
                    availability = data['offers'].get('availability', '')
                    if 'InStock' in availability:
                        log(f"  ✅ Costco: In stock | ${data['offers'].get('price', '?')}")
                        return True
                    elif 'OutOfStock' in availability:
                        return False
            except:
                continue

        if len(response.text) < 5000:
            log(f"  ⚠️  Costco may have blocked request")
        return False
    except Exception as e:
        log(f"  ❌ Error checking Costco: {e}")
        return False


def check_ebgames(url):
    try:
        response = cffi_get_with_fallback(url)
        if not response or response.status_code != 200:
            return False

        html = response.text.lower()
        if 'pre-order' in html or 'preorder' in html:
            return False
        if 'out of stock' in html or 'sold out' in html or 'unavailable' in html:
            return False
        if 'add to cart' in html:
            log(f"  ✅ EB Games: In stock!")
            return True
        return False
    except Exception as e:
        log(f"  ❌ Error checking EB Games: {e}")
        return False


def check_generic(url):
    try:
        response = session.get(url, timeout=15)
        if response.status_code != 200:
            return False
        html = response.text.lower()
        for p in ['out of stock', 'sold out', 'currently unavailable', 'not available']:
            if p in html:
                return False
        for p in ['add to cart', 'add to basket', 'buy now', 'in stock']:
            if p in html:
                return True
        return False
    except:
        return False


def check_product(product):
    url = product['url']
    if 'amazon.ca' in url:
        return check_amazon(url, product)
    elif 'walmart.ca' in url:
        return check_walmart(url)
    elif 'bestbuy.ca' in url:
        return check_bestbuy(url)
    elif 'costco.ca' in url:
        return check_costco(url)
    elif 'ebgames.ca' in url:
        return check_ebgames(url)
    else:
        return check_generic(url)


def check_product_wrapper(product):
    """Wrapper for parallel execution — returns (product, is_in_stock)."""
    name = product['name']
    try:
        result = check_product(product)
        update_timestamp(name)
        return (product, result)
    except Exception as e:
        log(f"  ❌ Error checking {name}: {e}")
        return (product, False)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def monitor_loop():
    config = load_config()

    # Initialize database if available
    if USE_DB:
        init_db()
        log("💾 SQLite database: ENABLED")
        stock_status = get_stock_status()
    else:
        log("⚠️  SQLite not available, using JSON files")
        stock_status = load_json(STOCK_STATUS_FILE) if os.path.exists(STOCK_STATUS_FILE) else {}

    bot_token = config.get('telegram_bot_token', '')
    chat_id = config.get('telegram_chat_id', '')
    channel_id = config.get('telegram_channel_id', '')
    use_telegram = bool(bot_token and chat_id)
    check_interval = config.get('check_interval', 30)

    enabled_products = [p for p in config['products'] if p.get('enabled', True)]
    
    # Restore follow-up state from disk (survives restarts)
    load_followup_state(config)

    log("🤖 MasterBall Alerts Monitor v3 Started!")
    log(f"📱 Notifications: {'Telegram' if use_telegram else 'Desktop only'}")
    log(f"⏱️  Check interval: {check_interval}s")
    log(f"📦 Monitoring {len(enabled_products)} products")
    log(f"🔄 Async parallel checking: ENABLED")
    log(f"🛡️  CAPTCHA fallback profiles: {CFFI_PROFILES}")
    log(f"🔁 Alert cooldown: {ALERT_COOLDOWN_SECONDS}s")
    log("─" * 50)

    def alert(message, channel_too=False):
        if use_telegram:
            if channel_too and channel_id:
                # Stock alerts go to public channel only
                send_telegram(bot_token, channel_id, message)
            else:
                # System notifications go to personal DM
                send_telegram(bot_token, chat_id, message)

    send_notification("MasterBall Alerts", "Monitor v3 started!")
    if use_telegram:
        send_telegram(bot_token, chat_id, f"🤖 <b>MasterBall Alerts v3 Started!</b>\n\n"
                      f"📦 {len(enabled_products)} products\n"
                      f"🔄 Parallel checking enabled\n"
                      f"🛡️ CAPTCHA fallback profiles\n"
                      f"🔁 10 min alert cooldown")

    MAX_WORKERS = 6  # Parallel threads

    log(f"📦 All products: {len(enabled_products)} (parallel, every cycle)")
    log(f"🔄 Max workers: {MAX_WORKERS}")
    last_health_ping = time.time()
    last_config_check = time.time()
    error_counts = {}  # Track errors per retailer per cycle

    while True:
        try:
            cycle_start = time.time()

            # --- CHECK ALL PRODUCTS (parallel) ---
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(check_product_wrapper, p): p for p in enabled_products}
                for future in as_completed(futures):
                    product, is_in_stock = future.result()
                    name = product['name']
                    url = product['url']
                    prev_status = stock_status.get(name, False)

                    if is_in_stock and not prev_status:
                        if check_alert_cooldown(name):
                            log(f"🚨 STOCK ALERT: {name}")
                            if USE_DB:
                                retailer = 'amazon' if 'amazon' in url else 'walmart' if 'walmart' in url else 'bestbuy' if 'bestbuy' in url else 'costco' if 'costco' in url else 'ebgames'
                                add_alert(name, 'in_stock', retailer=retailer, url=url, price=_detected_prices.get(name))
                                increment_daily_stat('alerts_sent')
                            if 'amazon' in url:
                                # Run screenshot in background thread to not block alerts
                                threading.Thread(target=take_screenshot, args=(url, name), daemon=True).start()
                                # Auto-add to Jason's Amazon cart via Safari
                                try:
                                    asin_match = re.search(r'/dp/([A-Z0-9]+)', url)
                                    if asin_match:
                                        asin = asin_match.group(1)
                                        cart_url = f"https://www.amazon.ca/gp/aws/cart/add.html?ASIN.1={asin}&Quantity.1=1"
                                        subprocess.Popen(['open', '-a', 'Safari', cart_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        log(f"🛒 Auto-added to cart: {name} (ASIN: {asin})")
                                except Exception as e:
                                    log(f"⚠️  Auto-cart failed: {e}")
                            if use_telegram:
                                alert_msg = build_alert_message(product, price=_detected_prices.get(name))
                                # Send to channel and store message ID
                                channel_msg_id = send_telegram(bot_token, channel_id, alert_msg)
                                _message_ids[name] = (channel_msg_id, None)
                            twitter_post(name, url, price=_detected_prices.get(name))
                            send_notification("🚨 STOCK ALERT!", name)
                            set_alert_cooldown(name)
                            # Queue "still live" follow-up with alert time
                            alert_time = time.time()
                            _followup_queue.append((time.time(), product, url, alert_time))
                            log(f"📥 Queued follow-up for {name} (will check in {FOLLOWUP_DELAY}s)")
                            save_followup_state()
                        else:
                            log(f"🔇 Alert suppressed (cooldown): {name}")
                    elif not is_in_stock and prev_status:
                        log(f"📉 Out of stock: {name}")
                        if USE_DB:
                            add_alert(name, 'out_of_stock', url=url)
                    elif is_in_stock:
                        log(f"✅ Still in stock: {name}")

                    stock_status[name] = is_in_stock
                    if USE_DB:
                        set_stock_status(name, is_in_stock)

            # Process "still live" follow-ups
            process_followups(stock_status, bot_token, channel_id)
            if _followup_queue or _message_ids:
                save_followup_state()

            # Save JSON as backup (dashboard still reads from it)
            save_json(STOCK_STATUS_FILE, stock_status)

            # Config hot-reload (check every 5 minutes)
            if time.time() - last_config_check > 300:
                try:
                    new_config = load_config()
                    new_enabled = [p for p in new_config['products'] if p.get('enabled', True)]
                    if len(new_enabled) != len(enabled_products):
                        log(f"🔄 Config reloaded: {len(new_enabled)} products (was {len(enabled_products)})")
                        enabled_products = new_enabled
                        high_priority = [p for p in enabled_products if p.get('priority') == 'high']
                        normal_priority = [p for p in enabled_products if p.get('priority') != 'high']
                        check_interval = new_config.get('check_interval', 30)
                except:
                    pass
                last_config_check = time.time()

            # Error rate monitoring
            if USE_DB:
                increment_daily_stat('checks_total')

            cycle_time = time.time() - cycle_start
            log(f"💤 Cycle done in {cycle_time:.1f}s — sleeping {check_interval}s...")
            log("─" * 50)

            # Health watchdog — alert if no cycle completes for 5 min
            last_health_ping = time.time()

            time.sleep(check_interval)

            if _shutdown:
                log("⚠️  Graceful shutdown requested")
                save_json(STOCK_STATUS_FILE, stock_status)
                save_followup_state()
                log("💾 State saved. Goodbye!")
                break

        except KeyboardInterrupt:
            log("⚠️  Monitor stopped by user")
            break
        except Exception as e:
            log(f"❌ Error in main loop: {e}")
            # Health watchdog check
            if time.time() - last_health_ping > 300 and use_telegram:
                send_telegram(bot_token, chat_id, "⚠️ <b>MasterBall Monitor may be stalled!</b>\nNo successful cycle in 5 minutes.")
                last_health_ping = time.time()
            time.sleep(10)


if __name__ == "__main__":
    monitor_loop()
