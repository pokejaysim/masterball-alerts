#!/usr/bin/env python3
"""Track Telegram subscriber count and notify on changes"""

import urllib.request
import urllib.parse
import json
import os

BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
CHANNEL_ID = "TELEGRAM_CHANNEL_ID"
JASON_CHAT_ID = "TELEGRAM_CHAT_ID"
STATE_FILE = "<repo>/subscriber_state.json"

def get_subscriber_count():
    """Get current subscriber count from Telegram API"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMemberCount?chat_id={CHANNEL_ID}"
    response = urllib.request.urlopen(url, timeout=10)
    data = json.loads(response.read())
    if data.get("ok"):
        return data["result"]
    return None

def send_telegram(message):
    """Send DM to Jason"""
    params = urllib.parse.urlencode({
        "chat_id": JASON_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    })
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?{params}"
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
    current_count = get_subscriber_count()
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
        send_telegram(msg)
        print(f"✅ Notified: {current_count} subscribers ({'+' if diff > 0 else ''}{diff})")
    elif prev_count == 0:
        # First run
        print(f"✅ Initial count tracked: {current_count}")
    else:
        print(f"✅ No change: {current_count} subscribers")

if __name__ == "__main__":
    main()
