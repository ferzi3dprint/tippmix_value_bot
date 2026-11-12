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


CHAT_FILE = os.path.join(os.getenv("DATA_DIR", "data"), "telegram_chat.json")
BAD_CHAT = ("bot can't send messages to the bot", "chat not found", "initiate conversation")


def discover_chat_id(token):
    """A botnak írt legutóbbi privát üzenetből kiolvassa a felhasználó chat-azonosítóját (getUpdates)."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
    except Exception:
        return None
    for u in reversed(r.get("result") or []):
        chat = (u.get("message") or u.get("edited_message") or {}).get("chat") or {}
        if chat.get("type") == "private" and chat.get("id"):
            return chat["id"]
    return None


def _saved():
    import json
    try:
        return json.load(open(CHAT_FILE, encoding="utf-8"))["chat_id"]
    except Exception:
        return None


def _remember(chat_id):
    import json
    os.makedirs(os.path.dirname(CHAT_FILE) or ".", exist_ok=True)
    json.dump({"chat_id": chat_id}, open(CHAT_FILE, "w", encoding="utf-8"))


def send(text, _retry=True, buttons=None):
    """buttons: [(felirat, url), …] – Telegram-gombok az üzenet alatt (soronként egy)."""
    if os.getenv("NOTIFY_SILENT") == "1":  # demó: se küldés, se kiírás
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = (_saved() or os.getenv("TELEGRAM_CHAT_ID")) if token else None
    if token and not chat_id:
        chat_id = discover_chat_id(token)
        if chat_id:
            _remember(chat_id)
    if os.getenv("DRY_RUN") == "1" or not token or not chat_id:
        print("----- [Telegram] -----\n" + text + "\n----------------------")
        return False
    for chunk in _split(text):
        body = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
        if buttons:
            body["reply_markup"] = {"inline_keyboard": [[{"text": t, "url": u}] for t, u in buttons]}
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body, timeout=30)
        if r.status_code != 200:
            try:
                desc = r.json().get("description")
            except Exception:
                desc = r.text[:200]
            if _retry and any(b in str(desc) for b in BAD_CHAT):
                found = discover_chat_id(token)
                if found and str(found) != str(chat_id):
                    print("[Telegram] a beállított chat ID nem jó – a botnak írt üzenetből megvan a helyes, azzal küldöm")
                    _remember(found)
                    return send(text, _retry=False, buttons=buttons)
            print(f"[Telegram] HIBA {r.status_code}: {desc}")
            if any(b in str(desc) for b in BAD_CHAT):
                print("[Telegram] Tipp: nyisd meg a botot a Telegramban és küldj neki egy üzenetet – a következő futás megtalálja a chat-azonosítódat.")
            return False
    return True
