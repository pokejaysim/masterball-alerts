#!/usr/bin/env python3
"""Track Telegram subscriber count and notify on changes"""

import urllib.request
import urllib.parse
import json
import os

from settings import MONITOR_DIR, load_config

STATE_FILE = os.path.join(MONITOR_DIR, "subscriber_state.json")

def get_subscriber_count(bot_token, channel_id):
    """Get current subscriber count from Telegram API"""
    url = f"https://api.telegram.org/bot{bot_token}/getChatMemberCount?chat_id={channel_id}"
    response = urllib.request.urlopen(url, timeout=10)
    data = json.loads(response.read())
    if data.get("ok"):
        return data["result"]
    return None

def send_telegram(bot_token, chat_id, message):
    """Send DM to Jason"""
    params = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    })
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage?{params}"
    try:
        urllib.request.urlopen(url, timeout=5)
    except:
        pass

def load_state():
    """Load previous subscriber count"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"count": 0, "last_notified": 0}

def save_state(state):
    """Save current state"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def main():
    config = load_config()
    bot_token = str(config.get("telegram_bot_token", "")).strip()
    channel_id = str(config.get("telegram_channel_id", "")).strip()
    chat_id = str(config.get("telegram_chat_id", "")).strip()

    if not bot_token or not channel_id or not chat_id:
        print("❌ Missing Telegram config. Set it in config.local.json or config.json.")
        return

    current_count = get_subscriber_count(bot_token, channel_id)
    if current_count is None:
        print("❌ Failed to get subscriber count")
        return
    
    state = load_state()
    prev_count = state.get("count", 0)
    
    # Always save current count
    state["count"] = current_count
    save_state(state)
    
    # Notify on change (growth or drop)
    if current_count != prev_count and prev_count > 0:
        diff = current_count - prev_count
        emoji = "🎉" if diff > 0 else "⚠️"
        msg = f"{emoji} <b>Telegram Subscriber Update</b>\n\n"
        msg += f"Current: {current_count} subscribers\n"
        msg += f"Change: {'+' if diff > 0 else ''}{diff}\n"
        msg += f"Previous: {prev_count}"
        send_telegram(bot_token, chat_id, msg)
        print(f"✅ Notified: {current_count} subscribers ({'+' if diff > 0 else ''}{diff})")
    elif prev_count == 0:
        # First run
        print(f"✅ Initial count tracked: {current_count}")
    else:
        print(f"✅ No change: {current_count} subscribers")

if __name__ == "__main__":
    main()
