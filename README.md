# 🤖 Pokemon Restock Monitor

Automated monitor that sends iMessage alerts when Pokemon products come back in stock.

## 📁 Files

- `monitor.py` - Main monitoring script
- `config.json` - **EDIT THIS** with your settings
- `control.sh` - Easy control script
- `stock_status.json` - Auto-generated stock tracking
- `monitor.log` - Log file

## 🚀 Quick Start

### 1. Edit Config

Open `config.json` and update:

```json
{
  "phone": "+16041234567",  // ← YOUR PHONE NUMBER (iMessage)
  "check_interval": 30,      // seconds between checks
  "products": [
    {
      "name": "Product Name",
      "url": "https://...",   // ← Product URL
      "enabled": true         // ← Set to true to monitor
    }
  ]
}
```

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
3. Sends iMessage to your phone when stock comes back
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

**iMessage not sending:**
- Make sure Messages app is signed in
- Check phone number is correct (include +1)
- Try sending a test message manually first

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

1. Ask Peter to set up a LaunchAgent
2. Or add to System Preferences → Users & Login Items

## 💡 Tips

- Keep `check_interval` at 30+ seconds (don't hammer sites)
- Use specific product URLs, not homepage
- Test with `./control.sh test` before running in background
- Check logs if alerts aren't working

---

**Created by Peter for Jason** 🤖
