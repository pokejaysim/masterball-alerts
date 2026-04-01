#!/usr/bin/env python3
"""
Scrape PokeBeach and Serebii for upcoming Pokemon TCG product announcements.
Saves results to preorder_candidates.json for manual review.
Does NOT auto-add to monitor.
"""

import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import time

def scrape_pokebeach():
    """Scrape PokeBeach news for upcoming TCG products."""
    products = []
    
    try:
        # PokeBeach homepage
        url = "https://www.pokebeach.com"
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Find recent article headings
        articles = soup.find_all(['h2', 'h3'], limit=30)
        
        for article in articles:
            try:
                title = article.get_text(strip=True)
                if len(title) < 15:  # Skip short/noise headings
                    continue
                    
                # Get link
                link_elem = article.find('a')
                link = link_elem['href'] if link_elem else ""
                if link and not link.startswith('http'):
                    link = "https://www.pokebeach.com" + link
                
                # Look for product announcements
                keywords = ['to release', 'releasing', 'coming', 'announced', 'revealed',
                           'elite trainer', 'booster', 'collection', 'premium', 'tin',
                           'set coming', 'mini set', 'prerelease']
                
                if any(kw in title.lower() for kw in keywords):
                    # Try to extract date/month
                    date_match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)', 
                                          title, re.IGNORECASE)
                    release_date = None
                    if date_match:
                        month = date_match.group(1).capitalize()
                        # Try to find day number nearby
                        day_match = re.search(rf'{month}\s+(\d+)', title, re.IGNORECASE)
                        if day_match:
                            release_date = f"{month} {day_match.group(1)}, 2026"
                        else:
                            release_date = f"{month} 2026"
                    
                    products.append({
                        'source': 'PokeBeach',
                        'title': title,
                        'url': link,
                        'release_date': release_date,
                        'scraped_at': datetime.now().isoformat()
                    })
            except Exception as e:
                continue
                
    except Exception as e:
        print(f"PokeBeach scrape failed: {e}")
    
    return products

def scrape_serebii():
    """Scrape Serebii for upcoming TCG products."""
    products = []
    
    try:
        # Serebii TCG page
        url = "https://www.serebii.net/card/english.shtml"
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Serebii lists upcoming sets in tables
        tables = soup.find_all('table')
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) >= 2:
                    text = ' '.join(cell.get_text(strip=True) for cell in cells)
                    
                    # Look for dates and product names
                    if re.search(r'\d{1,2}(st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}', 
                                text, re.IGNORECASE):
                        products.append({
                            'source': 'Serebii',
                            'title': text[:200],
                            'url': url,
                            'release_date': None,  # Would need better parsing
                            'scraped_at': datetime.now().isoformat()
                        })
                        
    except Exception as e:
        print(f"Serebii scrape failed: {e}")
    
    return products

def save_candidates(products):
    """Save scraped products to file for review."""
    output_file = "<repo>/preorder_candidates.json"
    
    # Load existing candidates
    existing = []
    try:
        with open(output_file) as f:
            existing = json.load(f)
    except:
        pass
    
    # Merge with new products (avoid duplicates by title)
    existing_titles = {p['title'] for p in existing}
    new_products = [p for p in products if p['title'] not in existing_titles]
    
    all_products = existing + new_products
    
    # Save
    with open(output_file, 'w') as f:
        json.dump(all_products, f, indent=2)
    
    print(f"✅ Saved {len(all_products)} total candidates ({len(new_products)} new)")
    print(f"📁 File: {output_file}")
    
    if new_products:
        print("\n🆕 New products found:")
        for p in new_products[:5]:
            print(f"  - {p['title'][:80]} ({p['source']})")

if __name__ == "__main__":
    print("🔍 Scraping PokeBeach and Serebii for upcoming products...\n")
    
    pokebeach_products = scrape_pokebeach()
    print(f"PokeBeach: {len(pokebeach_products)} products found")
    time.sleep(2)  # Be polite
    
    serebii_products = scrape_serebii()
    print(f"Serebii: {len(serebii_products)} products found")
    
    all_products = pokebeach_products + serebii_products
    save_candidates(all_products)
