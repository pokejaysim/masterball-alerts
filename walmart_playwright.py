#!/usr/bin/env python3
"""
Walmart checker using Playwright (real browser) to bypass anti-bot.
Slower but bypasses CAPTCHA when used with residential proxy.
"""

import json
import os
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))

def load_proxy_config():
    """Load Webshare proxy config."""
    proxy_file = os.path.join(MONITOR_DIR, "walmart_proxy.json")
    if os.path.exists(proxy_file):
        try:
            with open(proxy_file) as f:
                config = json.load(f)
            if config.get('enabled'):
                # Parse proxy URL: http://user:pass@host:port
                proxy_url = config['proxy_url']
                match = re.match(r'http://([^:]+):([^@]+)@([^:]+):(\d+)', proxy_url)
                if match:
                    return {
                        'server': f'http://{match.group(3)}:{match.group(4)}',
                        'username': match.group(1),
                        'password': match.group(2)
                    }
        except:
            pass
    return None

def check_walmart_playwright(url, headless=True):
    """
    Check Walmart stock using Playwright with stealth mode.
    Returns: (is_in_stock: bool, seller_name: str|None, error: str|None)
    """
    proxy_config = load_proxy_config()
    
    try:
        with sync_playwright() as p:
            # Launch browser with proxy if available
            browser_options = {
                'headless': headless,
                'args': [
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox'
                ]
            }
            
            if proxy_config:
                browser_options['proxy'] = proxy_config
            
            browser = p.chromium.launch(**browser_options)
            
            # Create context with realistic viewport
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            )
            
            # Add stealth scripts
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            """)
            
            page = context.new_page()
            
            # Navigate to product page
            response = page.goto(url, wait_until='domcontentloaded', timeout=20000)
            
            # Wait a bit for dynamic content
            page.wait_for_timeout(2000)
            
            # Check for CAPTCHA
            page_content = page.content().lower()
            if 'captcha' in page_content or 'px-captcha' in page_content:
                browser.close()
                return False, None, "CAPTCHA detected"
            
            # Extract seller name from rendered page
            seller_name = None
            try:
                # Look for seller info in various places
                seller_elements = page.query_selector_all('span:has-text("Sold by"), span:has-text("Seller")')
                for elem in seller_elements:
                    parent = elem.evaluate_handle('el => el.parentElement')
                    text = parent.as_element().inner_text() if parent else ""
                    if text and len(text) < 100:
                        seller_name = text.strip()
                        break
            except:
                pass
            
            # Check stock status via page content
            html = page.content()
            
            # Out of stock signals
            oos_signals = [
                'currently unavailable',
                'out of stock',
                'see all buying options',
                "we don't know when or if this item will be back"
            ]
            is_oos = any(signal in html.lower() for signal in oos_signals)
            
            # In stock signals
            has_add_to_cart = 'add to cart' in html.lower()
            
            browser.close()
            
            if is_oos:
                return False, seller_name, None
            elif has_add_to_cart:
                return True, seller_name, None
            else:
                return False, seller_name, "Could not determine stock status"
            
    except PlaywrightTimeout:
        return False, None, "Timeout loading page"
    except Exception as e:
        return False, None, f"Error: {str(e)[:100]}"

if __name__ == "__main__":
    # Test with a known Walmart URL
    test_url = "https://www.walmart.ca/en/ip/celadon/44V1HEXYK000"
    print(f"Testing: {test_url}")
    in_stock, seller, error = check_walmart_playwright(test_url, headless=False)
    print(f"In stock: {in_stock}")
    print(f"Seller: {seller}")
    print(f"Error: {error}")
