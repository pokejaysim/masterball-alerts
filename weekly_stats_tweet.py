#!/usr/bin/env python3
"""MasterBall Alerts — Weekly Stats Tweet (Sundays 6 PM)"""

import sqlite3
import os
import tweepy
from datetime import datetime, timedelta

MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(MONITOR_DIR, "masterball.db")
ENV_FILE = os.path.join(MONITOR_DIR, ".env.twitter")


def get_weekly_stats():
    """Pull stats from the last 7 days"""
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    
    # Total restocks caught (in_stock alerts only)
    cur.execute(
        "SELECT COUNT(*) FROM alert_history WHERE alert_type='in_stock' AND timestamp > ?",
        (week_ago,)
    )
    total_alerts = cur.fetchone()[0]
    
    # Alerts by retailer
    cur.execute("""
        SELECT retailer, COUNT(*) FROM alert_history 
        WHERE alert_type='in_stock' AND timestamp > ?
        GROUP BY retailer ORDER BY COUNT(*) DESC
    """, (week_ago,))
    by_retailer = cur.fetchall()
    
    # Products tracked
    cur.execute("SELECT COUNT(DISTINCT product_name) FROM stock_status")
    products_tracked = cur.fetchone()[0]
    
    # Total checks this week
    cur.execute(
        "SELECT SUM(checks_total) FROM daily_stats WHERE date > ?",
        (week_ago,)
    )
    total_checks = cur.fetchone()[0] or 0
    
    db.close()
    
    return {
        "total_alerts": total_alerts,
        "by_retailer": by_retailer,
        "products_tracked": products_tracked,
        "total_checks": total_checks,
    }


def build_tweet(stats):
    """Build the weekly stats tweet"""
    total = stats["total_alerts"]
    checks = stats["total_checks"]
    products = stats["products_tracked"]
    
    # Format retailer breakdown
    retailer_parts = []
    retailer_names = {"amazon": "Amazon", "walmart": "Walmart", "bestbuy": "Best Buy", "costco": "Costco", "ebgames": "EB Games"}
    for retailer, count in stats["by_retailer"]:
        name = retailer_names.get(retailer, retailer or "Unknown")
        retailer_parts.append(f"{name} ({count})")
    
    retailer_line = " | ".join(retailer_parts) if retailer_parts else "No restocks this week"
    
    if total == 0:
        tweet = (
            f"📊 MasterBall Weekly Report\n\n"
            f"🔍 {checks:,} checks across {products} products\n"
            f"📦 No restocks caught this week — shelves are dry!\n\n"
            f"We're watching 24/7 so you don't have to.\n\n"
            f"Free alerts → masterballalerts.ca 🇨🇦"
        )
    else:
        tweet = (
            f"📊 MasterBall Weekly Report\n\n"
            f"✅ {total} restocks caught this week\n"
            f"🔍 {checks:,} checks across {products} products\n"
            f"🏪 {retailer_line}\n\n"
            f"Free alerts → masterballalerts.ca 🇨🇦\n\n"
            f"#PokemonTCG #PrismaticEvolutions #Canada"
        )
    
    return tweet


def post_tweet(tweet_text):
    """Post to Twitter"""
    keys = {}
    with open(ENV_FILE) as f:
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
    
    response = client.create_tweet(text=tweet_text)
    return response.data['id']


def main():
    print(f"📊 Weekly Stats Tweet — {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    
    stats = get_weekly_stats()
    tweet = build_tweet(stats)
    
    print(f"\nTweet ({len(tweet)} chars):")
    print(tweet)
    print()
    
    try:
        tweet_id = post_tweet(tweet)
        print(f"✅ Posted! https://twitter.com/MasterBallCA/status/{tweet_id}")
    except Exception as e:
        print(f"❌ Failed to post: {e}")


if __name__ == "__main__":
    main()
