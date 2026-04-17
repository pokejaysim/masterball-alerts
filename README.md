# 🤖 Pokemon Restock Monitor

Automated monitor that sends Telegram alerts when Pokemon products come back in stock.

## 📁 Files

- `monitor.py` - Main monitoring script
- `config.json` - Public-safe defaults and product list
- `config.local.json` - Your local Telegram and API tokens (ignored by git)
- `control.sh` - Easy control script
- `stock_status.json` - Auto-generated stock tracking
- `monitor.log` - Log file

## 🚀 Quick Start

### 1. Fill In Local Secrets

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

### 2. Run the Monitor

```bash
# Start in background
./control.sh start

# View logs
./control.sh logs

# Check status
./control.sh status

# Stop monitor
./control.sh stop

# Test run (foreground)
./control.sh test
```

## 📱 How It Works

1. Script checks product URLs every 30 seconds (configurable)
2. Detects when "Add to Cart" or similar text appears
3. Sends Telegram alerts when stock comes back
4. Also sends macOS notification

## ✏️ Adding Products

Edit `config.json`:

```json
{
  "name": "Prismatic Evolutions SPC",
  "url": "https://www.costco.ca/product-link",
  "enabled": true
}
```

## 🔧 Troubleshooting

**Telegram not sending:**
- Make sure `config.local.json` has the right bot token and chat ID
- Confirm the bot can message your account/channel
- Try a manual Telegram API test if needed

**Monitor not detecting stock:**
- Some sites need custom detection logic
- Check `monitor.log` for errors
- Ask Peter to add site-specific support

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
