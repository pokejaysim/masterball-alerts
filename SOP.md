# RestockBall — Standard Operating Procedure

## Overview

RestockBall is an automated Pokemon TCG restock monitoring system that tracks 45+ products across 5 Canadian retailers and sends instant Telegram notifications when products come back in stock from trusted sellers.

---

## System Architecture

```
┌─────────────────────────────────────────┐
│          Mac Mini (24/7 Server)          │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │       monitor.py (Python)        │   │
│  │                                  │   │
│  │  ┌──────────┐  ┌──────────────┐  │   │
│  │  │ Amazon   │  │  Best Buy    │  │   │
│  │  │ Checker  │  │  API Checker │  │   │
│  │  └──────────┘  └──────────────┘  │   │
│  │  ┌──────────┐  ┌──────────────┐  │   │
│  │  │ Walmart  │  │   Costco     │  │   │
│  │  │ Checker  │  │   Checker    │  │   │
│  │  └──────────┘  └──────────────┘  │   │
│  │  ┌──────────┐                    │   │
│  │  │ EB Games │                    │   │
│  │  │ Checker  │                    │   │
│  │  └──────────┘                    │   │
│  └──────────┬───────────────────────┘   │
│             │                           │
│             ▼                           │
│  ┌──────────────────────────────────┐   │
│  │     Telegram Bot API             │   │
│  │  @PokeJay_Stock_Bot              │   │
│  └──────────┬───────────────────────┘   │
└─────────────┼───────────────────────────┘
              │
              ▼
    ┌─────────────────┐    ┌─────────────────┐
    │  Jason's DM      │    │  PJS Restock    │
    │  (all alerts)    │    │  Alerts Channel │
    │                  │    │  (stock alerts  │
    │                  │    │   only)         │
    └─────────────────┘    └─────────────────┘
```

---

## File Locations

| File | Path | Description |
|------|------|-------------|
| Monitor script | `~/.openclaw/workspace/pokemon-monitor/monitor.py` | Main monitoring script |
| Config | `~/.openclaw/workspace/pokemon-monitor/config.json` | Products, API keys, settings |
| Control script | `~/.openclaw/workspace/pokemon-monitor/control.sh` | Start/stop/status commands |
| Stock status | `~/.openclaw/workspace/pokemon-monitor/stock_status.json` | Tracks current stock state |
| Logs | `~/.openclaw/workspace/pokemon-monitor/monitor.log` | Real-time monitoring logs |
| Error logs | `~/.openclaw/workspace/pokemon-monitor/monitor.err.log` | Error output |
| Log rotation | `~/.openclaw/workspace/pokemon-monitor/rotate-logs.sh` | Daily log rotation script |
| LaunchAgent | `~/Library/LaunchAgents/com.peter.pokemon-monitor.plist` | Auto-start on boot |
| Log rotation agent | `~/Library/LaunchAgents/com.peter.pokemon-log-rotation.plist` | Daily log rotation |
| Python venv | `~/.openclaw/workspace/pokemon-monitor-env/` | Virtual environment |

---

## Retailers & Detection Methods

### Amazon.ca
- **Method:** HTTP requests + BeautifulSoup HTML parsing
- **CAPTCHA Bypass:** Not needed (serves full HTML)
- **Stock Detection:**
  - "See all buying options" = Out of stock
  - "Ships from and sold by Amazon" = In stock (trusted)
  - "Sold by: [name]" = Check against trusted seller list
  - No seller detected = Skip (safety measure)
- **Trusted Sellers:** Amazon, Amazon.ca, The Pokemon Company, Pokemon Center
- **Special:** Direct "Add to Cart" link included in alerts

### Best Buy Canada
- **Method:** JSON API (`/api/v2/json/product/{SKU}`)
- **CAPTCHA Bypass:** Not needed (open API)
- **Stock Detection:**
  - `buttonState: "AddToCart"` + `isPurchasable: true` + `isAvailableOnline: true` = In stock
  - `seller.name` checked against trusted list
- **Trusted Sellers:** Best Buy, Best Buy Canada
- **Note:** Website (HTML) blocks bots, but API is open

### Walmart.ca
- **Method:** curl_cffi (chrome131 TLS fingerprint) + __NEXT_DATA__ JSON parsing
- **CAPTCHA Bypass:** curl_cffi impersonating chrome131
- **Stock Detection:**
  - `"availabilityStatus": "IN_STOCK"` = Available
  - `"sellerName"` checked against trusted list
- **Trusted Sellers:** Walmart, Walmart Canada, Walmart.ca
- **Note:** PerimeterX bot protection — only works with curl_cffi

### Costco.ca
- **Method:** curl_cffi (chrome131) + JSON-LD structured data
- **CAPTCHA Bypass:** curl_cffi impersonating chrome131
- **Stock Detection:**
  - `schema.org/InStock` = Available
  - `schema.org/OutOfStock` = Not available
- **Seller Filtering:** Not needed (Costco sells direct only)

### EB Games Canada
- **Method:** curl_cffi (chrome131) + HTML parsing
- **CAPTCHA Bypass:** curl_cffi impersonating chrome131
- **Stock Detection:**
  - "Add to Cart" present + no "pre-order" / "out of stock" = In stock
  - "Pre-order" = Not yet available (don't alert)
- **Seller Filtering:** Not needed (direct retailer)

---

## Priority System

Products are assigned priority tiers:

### High Priority (checked every cycle ~30 sec)
- All SPC products
- All ETB products
- All Costco products
- First Partner Illustration Collection

### Normal Priority (rotating batch of 8 per cycle)
- Booster Bundles, Booster Packs, Mini Tins, Poster Collections, etc.

**Rationale:** High-demand items (SPC, ETB) sell out in seconds to minutes. Normal items typically stay in stock longer.

---

## Telegram Configuration

| Setting | Value |
|---------|-------|
| Bot Name | RestockBall |
| Bot Username | @PokeJay_Stock_Bot |
| Bot Token | `TELEGRAM_BOT_TOKEN` |
| Jason's Chat ID | `TELEGRAM_CHAT_ID` |
| Channel Name | RestockBall |
| Channel ID | `TELEGRAM_CHANNEL_ID` |

### Notification Routing

| Event | Jason's DM | Channel |
|-------|-----------|---------|
| Stock Alert | ✅ | ✅ |
| Out of Stock | ✅ | ❌ |
| Monitor Started | ✅ | ❌ |

---

## Alert Format

```
🚨 Amazon CA Restock Alert ⚡
━━━━━━━━━━━━━━━━━

PE SPC - Amazon.ca

Type
Restock

ASIN
B0DYR8D7YC

Links
Product Page | Add to Cart

⚡ Act fast — limited stock!
```

---

## Daily Operations

### Monitor is Automatic
- Runs 24/7 via macOS LaunchAgent
- Auto-restarts on crash or reboot
- Log rotation runs daily at 3 AM
- No manual intervention needed

### Checking Monitor Status
```bash
cd ~/.openclaw/workspace/pokemon-monitor

# Check if running
./control.sh status

# View live logs
./control.sh logs
# or
tail -f monitor.log

# Check recent alerts
grep "🚨" monitor.log | tail -10

# Check which sellers are being blocked
grep "⚠️.*sold by" monitor.log | tail -20
```

### Restart Monitor
```bash
# Via control script
./control.sh restart

# Via launchctl
launchctl unload ~/Library/LaunchAgents/com.peter.pokemon-monitor.plist
launchctl load ~/Library/LaunchAgents/com.peter.pokemon-monitor.plist
```

### Stop Monitor
```bash
./control.sh stop
# or
launchctl unload ~/Library/LaunchAgents/com.peter.pokemon-monitor.plist
```

---

## Adding New Products

### Option 1: Ask Peter (Easiest)
Tell Peter the product URL in Discord and he'll add it and restart the monitor.

### Option 2: Edit config.json manually
```bash
cd ~/.openclaw/workspace/pokemon-monitor
nano config.json
```

Add a new product entry:
```json
{
  "name": "Product Name - Retailer",
  "url": "https://retailer.ca/product-url",
  "enabled": true,
  "priority": "high"
}
```

Priority options: `"high"` (every cycle) or `"normal"` (rotating)

Then restart:
```bash
launchctl unload ~/Library/LaunchAgents/com.peter.pokemon-monitor.plist
launchctl load ~/Library/LaunchAgents/com.peter.pokemon-monitor.plist
```

### Supported URL Formats
- **Amazon:** `https://www.amazon.ca/dp/ASIN`
- **Best Buy:** `https://www.bestbuy.ca/en-ca/product/product-name/SKU`
- **Walmart:** `https://www.walmart.ca/en/ip/product-name/PRODUCT_ID`
- **Costco:** `https://www.costco.ca/p/-/product-name/PRODUCT_ID`
- **EB Games:** `https://www.ebgames.ca/Trading%20Cards/Games/ID/product-name`

---

## Adding New Users (Telegram Channel)

1. Share the RestockBall channel invite link
2. User joins the channel
3. They automatically receive all stock alerts
4. No config changes needed

### Adding Individual DM Users
1. User messages @PokeJay_Stock_Bot on Telegram
2. Peter grabs their chat ID from the bot API
3. Update monitor.py to send to additional chat IDs
4. Restart monitor

---

## Troubleshooting

### Monitor Not Running
```bash
# Check process
ps aux | grep monitor.py

# Check LaunchAgent
launchctl list | grep pokemon

# Restart
launchctl unload ~/Library/LaunchAgents/com.peter.pokemon-monitor.plist
launchctl load ~/Library/LaunchAgents/com.peter.pokemon-monitor.plist
```

### No Alerts Being Sent
1. Check logs: `tail -50 monitor.log`
2. Look for errors: `tail -20 monitor.err.log`
3. Verify Telegram bot token is valid
4. Test manually: Ask Peter to send a test ping

### Walmart Returning 404/520 Errors
- Rate limiting — Walmart blocks too many requests
- The monitor handles this gracefully and retries next cycle
- If persistent, increase `check_interval` in config.json

### Costco Returning Small Pages
- CAPTCHA may have triggered
- curl_cffi profile may need updating
- Check if `chrome131` profile still works

### Amazon CAPTCHA
- Usually temporary — clears after a few minutes
- If persistent, add delay between Amazon checks
- Rotate User-Agent strings

### False Alerts
- Check seller filtering in logs
- Verify "trusted sellers" list is correct
- Test the specific product URL manually

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.14 |
| HTTP Client | requests + curl_cffi |
| HTML Parsing | BeautifulSoup4 |
| TLS Fingerprinting | curl_cffi (chrome131 impersonation) |
| Notifications | Telegram Bot API |
| Process Manager | macOS LaunchAgent |
| Virtual Environment | Python venv |
| Logging | Unbuffered stdout → file |
| Log Rotation | Custom bash script + LaunchAgent |

### Python Packages
- `requests` — HTTP client (Amazon, Best Buy)
- `beautifulsoup4` — HTML parsing
- `curl_cffi` — TLS fingerprint impersonation (Walmart, Costco, EB Games)
- `playwright` — Installed but not currently used (reserved for future)

---

## Product Inventory (as of March 24, 2026)

### Prismatic Evolutions (PE) — 28 products
| Product | Amazon | Best Buy | Walmart | Costco | EB Games |
|---------|--------|----------|---------|--------|----------|
| SPC | ✅ | ✅ | — | — | — |
| ETB | ✅ | — | ✅ | ✅ (bundle) | — |
| PC ETB | ✅ | — | — | — | — |
| Booster Bundle | ✅ | — | ✅ | — | ✅ |
| Booster Pack | ✅ | — | — | — | — |
| Binder Collection | ✅ | — | — | — | — |
| Two-Booster Blister | ✅ | — | — | — | — |
| Premium Figure | ✅ | — | ✅ | — | ✅ |
| Accessory Pouch | ✅ | — | ✅ | — | — |
| Lucario/Tyranitar | ✅ | — | — | — | — |
| Surprise Box | — | ✅ | ✅ | — | ✅ |
| Poster Collection | — | ✅ | ✅ | — | — |
| Mini Tin | — | ✅ | — | — | ✅ |

### Ascended Heroes (AH) — 14 products
| Product | Amazon | Walmart | EB Games |
|---------|--------|---------|----------|
| ETB | ✅ | ✅ | ✅ |
| Booster Bundle | ✅ | — | — |
| Booster Pack | ✅ | — | — |
| 2-Pack Blister | ✅ | ✅ | ✅ |
| Poster Collection | ✅ | ✅ | ✅ |
| Tech Sticker | ✅ | ✅ | ✅ |
| Mini Tin | — | — | ✅ |
| Deluxe Pin | — | ✅ | ✅ |
| First Partners Pin | — | ✅ | — |

### Other — 3 products
| Product | Amazon | Walmart |
|---------|--------|---------|
| First Partner Illustration S1 | ✅ | ✅ |
| Mega Charizard X UPC | — | — (Costco ✅) |
| Pokemon Day 2026 Collection | ✅ | — |

---

## Future Enhancements

### Short Term
- [ ] Add price tracking (alert if price drops below threshold)
- [ ] Add more Best Buy SKUs via API discovery
- [ ] Build simple web dashboard to view status
- [ ] Add SPC to Costco when link becomes available

### Medium Term
- [ ] Convert to Telegram channel as primary (SaaS foundation)
- [ ] Add user subscription management
- [ ] Add free tier (delayed alerts) vs premium (instant)
- [ ] Build landing page / website

### Long Term
- [ ] Full SaaS product with web dashboard
- [ ] Mobile app
- [ ] Multi-country support (US, UK)
- [ ] Auto-checkout integration
- [ ] Browser extension

---

## Key Dates

| Date | Event |
|------|-------|
| March 24, 2026 | System built and deployed |
| March 24, 2026 | First real catch — PE ETB from Walmart! |
| March 27, 2026 | Costco PE SPC drop (rumoured) |
| March 27, 2026 | AH Mega Lucario Poster Collection release |

---

## Contact

- **Built by:** Peter (AI assistant on Mac Mini)
- **Operated by:** Jason (pokejaysim)
- **Discord:** #peter-mac-mini channel
- **Telegram Bot:** @PokeJay_Stock_Bot
- **Telegram Channel:** RestockBall

---

*Last updated: March 24, 2026*
