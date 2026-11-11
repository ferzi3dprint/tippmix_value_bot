"""Gyors API-ellenőrzés az élesítés előtt (Actions → API-teszt → Run workflow).

Kiírja: a havi keretet, hogy a Tippmixpro és a Pinnacle elérhető-e, és hogy az ingyenes historical-odds végpont ad-e
mai/holnapi meccsre friss oddsot mindkét irodától. Fizetős kérés: legfeljebb 3 (sports, markets, fixtures).
"""
import datetime as dt
import json

from common import load_config, now_utc
from freefeed import snapshot
from oddsapi import OddsPapi


def main():
    cfg = load_config()
    api = cfg["api"]
    c = OddsPapi(cfg)
    acc = c.account()
    print("Fiók / keret:", json.dumps(acc, ensure_ascii=False)[:300])

    sports = c.sports()
    soccer = next((s for s in sports if str(s.get("sportName", "")).lower() == "soccer"), None)
    print("Sportágak száma:", len(sports), "| Soccer:", soccer)
    print("Mezők egy sportnál:", list(sports[0].keys()) if sports else "-")

    now = now_utc()
    fx = c.fixtures(soccer["sportId"], now + dt.timedelta(hours=1), now + dt.timedelta(hours=30),
                    f"{api['soft_book']},{api['sharp_book']}")
    print(f"Foci meccsek a következő 30 órában (hasOdds): {len(fx)}")
    if fx:
        print("Mezők egy meccsnél:", list(fx[0].keys()))

    found = 0
    for f in fx[:12]:          # ingyenes végpont, 5 mp várakozás kérésenként
        h = c.historical_odds(f["fixtureId"], f"{api['soft_book']},{api['sharp_book']}")
        books = (h or {}).get("bookmakers") or {}
        tm, pin = snapshot(books.get(api["soft_book"])), snapshot(books.get(api["sharp_book"]))
        m_tm = set((tm or {}).get("markets", {}).keys())
        m_pin = set((pin or {}).get("markets", {}).keys())
        print(f"- {f.get('participant1Name')} – {f.get('participant2Name')} ({f.get('startTime')}): "
              f"Tippmix piacok {len(m_tm)}, Pinnacle piacok {len(m_pin)}, közös {len(m_tm & m_pin)}")
        if "101" in m_tm and "101" in m_pin:
            g = lambda b: [b["markets"]["101"]["outcomes"].get(o, {}).get("players", {}).get("0", {}).get("price") for o in ("101", "102", "103")]
            print(f"    1X2  Tippmix {g(tm)}  |  Pinnacle {g(pin)}")
            found += 1
        if found >= 3:
            break
    print("\nIngyenes kérések:", c.free_calls, "| fizetős kérések ebben a futásban:", c.calls)
    print("EREDMÉNY:", "✅ az ingyenes mód működik" if found else "⚠️ nem jött közös Tippmix+Pinnacle 1X2 odds – nézzük meg a naplót")
    print("Keret a végén:", json.dumps(c.account(), ensure_ascii=False)[:200])


if __name__ == "__main__":
    main()
