#!/usr/bin/env python3
"""Telegram owner commands for product discovery review."""

import requests

from database import (
    get_bot_state,
    list_candidates,
    set_bot_state,
    set_candidate_status,
)
from product_utils import escape_html, retailer_display_name


def _send(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=10,
    )


def _format_candidate(candidate):
    return (
        f"<b>{escape_html(candidate['id'])}</b> "
        f"{escape_html(retailer_display_name(candidate['retailer']))}\n"
        f"{escape_html(candidate['name'])}\n"
        f"{escape_html(candidate['priority'])} priority | confidence {float(candidate.get('confidence') or 0):.2f}\n"
        f"{escape_html(candidate['url'])}"
    )


def send_pending_summary(bot_token, chat_id, limit=10):
    pending = list_candidates(status="pending", limit=limit)
    if not pending:
        _send(bot_token, chat_id, "✅ No pending product discoveries.")
        return

    lines = ["🔍 <b>Pending Product Discoveries</b>", ""]
    for candidate in pending:
        lines.append(_format_candidate(candidate))
        lines.append(f"/approve {candidate['id']}  |  /ignore {candidate['id']}")
        lines.append("")
    _send(bot_token, chat_id, "\n".join(lines).strip())


def process_review_commands(bot_token, owner_chat_id, log_func=print):
    """Poll Telegram updates and apply owner-only discovery commands.

    Returns True when an approval/ignore command changed candidate status.
    """
    offset = get_bot_state("telegram_update_offset")
    params = {"timeout": 0, "allowed_updates": '["message"]'}
    if offset:
        params["offset"] = int(offset)

    response = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getUpdates",
        params=params,
        timeout=10,
    )
    data = response.json()
    if not data.get("ok"):
        log_func(f"Telegram getUpdates failed: {data}")
        return False

    changed = False
    max_update_id = None
    owner_chat_id_str = str(owner_chat_id)

    for update in data.get("result", []):
        max_update_id = max(max_update_id or update["update_id"], update["update_id"])
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip()

        if str(chat.get("id")) != owner_chat_id_str:
            continue

        parts = text.split()
        if not parts:
            continue

        command = parts[0].split("@", 1)[0].lower()
        candidate_id = parts[1].strip().lower() if len(parts) > 1 else ""

        if command == "/pending":
            send_pending_summary(bot_token, owner_chat_id)
        elif command in {"/approve", "/ignore"}:
            if not candidate_id:
                _send(bot_token, owner_chat_id, f"Usage: {command} abc123")
                continue
            status = "approved" if command == "/approve" else "ignored"
            candidate = set_candidate_status(candidate_id, status, reason=f"Telegram {command[1:]}")
            if not candidate:
                _send(bot_token, owner_chat_id, f"⚠️ Candidate <b>{escape_html(candidate_id)}</b> was not found.")
                continue
            changed = True
            if status == "approved":
                _send(
                    bot_token,
                    owner_chat_id,
                    "✅ <b>Approved for monitoring</b>\n\n" + _format_candidate(candidate),
                )
            else:
                _send(
                    bot_token,
                    owner_chat_id,
                    "🧹 <b>Ignored discovery</b>\n\n" + _format_candidate(candidate),
                )
        elif command == "/discover":
            _send(
                bot_token,
                owner_chat_id,
                "Run <code>./control.sh discover-now</code> on the Mac Mini to scan retailers now.",
            )

    if max_update_id is not None:
        set_bot_state("telegram_update_offset", max_update_id + 1)

    return changed
