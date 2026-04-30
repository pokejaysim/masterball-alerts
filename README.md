# 🤖 Pokemon Restock Monitor

Automated monitor that sends Telegram alerts when Pokemon products come back in stock.

## 📁 Files

- `monitor.py` - Main monitoring script
- `config.json` - Public-safe defaults and product list
- `config.local.json` - Your local Telegram and API tokens (ignored by git)
- `control.sh` - Easy control script
- `status_page.py` - Private local health dashboard
- `stock_status.json` - Auto-generated stock tracking
- `monitor.log` - Log file

## 🚀 Quick Start

### 1. Bootstrap Local Setup

```bash
./control.sh bootstrap
```

This creates/updates the local virtual environment, installs Python dependencies, installs Playwright Chromium for selective browser checks, initializes SQLite, and runs `doctor`.

### 2. Fill In Local Secrets

Keep `config.json` checked in with blank secrets, then create `config.local.json` for anything private:

```json
{
  "telegram_bot_token": "your-bot-token",
  "telegram_chat_id": "your-chat-id",
  "telegram_channel_id": "optional-channel-id",
  "pricecharting_token": "optional-pricecharting-token"
}
```

Optional Walmart proxy credentials live in `walmart_proxy.local.json`.

You can copy the shape from `config.local.example.json` and `walmart_proxy.local.example.json`.

### 3. Run the Monitor

```bash
# Start in background
./control.sh start

# View logs
./control.sh logs

# Check status
./control.sh status

# Start/open private local status page
./control.sh dashboard-open

# Stop monitor
./control.sh stop

# Test run (foreground)
./control.sh test
```

## 🔍 New Product Discovery

Discovery now runs automatically from the monitor. Defaults in `config.json`:

- `auto_run`: enabled
- `auto_run_interval_minutes`: every 3 hours
- `auto_approve`: enabled for high-confidence matches
- `auto_approve_retailers`: Costco, Best Buy, EB Games, Walmart

Amazon and Pokemon Center still stay review-first by default because they are noisier/protected. Walmart is automatic only after protected validation succeeds.

Run discovery manually any time:

```bash
./control.sh discover-now
```

The scanner looks for Canada-first sealed Pokemon TCG products across Walmart.ca, Costco.ca, Best Buy Canada, EB Games/GameStop Canada, Amazon.ca, and Pokemon Center Canada. New products are stored in SQLite as a review queue and sent to your owner Telegram chat.

Approve or ignore from Telegram:

```text
/approve abc123
/ignore abc123
/pending
```

Approved products are loaded by the monitor without editing `config.json`.

To skip manual review for high-confidence retailer matches:

```bash
./control.sh discover-auto-add
```

Auto-add uses the guardrails in `config.json` under `discovery`: minimum confidence `0.82`, default retailers Costco, Best Buy, EB Games, and Walmart. Walmart candidates must pass a live protected validation first; blocked or unclear Walmart pages stay pending.

## 🛒 Walmart Protected Lane

Walmart.ca checks are proxy-gated and browser-confirmed before a stock alert can fire.

```bash
./control.sh doctor-walmart
./control.sh discover-walmart-dry-run
./control.sh test-product "https://www.walmart.ca/en/ip/example/12345678"
```

Put residential proxy credentials in `walmart_proxy.local.json`:

```json
{
  "proxy_url": "http://user:pass@host:port",
  "enabled": true
}
```

If the proxy is missing or Walmart blocks validation, Walmart products are not marked sold out and new discoveries stay pending.

## 📱 How It Works

1. Script checks product URLs every 30 seconds (configurable)
2. Distinguishes `in_stock`, `out_of_stock`, `unknown`, `blocked`, `preorder`, and `marketplace`
3. Sends Telegram alerts when stock comes back
4. Preserves previous state when a site blocks or returns an unclear page
5. Uses selective browser checks for high-priority protected products

## ✏️ Adding Products

Preferred: run discovery and approve candidates from Telegram.

Manual seed products still live in `config.json`:

```json
{
  "name": "Prismatic Evolutions SPC",
  "url": "https://www.costco.ca/product-link",
  "enabled": true
}
```

## 🧪 Health Checks

```bash
./control.sh doctor
./control.sh doctor-retailers
./control.sh test-product "https://www.bestbuy.ca/en-ca/product/example/12345678"
./control.sh discover-dry-run
```

## 🚦 Private Status Page

The status page is an internal Mac Mini tool. It binds to `127.0.0.1` by default, so it is not public-facing.

```bash
./control.sh dashboard-start
./control.sh dashboard-open
./control.sh dashboard-status
./control.sh status-json
```

Open: `http://127.0.0.1:8787`

It shows whether the monitor LaunchAgent is running, how fresh the logs are, active product counts, discovery queue counts, retailer degradation signals, and the recent log tail. If the monitor is down or stale, it shows the exact control commands to run next.

The page also shows Walmart lane state, active Walmart product count, pending Walmart validation count, and whether the Walmart proxy is configured.

## 🔧 Troubleshooting

**Telegram not sending:**
- Make sure `config.local.json` has the right bot token and chat ID
- Confirm the bot can message your account/channel
- Try a manual Telegram API test if needed

**Monitor not detecting stock:**
- Some sites need custom detection logic
- Check `monitor.log` for errors
- Run `./control.sh test-product "<url>"` to inspect one product

## 📊 Logs

View live logs:
```bash
./control.sh logs
# or
tail -f monitor.log
```

## 🛑 Stopping

```bash
./control.sh stop
```

## 🔄 Auto-Start on Boot

To make it start automatically when Mac Mini boots:

1. Add your own LaunchAgent
2. Or add to System Preferences → Users & Login Items

## 💡 Tips

- Keep `check_interval` at 30+ seconds (don't hammer sites)
- Use specific product URLs, not homepage
- Test with `./control.sh test` before running in background
- Check logs if alerts aren't working

---

**Created by Peter for Jason** 🤖
