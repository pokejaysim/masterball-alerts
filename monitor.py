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
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from settings import MONITOR_DIR, load_config, load_json_with_local_override
from product_utils import (
    PROTECTED_RETAILERS,
    STOCK_BLOCKED,
    STOCK_UNKNOWN,
    StockResult,
    escape_html,
    normalize_url,
    product_identifier,
    retailer_display_name,
    retailer_from_url,
    stock_transition,
)

# Thread-local storage for per-thread HTTP sessions and state
_thread_local = threading.local()

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

STOCK_STATUS_FILE = os.path.join(MONITOR_DIR, "stock_status.json")
TIMESTAMPS_FILE = os.path.join(MONITOR_DIR, "check_timestamps.json")
ALERT_COOLDOWNS_FILE = os.path.join(MONITOR_DIR, "alert_cooldowns.json")

ALERT_COOLDOWN_SECONDS = 1800  # 30 minutes

# Import database layer
try:
    from database import (init_db, get_stock_status, set_stock_status,
                          add_alert, update_timestamp as db_update_timestamp,
                          check_cooldown as db_check_cooldown, set_cooldown as db_set_cooldown,
                          log_error as db_log_error, increment_daily_stat,
                          get_approved_products)
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

# Thread-safe session getter — each thread gets its own requests.Session
def get_session():
    if not hasattr(_thread_local, 'session') or _thread_local.session is None:
        _thread_local.session = requests.Session()
        _thread_local.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-CA,en;q=0.9',
        })
    return _thread_local.session

# Backwards-compat module-level session for non-threaded calls
session = get_session()


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


# Thread-safe detected prices and message IDs
_prices_lock = threading.Lock()
_sellers_lock = threading.Lock()
_message_ids_lock = threading.Lock()

def set_detected_price(name, price):
    with _prices_lock:
        _detected_prices[name] = price

def get_detected_price(name, default=None):
    with _prices_lock:
        return _detected_prices.get(name, default)

def set_detected_seller(name, seller):
    if not seller:
        return
    with _sellers_lock:
        _detected_sellers[name] = seller

def get_detected_seller(name, default=None):
    with _sellers_lock:
        return _detected_sellers.get(name, default)

def set_message_id(name, channel_id, dm_id=None):
    with _message_ids_lock:
        _message_ids[name] = (channel_id, dm_id)

def get_message_id(name):
    with _message_ids_lock:
        return _message_ids.get(name, (None, None))

def del_message_id(name):
    with _message_ids_lock:
        _message_ids.pop(name, None)

# Track detected prices and "still live" follow-ups
_detected_prices = {}  # product_name -> price string
_detected_sellers = {}  # product_name -> seller string
_followup_queue = []   # list of (check_time, product, url, alert_time)
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
    retailer = retailer_display_name(url)
    seller = get_detected_seller(name)
    product_id = product_identifier(url)

    lines = [
        f"🟣 <b>{escape_html(retailer)} Restock</b>",
        "",
        f"<b>{escape_html(name)}</b>",
    ]

    price_value = price or get_detected_price(name)
    if price_value:
        price_text = str(price_value).lstrip("$")
        lines.append(f"Price: <b>${escape_html(price_text)} CAD</b>")
    if seller:
        lines.append(f"Seller: {escape_html(seller)}")

    links = [f'<a href="{escape_html(url)}">Product</a>']
    if retailer_from_url(url) == "amazon" and product_id:
        cart_link = f"https://www.amazon.ca/gp/aws/cart/add.html?ASIN.1={product_id}&Quantity.1=1"
        links.append(f'<a href="{escape_html(cart_link)}">Add to Cart</a>')

    lines.extend([
        "",
        " | ".join(links),
        "",
        "<i>Act fast. Limited stock can vanish quickly.</i>",
    ])
    return "\n".join(lines)



# ---------------------------------------------------------------------------
# Unified alert function (Fix #2: deduplicated alert logic)
# ---------------------------------------------------------------------------

def fire_alert(product, url, is_hot=False, bot_token='', channel_id='', use_telegram=False):
    """Fire a restock alert — used by both main loop and hot-product fast-check."""
    name = product['name']
    price = get_detected_price(name)
    retailer = 'amazon' if 'amazon' in url else 'walmart' if 'walmart' in url else 'bestbuy' if 'bestbuy' in url else 'costco' if 'costco' in url else 'ebgames'
    tag = '🚨 HOT STOCK ALERT' if is_hot else '🚨 STOCK ALERT'

    log(f"{tag}: {name}")

    # Database
    if USE_DB:
        add_alert(name, 'in_stock', retailer=retailer, url=url, price=price)
        increment_daily_stat('alerts_sent')

    # Screenshot (Amazon only)
    if 'amazon' in url:
        threading.Thread(target=take_screenshot, args=(url, name), daemon=True).start()

    # Telegram alert
    if use_telegram and channel_id:
        alert_msg = build_alert_message(product, price=price)
        channel_msg_id = send_telegram(bot_token, channel_id, alert_msg)
        set_message_id(name, channel_msg_id)

    # Twitter
    twitter_post(name, url, price=price)

    # Desktop notification
    send_notification(tag, name)

    # Cooldown
    set_alert_cooldown(name)

    # Queue "still live" follow-up
    alert_time = time.time()
    _followup_queue.append((time.time(), product, url, alert_time))
    save_followup_state()
    log(f"📥 Queued follow-up for {name} (will check in {FOLLOWUP_DELAY}s)")


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
            channel_msg_id, dm_msg_id = get_message_id(name)
            
            log(f"  🔍 Checking {name}: in_stock={is_still_in_stock}, msg_id={channel_msg_id}, elapsed={int(now - queued_time)}s")
            
            if not is_still_in_stock and channel_msg_id:
                # Calculate how long it was live
                live_duration_sec = int(now - alert_time)
                live_min = live_duration_sec // 60
                live_sec = live_duration_sec % 60
                
                # Build sold-out message
                retailer = retailer_display_name(url)
                price_str = _detected_prices.get(name, '')
                price_line = f" | ${escape_html(price_str)} CAD" if price_str else ""
                
                time_str = datetime.fromtimestamp(alert_time).strftime('%I:%M %p')
                duration_str = f"{live_min}m {live_sec}s" if live_min > 0 else f"{live_sec}s"
                
                sold_out_msg = (
                    f"🔴 <b>SOLD OUT</b>\n\n"
                    f"<b>{escape_html(name)}</b>\n"
                    f"{escape_html(retailer)}{price_line}\n"
                    f"Live around {escape_html(time_str)} for {escape_html(duration_str)}\n\n"
                    f"<a href=\"{escape_html(url)}\">Product</a>"
                )
                
                # Edit the channel message
                edit_telegram(bot_token, channel_id, channel_msg_id, sold_out_msg)
                log(f"  ✏️  Edited message: {name} marked as SOLD OUT (live for {duration_str})")
                
                # Clean up
                if name in _message_ids:
                    del_message_id(name)
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
    try:
        proxy_config = load_json_with_local_override("walmart_proxy.json")
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
# Selective browser lane for protected retailers
# ---------------------------------------------------------------------------

_browser_lane_lock = threading.Lock()
_browser_backoff = {}  # normalized_url -> {"next_allowed": float, "failures": int}
_degraded_notices = {}  # retailer -> last_notice_time
_discovery_lock = threading.Lock()
_discovery_reload_requested = threading.Event()
BROWSER_MIN_INTERVAL_SECONDS = 180
BROWSER_MAX_BACKOFF_SECONDS = 3600


def _browser_lane_allowed(product):
    url = product.get("url", "")
    retailer = retailer_from_url(url)
    if retailer not in PROTECTED_RETAILERS:
        return False, "not protected"
    if product.get("priority") != "high":
        return False, "not high priority"

    key = normalize_url(url)
    state = _browser_backoff.get(key, {})
    now = time.time()
    if now < state.get("next_allowed", 0):
        return False, f"browser backoff {int(state['next_allowed'] - now)}s"
    return True, None


def _record_browser_lane_result(url, blocked=False):
    key = normalize_url(url)
    now = time.time()
    state = _browser_backoff.setdefault(key, {"next_allowed": 0, "failures": 0})
    if blocked:
        state["failures"] = min(int(state.get("failures", 0)) + 1, 4)
        backoff = min(900 * state["failures"], BROWSER_MAX_BACKOFF_SECONDS)
        state["next_allowed"] = now + backoff
    else:
        state["failures"] = 0
        state["next_allowed"] = now + BROWSER_MIN_INTERVAL_SECONDS


def check_walmart_browser_lane(product):
    allowed, reason = _browser_lane_allowed(product)
    if not allowed:
        return StockResult.blocked(reason=reason) if "backoff" in str(reason) else StockResult.unknown(reason=reason)

    if not _browser_lane_lock.acquire(blocking=False):
        return StockResult.unknown(reason="browser lane busy")

    try:
        from browser_checker import check_walmart_browser

        in_stock, seller, error = check_walmart_browser(product["url"], headless=True)
        blocked = bool(error and "captcha" in error.lower())
        _record_browser_lane_result(product["url"], blocked=blocked)
        if blocked:
            return StockResult.blocked(reason=error)
        if error and not in_stock:
            return StockResult.unknown(reason=error, seller=seller)
        if in_stock:
            return StockResult.in_stock(seller=seller, reason="browser")
        return StockResult.out_of_stock(seller=seller, reason="browser")
    except ImportError as e:
        _record_browser_lane_result(product["url"], blocked=True)
        return StockResult.unknown(reason=f"browser dependency missing: {e}")
    except Exception as e:
        _record_browser_lane_result(product["url"], blocked=True)
        return StockResult.unknown(reason=f"browser check failed: {e}")
    finally:
        _browser_lane_lock.release()


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
            if response.status_code in (403, 429, 503):
                return StockResult.blocked(reason=f"Amazon returned {response.status_code}")
            return StockResult.unknown(reason=f"Amazon returned {response.status_code}")

        html = response.text.lower()
        if 'captcha' in html[:5000] or 'enter the characters you see below' in html[:5000]:
            return StockResult.blocked(reason="Amazon CAPTCHA")
        soup = BeautifulSoup(response.text, 'html.parser')

        # Definite OOS signals
        oos_signals = ['see all buying options', 'currently unavailable',
                       "we don't know when or if this item will be back",
                       'sign up for restock alerts', 'email me when available']
        for signal in oos_signals:
            if signal in html:
                return StockResult.out_of_stock(reason=signal)

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
                return StockResult.marketplace(seller=seller_name, reason="third-party seller")
        elif ships_sold_by_amazon:
            seller_name = "Amazon.ca"
            log(f"  ✅ Ships from and sold by Amazon.ca")
        else:
            log(f"  ⚠️  No trusted seller detected — skipping")
            return StockResult.unknown(reason="No trusted seller detected")

        # Extract price
        price_el = soup.find('span', class_='a-price-whole')
        if price_el:
            try:
                price_text = price_el.get_text(strip=True).replace(',', '')
                set_detected_price(product['name'] if product else url, price_text)
                log(f"  💰 Price: ${price_text}")
            except:
                pass

        # In-stock signals
        avail_div = soup.find('div', {'id': 'availability'})
        if avail_div and 'in stock' in avail_div.get_text(strip=True).lower():
            return StockResult.in_stock(price=get_detected_price(product['name'] if product else url), seller=seller_name)

        buy_box = soup.find('input', {'id': 'add-to-cart-button'})
        if buy_box and 'see all buying options' not in html:
            return StockResult.in_stock(price=get_detected_price(product['name'] if product else url), seller=seller_name)

        return StockResult.unknown(reason="No stock signal found", seller=seller_name)
    except Exception as e:
        log(f"  ❌ Error checking Amazon: {e}")
        return StockResult.unknown(reason=str(e))


# --- Camoufox browser for Walmart ---
_camoufox_browser = None
_camoufox_lock = threading.Lock()

def check_walmart_camoufox(url):
    """Check a single Walmart URL using a fresh Camoufox browser session.
    Retries once on proxy/connection failure."""
    from camoufox.sync_api import Camoufox
    proxy_config = None
    proxy_file = os.path.join(MONITOR_DIR, "camoufox_proxy.json")
    if os.path.exists(proxy_file):
        try:
            with open(proxy_file) as pf:
                proxy_config = json.load(pf)
        except:
            log("  ⚠️  Failed to load camoufox_proxy.json")
    for attempt in range(2):  # Try twice (rotating proxy = different IP each time)
        try:
            with Camoufox(headless=True, proxy=proxy_config, geoip=True) as browser:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)
                html = page.content()
                page.close()
                return html
        except Exception as e:
            if attempt == 0:
                time.sleep(2)  # Brief pause before retry
                continue
            return None
    return None

def check_walmart(url, product=None):
    TRUSTED_SELLERS = ['walmart', 'walmart canada', 'walmart.ca']
    try:
        response = cffi_get_with_fallback(url)
        html = response.text if response else ""
        if not response:
            log(f"  ⚠️  Walmart: no response")
            result = StockResult.blocked(reason="no response")
            if product:
                browser_result = check_walmart_browser_lane(product)
                return browser_result if not browser_result.is_indeterminate else result
            return result

        if response.status_code in (403, 429, 520, 530):
            log(f"  ⚠️  Walmart blocked/status {response.status_code}")
            result = StockResult.blocked(reason=f"status {response.status_code}")
            if product:
                browser_result = check_walmart_browser_lane(product)
                return browser_result if not browser_result.is_indeterminate else result
            return result
        
        # Check for CAPTCHA (only if page is small = blocked)
        if len(html) < 15000 and 'captcha' in html.lower():
            log(f"  ⚠️  Walmart CAPTCHA — Camoufox blocked")
            result = StockResult.blocked(reason="Walmart CAPTCHA")
            if product:
                browser_result = check_walmart_browser_lane(product)
                return browser_result if not browser_result.is_indeterminate else result
            return result
        
        json_match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if json_match:
            try:
                data_str = json.dumps(json.loads(json_match.group(1)))
                seller_match = re.search(r'"sellerName"\s*:\s*"([^"]+)"', data_str)
                seller_name = seller_match.group(1) if seller_match else ''
                price_match = re.search(r'"currentPrice"\s*:\s*\{[^}]*"price"\s*:\s*([0-9.]+)', data_str)
                price = price_match.group(1) if price_match else None
                if product and price:
                    set_detected_price(product["name"], price)

                is_trusted = any(t in seller_name.lower() for t in TRUSTED_SELLERS)
                is_in_stock = '"availabilityStatus":"IN_STOCK"' in data_str or '"availabilityStatus": "IN_STOCK"' in data_str

                if not is_in_stock:
                    return StockResult.out_of_stock(price=price, seller=seller_name)
                if is_trusted:
                    log(f"  ✅ Walmart: In stock | Seller: {seller_name} (trusted)")
                    return StockResult.in_stock(price=price, seller=seller_name)
                else:
                    log(f"  ⚠️  In stock but sold by '{seller_name}' (marketplace) — skipping")
                    return StockResult.marketplace(price=price, seller=seller_name, reason="marketplace seller")
            except json.JSONDecodeError:
                pass

        result = StockResult.unknown(reason="Walmart JSON not found")
        if product:
            browser_result = check_walmart_browser_lane(product)
            return browser_result if not browser_result.is_indeterminate else result
        return result
    except Exception as e:
        log(f"  ❌ Error checking Walmart: {e}")
        return StockResult.unknown(reason=str(e))


def check_bestbuy(url):
    TRUSTED_SELLERS = ['best buy', 'bestbuy', 'best buy canada']
    try:
        sku_match = re.search(r'/(\d{8,})(?:\?|$|#)', url)
        if not sku_match:
            return StockResult.unknown(reason="SKU not found")
        sku = sku_match.group(1)

        api_url = f"https://www.bestbuy.ca/api/v2/json/product/{sku}?lang=en-CA"
        response = get_session().get(api_url, timeout=15)
        if response.status_code != 200:
            if response.status_code in (403, 429, 503):
                return StockResult.blocked(reason=f"Best Buy API returned {response.status_code}")
            return StockResult.unknown(reason=f"Best Buy API returned {response.status_code}")

        data = response.json()
        seller = data.get('seller', {})
        seller_name = seller.get('name', '').lower() if seller else ''
        price = data.get('salePrice', data.get('regularPrice', 0))

        if seller_name and not any(t in seller_name for t in TRUSTED_SELLERS):
            log(f"  ⚠️  In stock but sold by '{seller.get('name', 'unknown')}' (marketplace) — skipping")
            return StockResult.marketplace(price=price, seller=seller.get('name', 'unknown'), reason="marketplace seller")

        avail = data.get('availability', {})
        if avail.get('buttonState', '').lower() == 'addtocart' and data.get('isPurchasable') and avail.get('isAvailableOnline'):
            log(f"  ✅ Best Buy: In stock | ${price}")
            return StockResult.in_stock(price=price, seller=seller.get('name', 'Best Buy') if seller else "Best Buy")
        button_state = str(avail.get('buttonState', '')).lower()
        if 'preorder' in button_state or 'pre-order' in button_state:
            return StockResult.preorder(price=price, reason=button_state)
        return StockResult.out_of_stock(price=price, seller=seller.get('name') if seller else None)
    except Exception as e:
        log(f"  ❌ Error checking Best Buy: {e}")
        return StockResult.unknown(reason=str(e))


def check_costco(url):
    try:
        response = cffi_get_with_fallback(url)
        if not response or response.status_code != 200:
            status = response.status_code if response else "no response"
            if status in (403, 429, 503):
                return StockResult.blocked(reason=f"Costco returned {status}")
            return StockResult.unknown(reason=f"Costco returned {status}")

        ld_matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', response.text, re.DOTALL)
        for m in ld_matches:
            try:
                data = json.loads(m)
                if isinstance(data, dict) and 'offers' in data:
                    availability = data['offers'].get('availability', '')
                    price = data['offers'].get('price')
                    if 'InStock' in availability:
                        log(f"  ✅ Costco: In stock | ${price or '?'}")
                        return StockResult.in_stock(price=price, seller="Costco")
                    elif 'OutOfStock' in availability:
                        return StockResult.out_of_stock(price=price, seller="Costco")
            except:
                continue

        if len(response.text) < 5000:
            log(f"  ⚠️  Costco may have blocked request")
            return StockResult.blocked(reason="small response")
        return StockResult.unknown(reason="No Costco stock metadata")
    except Exception as e:
        log(f"  ❌ Error checking Costco: {e}")
        return StockResult.unknown(reason=str(e))


def check_ebgames(url):
    try:
        response = cffi_get_with_fallback(url)
        if not response or response.status_code != 200:
            status = response.status_code if response else "no response"
            if status in (403, 429, 503):
                return StockResult.blocked(reason=f"EB Games returned {status}")
            return StockResult.unknown(reason=f"EB Games returned {status}")

        html = response.text.lower()
        price_match = re.search(r'\$(\d+(?:\.\d{2})?)', response.text)
        price = price_match.group(1) if price_match else None
        if 'pre-order' in html or 'preorder' in html:
            return StockResult.preorder(price=price, reason="preorder")
        if 'out of stock' in html or 'sold out' in html or 'unavailable' in html:
            return StockResult.out_of_stock(price=price, seller="GameStop/EB Games")
        if 'add to cart' in html:
            log(f"  ✅ EB Games: In stock!")
            return StockResult.in_stock(price=price, seller="GameStop/EB Games")
        return StockResult.unknown(price=price, reason="No EB Games stock signal")
    except Exception as e:
        log(f"  ❌ Error checking EB Games: {e}")
        return StockResult.unknown(reason=str(e))


def check_generic(url):
    try:
        response = get_session().get(url, timeout=15)
        if response.status_code != 200:
            return StockResult.unknown(reason=f"status {response.status_code}")
        html = response.text.lower()
        for p in ['out of stock', 'sold out', 'currently unavailable', 'not available']:
            if p in html:
                return StockResult.out_of_stock(reason=p)
        for p in ['add to cart', 'add to basket', 'buy now', 'in stock']:
            if p in html:
                return StockResult.in_stock(reason=p)
        return StockResult.unknown(reason="No generic stock signal")
    except Exception as e:
        return StockResult.unknown(reason=str(e))


def check_product(product):
    url = product['url']
    if 'amazon.ca' in url:
        return check_amazon(url, product)
    elif 'walmart.ca' in url:
        return check_walmart(url, product)
    elif 'bestbuy.ca' in url:
        return check_bestbuy(url)
    elif 'costco.ca' in url:
        return check_costco(url)
    elif 'ebgames.ca' in url:
        return check_ebgames(url)
    elif 'pokemoncenter.com' in url:
        return StockResult.blocked(reason="Pokemon Center is discovery/manual-review only")
    else:
        return check_generic(url)


def check_product_wrapper(product):
    """Wrapper for parallel execution — returns (product, is_in_stock)."""
    name = product['name']
    try:
        result = check_product(product)
        if isinstance(result, bool):
            result = StockResult.in_stock() if result else StockResult.out_of_stock(reason="legacy boolean")
        if result.price:
            set_detected_price(name, result.price)
        if result.seller:
            set_detected_seller(name, result.seller)
        update_timestamp(name)
        return (product, result)
    except Exception as e:
        log(f"  ❌ Error checking {name}: {e}")
        return (product, StockResult.unknown(reason=str(e)))


def load_enabled_products(config=None):
    """Merge checked-in products with approved discovery products."""
    if config is None:
        config = load_config()

    products = [p for p in config.get('products', []) if p.get('enabled', True)]
    seen = {normalize_url(p.get("url", "")) for p in products}

    if USE_DB:
        try:
            for product in get_approved_products():
                normalized = normalize_url(product.get("url", ""))
                if normalized and normalized not in seen:
                    products.append(product)
                    seen.add(normalized)
        except Exception as e:
            log(f"⚠️  Failed to load approved discovery products: {e}")

    return products


def notify_degraded(retailer, reason, bot_token, chat_id):
    if not bot_token or not chat_id:
        return
    now = time.time()
    key = retailer or "unknown"
    if now - _degraded_notices.get(key, 0) < 900:
        return
    _degraded_notices[key] = now
    send_telegram(
        bot_token,
        chat_id,
        f"⚠️ <b>{escape_html(retailer_display_name(key))} checks degraded</b>\n\n{escape_html(reason or 'Blocked or unknown response')}\n\nStock state was preserved.",
    )


def discovery_settings_from_config(config):
    discovery = config.get("discovery", {})
    return {
        "auto_run": bool(discovery.get("auto_run", False)),
        "interval_seconds": max(3600, int(discovery.get("auto_run_interval_minutes", 180)) * 60),
        "startup_delay_seconds": max(0, int(discovery.get("auto_run_startup_delay_seconds", 120))),
        "auto_approve": bool(discovery.get("auto_approve", False)),
        "auto_min_confidence": float(discovery.get("auto_approve_min_confidence", 0.82)),
        "auto_retailers": discovery.get("auto_approve_retailers", ["walmart", "costco", "bestbuy", "ebgames"]),
    }


def maybe_start_auto_discovery(settings, last_started_at, monitor_started_at, log_func=log):
    if not settings.get("auto_run"):
        return last_started_at

    now = time.time()
    if now - monitor_started_at < settings["startup_delay_seconds"]:
        return last_started_at
    if last_started_at and now - last_started_at < settings["interval_seconds"]:
        return last_started_at
    if not _discovery_lock.acquire(blocking=False):
        return last_started_at

    def _run():
        try:
            from discover import run_discovery

            log_func("🔍 Auto-discovery started")
            changed = run_discovery(
                dry_run=False,
                send_review=True,
                auto_approve=settings["auto_approve"],
                auto_min_confidence=settings["auto_min_confidence"],
                auto_retailers=settings["auto_retailers"],
            )
            log_func(f"🔍 Auto-discovery finished: {len(changed)} new/updated candidates")
            _discovery_reload_requested.set()
        except Exception as e:
            log_func(f"⚠️  Auto-discovery failed: {e}")
        finally:
            _discovery_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return now


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
    stock_alert_chat_id = channel_id or chat_id
    use_telegram = bool(bot_token and chat_id)
    check_interval = config.get('check_interval', 30)
    discovery_settings = discovery_settings_from_config(config)

    enabled_products = load_enabled_products(config)
    
    # Restore follow-up state from disk (survives restarts)
    load_followup_state({"products": enabled_products})

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
    last_review_poll = 0
    monitor_started_at = time.time()
    last_auto_discovery_started = 0
    error_counts = {}  # Track errors per retailer per cycle

    while True:
        try:
            cycle_start = time.time()

            # --- Split products: hot (PE/AH Amazon), regular, Walmart ---
            hot_products = [p for p in enabled_products if 'amazon' in p['url'] and any(tag in p['name'] for tag in ['PE ', 'AH '])]
            non_walmart = [p for p in enabled_products if 'walmart.ca' not in p['url']]
            walmart_products = [p for p in enabled_products if 'walmart.ca' in p['url']]
            
            all_results = []
            
            # Check ALL non-Walmart products in parallel (includes hot products)
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(check_product_wrapper, p): p for p in non_walmart}
                for future in as_completed(futures):
                    all_results.append(future.result())
            
            # Check Walmart products sequentially because protected-browser fallback is single-lane.
            for p in walmart_products:
                result = check_product_wrapper(p)
                all_results.append(result)
                time.sleep(2)  # Delay between Walmart checks to avoid detection
            
            for product, result in all_results:
                name = product['name']
                url = product['url']
                prev_status = stock_status.get(name, False)
                new_status, transition = stock_transition(prev_status, result)

                if result.status == STOCK_BLOCKED:
                    retailer = retailer_from_url(url)
                    reason = result.reason or "blocked"
                    log(f"🛡️  Preserving state for {name}: {reason}")
                    if USE_DB:
                        increment_daily_stat('captchas_hit')
                        db_log_error(retailer, 'blocked', reason)
                    notify_degraded(retailer, reason, bot_token, chat_id)
                elif result.status == STOCK_UNKNOWN:
                    log(f"❔ Unknown stock state for {name}: {result.reason or 'no reason'}")
                    if USE_DB:
                        increment_daily_stat('checks_failed')
                elif transition == "became_in_stock":
                    if check_alert_cooldown(name):
                        fire_alert(product, url, is_hot=False, bot_token=bot_token, channel_id=stock_alert_chat_id, use_telegram=use_telegram)
                    else:
                        log(f"🔇 Alert suppressed (cooldown): {name}")
                elif transition == "became_out_of_stock":
                    log(f"📉 Out of stock: {name}")
                    if USE_DB:
                        add_alert(name, 'out_of_stock', url=url)
                elif transition == "still_in_stock":
                    log(f"✅ Still in stock: {name}")

                stock_status[name] = new_status
                if USE_DB and not result.is_indeterminate:
                    set_stock_status(name, new_status)

            # Process "still live" follow-ups
            process_followups(stock_status, bot_token, stock_alert_chat_id)
            if _followup_queue or _message_ids:
                save_followup_state()

            # Telegram owner commands for discovery review (/approve, /ignore, /pending)
            if use_telegram and time.time() - last_review_poll > 20:
                try:
                    from telegram_review import process_review_commands
                    changed = process_review_commands(bot_token, chat_id, log_func=log)
                    if changed:
                        enabled_products = load_enabled_products()
                        log(f"🔄 Discovery products reloaded: {len(enabled_products)} active products")
                except Exception as e:
                    log(f"⚠️  Review command polling failed: {e}")
                last_review_poll = time.time()

            if _discovery_reload_requested.is_set():
                _discovery_reload_requested.clear()
                enabled_products = load_enabled_products()
                log(f"🔄 Auto-discovery products reloaded: {len(enabled_products)} active products")

            last_auto_discovery_started = maybe_start_auto_discovery(
                discovery_settings,
                last_auto_discovery_started,
                monitor_started_at,
            )

            # Save JSON as backup (dashboard still reads from it)
            save_json(STOCK_STATUS_FILE, stock_status)

            # Config hot-reload (check every 5 minutes)
            if time.time() - last_config_check > 300:
                try:
                    new_config = load_config()
                    new_enabled = load_enabled_products(new_config)
                    if len(new_enabled) != len(enabled_products):
                        log(f"🔄 Products reloaded: {len(new_enabled)} products (was {len(enabled_products)})")
                    enabled_products = new_enabled
                    bot_token = new_config.get('telegram_bot_token', '')
                    chat_id = new_config.get('telegram_chat_id', '')
                    channel_id = new_config.get('telegram_channel_id', '')
                    stock_alert_chat_id = channel_id or chat_id
                    use_telegram = bool(bot_token and chat_id)
                    check_interval = new_config.get('check_interval', 30)
                    discovery_settings = discovery_settings_from_config(new_config)
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

            # --- HOT PRODUCT FAST-CHECK (PE/AH Amazon every ~10s) ---
            if hot_products:
                # Run 2 fast checks during the 30s sleep
                for fast_round in range(2):
                    time.sleep(10)
                    if _shutdown:
                        break
                    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        hot_futures = {executor.submit(check_product_wrapper, p): p for p in hot_products}
                        for future in as_completed(hot_futures):
                            product, result = future.result()
                            name = product['name']
                            url = product['url']
                            prev_status = stock_status.get(name, False)
                            new_status, transition = stock_transition(prev_status, result)
                            if result.is_indeterminate:
                                log(f"🛡️  Hot check preserved state for {name}: {result.reason or result.status}")
                                continue
                            if transition == "became_in_stock":
                                if check_alert_cooldown(name):
                                    fire_alert(product, url, is_hot=True, bot_token=bot_token, channel_id=stock_alert_chat_id, use_telegram=use_telegram)
                            elif transition == "became_out_of_stock":
                                stock_status[name] = False
                            stock_status[name] = new_status
                            if USE_DB:
                                set_stock_status(name, new_status)
                    if USE_DB:
                        increment_daily_stat('checks_total')
            else:
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


def test_product(url):
    """Run a single URL through the checker and print a structured result."""
    if USE_DB:
        init_db()
    product = {
        "name": f"Test Product - {retailer_display_name(url)}",
        "url": url,
        "enabled": True,
        "priority": "high",
    }
    result = check_product(product)
    if isinstance(result, bool):
        result = StockResult.in_stock() if result else StockResult.out_of_stock(reason="legacy boolean")
    print(json.dumps({
        "product": product,
        "result": result.as_dict(),
        "normalized_url": normalize_url(url),
        "retailer": retailer_from_url(url),
        "product_id": product_identifier(url),
    }, indent=2))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--test-product":
        test_product(sys.argv[2])
    else:
        monitor_loop()
