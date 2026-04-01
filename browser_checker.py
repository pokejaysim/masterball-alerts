#!/usr/bin/env python3
"""
Browser-based stock checker using Playwright for sites that block curl_cffi.
Used for: Walmart.ca, LondonDrugs.com
Runs headless Chromium with stealth mode.
"""

import json
import os
import re
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))

# Reuse browser across checks to avoid startup overhead
_browser = None
_context = None
_playwright = None

def get_browser(headless=True):
    """Get or create a persistent browser instance."""
    global _browser, _context, _playwright
    
    if _browser and _browser.is_connected():
        return _browser, _context
    
    _playwright = sync_playwright().start()
    
    _browser = _playwright.chromium.launch(
        headless=headless,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-gpu',
        ]
    )
    
    _context = _browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        locale='en-CA'
    )
    
    # Stealth scripts
    _context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-CA', 'en-US', 'en']});
        window.chrome = {runtime: {}};
    """)
    
    return _browser, _context

def close_browser():
    """Clean up browser resources."""
    global _browser, _context, _playwright
    try:
        if _context:
            _context.close()
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()
    except:
        pass
    _browser = None
    _context = None
    _playwright = None

def check_walmart_browser(url, headless=True):
    """
    Check Walmart.ca stock using real browser.
    Returns: (is_in_stock: bool, seller_name: str|None, error: str|None)
    """
    TRUSTED_SELLERS = ['walmart', 'walmart canada', 'walmart.ca']
    
    try:
        browser, context = get_browser(headless)
        page = context.new_page()
        
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=20000)
            page.wait_for_timeout(3000)  # Let JS render
            
            html = page.content()
            html_lower = html.lower()
            
            # Check for CAPTCHA
            if 'px-captcha' in html_lower or ('captcha' in html_lower and len(html) < 15000):
                page.close()
                return False, None, "CAPTCHA"
            
            # Try to extract __NEXT_DATA__ JSON
            next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if next_data:
                try:
                    data = json.loads(next_data.group(1))
                    data_str = json.dumps(data)
                    
                    # Extract seller
                    seller_match = re.search(r'"sellerName"\s*:\s*"([^"]+)"', data_str)
                    seller_name = seller_match.group(1) if seller_match else None
                    
                    # Check stock
                    is_in_stock = '"availabilityStatus":"IN_STOCK"' in data_str or '"availabilityStatus": "IN_STOCK"' in data_str
                    
                    if is_in_stock and seller_name:
                        is_trusted = any(t in seller_name.lower() for t in TRUSTED_SELLERS)
                        page.close()
                        if is_trusted:
                            return True, seller_name, None
                        else:
                            return False, seller_name, f"Third-party seller: {seller_name}"
                    
                    page.close()
                    return False, seller_name, None
                except json.JSONDecodeError:
                    pass
            
            # Fallback: check rendered page text
            page_text = page.inner_text('body')
            
            has_add_to_cart = 'add to cart' in page_text.lower()
            has_oos = any(s in page_text.lower() for s in ['out of stock', 'currently unavailable', 'not available'])
            
            page.close()
            
            if has_oos:
                return False, None, None
            elif has_add_to_cart:
                return True, None, None
            else:
                return False, None, "Could not determine stock"
                
        except PlaywrightTimeout:
            page.close()
            return False, None, "Timeout"
        except Exception as e:
            try:
                page.close()
            except:
                pass
            return False, None, f"Error: {str(e)[:80]}"
            
    except Exception as e:
        return False, None, f"Browser error: {str(e)[:80]}"

def check_londondrugs_browser(url, headless=True):
    """
    Check LondonDrugs.com stock using real browser.
    Returns: (is_in_stock: bool, price: str|None, error: str|None)
    """
    try:
        browser, context = get_browser(headless)
        page = context.new_page()
        
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=20000)
            page.wait_for_timeout(3000)  # Let React render
            
            page_text = page.inner_text('body')
            html = page.content()
            
            # Check stock
            has_add_to_cart = 'add to cart' in page_text.lower() or 'add to bag' in page_text.lower()
            has_oos = any(s in page_text.lower() for s in ['out of stock', 'sold out', 'currently unavailable', 'not available'])
            
            # Extract price
            price = None
            price_match = re.search(r'\$(\d+\.\d{2})', page_text)
            if price_match:
                p = float(price_match.group(1))
                if p > 3 and p < 500:  # Filter out $1.99 shipping etc
                    price = price_match.group(1)
            
            # Extract product title
            title = None
            title_elem = page.query_selector('h1')
            if title_elem:
                title = title_elem.inner_text().strip()
            
            page.close()
            
            if has_oos:
                return False, price, None
            elif has_add_to_cart:
                return True, price, None
            else:
                return False, price, "Could not determine stock"
                
        except PlaywrightTimeout:
            page.close()
            return False, None, "Timeout"
        except Exception as e:
            try:
                page.close()
            except:
                pass
            return False, None, f"Error: {str(e)[:80]}"
            
    except Exception as e:
        return False, None, f"Browser error: {str(e)[:80]}"

if __name__ == "__main__":
    print("=== Browser-based Stock Checker ===\n")
    
    # Test Walmart
    print("--- Testing Walmart.ca ---")
    walmart_url = "https://www.walmart.ca/en/ip/celadon/44V1HEXYK000"
    print(f"URL: {walmart_url}")
    in_stock, seller, error = check_walmart_browser(walmart_url, headless=True)
    print(f"In stock: {in_stock}")
    print(f"Seller: {seller}")
    print(f"Error: {error}")
    
    time.sleep(2)
    
    # Test London Drugs
    print("\n--- Testing LondonDrugs.com ---")
    ld_url = "https://www.londondrugs.com/products/pokemon-tcg-scarlet-and-violet-prismatic-evolutions-booster-bundle-expansion-pack/p/L2983911"
    print(f"URL: {ld_url}")
    in_stock, price, error = check_londondrugs_browser(ld_url, headless=True)
    print(f"In stock: {in_stock}")
    print(f"Price: ${price}" if price else "Price: N/A")
    print(f"Error: {error}")
    
    close_browser()
    print("\n✅ Done")
