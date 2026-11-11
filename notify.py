"""Küldés Telegramra (vagy kiírás, ha DRY_RUN=1 vagy nincs token)."""
import os

import requests


def _split(text, limit=4000):
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def send(text):
    if os.getenv("NOTIFY_SILENT") == "1":  # demó: se küldés, se kiírás
        return False
    token, chat_id =os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if os.getenv("DRY_RUN") == "1" or not token or not chat_id:
        print("----- [Telegram] -----\n" + text + "\n----------------------")
        return False
    for chunk in _split(text):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}, timeout=30)
        r.raise_for_status()
    return True
