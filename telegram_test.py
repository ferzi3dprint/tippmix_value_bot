"""Telegram-próba: rövid üzenet a botról és az utolsó futásról (Actions → Telegram-teszt)."""
import json
import os

import notify

runs = []
try:
    runs = json.load(open(os.path.join("data", "runs.json"), encoding="utf-8"))
except Exception:
    pass
last = runs[-1] if runs else {}
lines = ["✅ A Tippmix value-bot csatlakozott a Telegramhoz.", "",
         "Mostantól innen jönnek az ajánlások (szimulált fogadások), és este 23 órakor a napi kimutatás."]
if last:
    lines += ["", f"Utolsó futás: {last.get('tippmix_matches', '?')} mai Tippmix-meccs, ebből {last.get('paired', '?')} párosítva a Pinnacle-lel, "
              f"{last.get('fixtures_both', '?')} részletesen összevetve, value-jelölt: {last.get('candidates', 0)}."]
    if last.get("top_ev"):
        lines += ["Legjobb EV-k (küszöbtől függetlenül):"] + [f"• {x}" for x in last["top_ev"][:3]]
lines += ["", "🔗 Dashboard: https://ferzi3dprint.github.io/tippmix_value_bot/"]
ok = notify.send("\n".join(lines))
print("Elküldve" if ok else "NEM ment el (hiányzó TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID?)")
