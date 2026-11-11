"""Tippmixpro WAMP-végpont próbája böngésző nélkül: mely lekérdezés adja a mai meccseket oddsokkal."""
import asyncio
import collections
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

import websockets

URL, OP, LANG = "wss://sportsapi.tippmixpro.hu/v2", "2901", "hu"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
HELLO = [1, "www.tippmixpro.hu", {"agent": "Wampy.js v6.2.2", "roles": {"caller": {"features": {}}, "subscriber": {"features": {}}}}]


async def main():
    os.makedirs(OUT, exist_ok=True)
    tz = ZoneInfo("Europe/Budapest")
    now = dt.datetime.now(tz)
    day0 = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    day1 = day0 + 86399
    async with websockets.connect(URL, subprotocols=["wamp.2.json"], origin="https://www.tippmixpro.hu",
                                  max_size=2 ** 26, open_timeout=30) as ws:
        await ws.send(json.dumps(HELLO))
        print("HELLO ->", (await ws.recv())[:200])
        rid = [100]

        async def call(topic, label):
            rid[0] += 1
            await ws.send(json.dumps([48, rid[0], {}, "/sports#initialDump", [], {"topic": topic}]))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 60))
                if msg[0] in (50, 8) and msg[1] == rid[0]:
                    break
            if msg[0] == 8:
                print(f"❌ {label}: {str(msg)[:300]}")
                return None
            recs = (msg[4] or {}).get("records") or []
            cnt = collections.Counter(r.get("_type") for r in recs)
            print(f"✅ {label} [{topic}]: {len(recs)} rekord {dict(cnt)}")
            json.dump(recs, open(os.path.join(OUT, f"tm_{label}.json"), "w", encoding="utf-8"), ensure_ascii=False)
            return recs

        cal = await call(f"/sports/{OP}/{LANG}/live-calendar-matches-by-timestamp/all-disciplines/{day0}-{day1}/500", "calendar")
        for loc in ("0", "all", "1"):
            await call(f"/sports/{OP}/{LANG}/next-matches-aggregator-main/1/{loc}/50/3", f"next_foci_{loc}")
        await call(f"/sports/{OP}/{LANG}/popular-matches-aggregator-main/1/0/1/3", "popular_foci")
        tours = await call(f"/sports/{OP}/{LANG}/tournaments/1", "tournaments_foci")
        # egy mai foci-meccs részletes oddsai
        matches = [r for r in (cal or []) if r.get("_type") == "MATCH" and r.get("sportId") == "1"]
        if matches:
            m = matches[0]
            print("Minta meccs:", m.get("name"), m.get("startTime"), m.get("parentId"))
            await call(f"/sports/{OP}/{LANG}/{m['id']}/match-odds/", "match_odds")
            for a, b in (("7", "1"), ("1", "1"), ("0", "0")):
                await call(f"/sports/{OP}/{LANG}/{m['parentId']}/tournament-odds/{a}/{b}", f"tournament_odds_{a}_{b}")
        # összefoglaló a piac-szerkezetről
        for f in ("match_odds", "tournament_odds_7_1"):
            p = os.path.join(OUT, f"tm_{f}.json")
            if not os.path.exists(p):
                continue
            recs = json.load(open(p, encoding="utf-8"))
            mk = [r for r in recs if r.get("_type") == "MARKET"]
            print(f"\n== {f}: piactípusok (bettingTypeId, név) – első 40:")
            seen = collections.OrderedDict()
            for r in mk:
                seen.setdefault((r.get("bettingTypeId"), r.get("eventPartId"), r.get("name")), 0)
            for k in list(seen)[:40]:
                print("  ", k)
            for t in ("OUTCOME", "BETTING_OFFER", "MARKET_OUTCOME_RELATION", "MARKET"):
                ex = next((r for r in recs if r.get("_type") == t), None)
                print(f"  minta {t}: {json.dumps(ex, ensure_ascii=False)[:700]}")


if __name__ == "__main__":
    asyncio.run(main())
