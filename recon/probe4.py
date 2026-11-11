"""Próba 4: javított párosítás, tenisz-piacok a Tippmixen, OddsPapi piacazonosítók, Pinnacle-minta."""
import asyncio
import collections
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import load_config  # noqa: E402
from freefeed import snapshot  # noqa: E402
from matcher import match_fixtures  # noqa: E402
from oddsapi import OddsPapi  # noqa: E402

URL, OP, LANG = "wss://sportsapi.tippmixpro.hu/v2", "2901", "hu"
TZ = ZoneInfo("Europe/Budapest")


async def tm_fetch(match_ids_for_odds):
    now = dt.datetime.now(TZ)
    t0, t1 = int(now.timestamp()), int(now.replace(hour=23, minute=59, second=59).timestamp())
    res, rid = {"matches": [], "odds": {}}, 100
    async with websockets.connect(URL, subprotocols=["wamp.2.json"], origin="https://www.tippmixpro.hu", max_size=2 ** 26) as ws:
        await ws.send(json.dumps([1, "www.tippmixpro.hu", {"roles": {"caller": {"features": {}}}}]))
        await ws.recv()

        async def call(topic):
            nonlocal rid
            rid += 1
            await ws.send(json.dumps([48, rid, {}, "/sports#initialDump", [], {"topic": topic}]))
            try:
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 25))
                    if m[0] in (50, 8) and m[1] == rid:
                        return ((m[4] or {}).get("records") or []) if m[0] == 50 else None
            except asyncio.TimeoutError:
                return None
        seen = {}
        for a in range(t0, t1, 7200):
            for r in await call(f"/sports/{OP}/{LANG}/live-calendar-matches-by-timestamp/all-disciplines/{a}-{min(a + 7199, t1)}/500") or []:
                if r.get("_type") == "MATCH":
                    seen[r["id"]] = r
        res["matches"] = list(seen.values())
        tennis = []
        for m in tennis:
            res["odds"][m["id"]] = await call(f"/sports/{OP}/{LANG}/{m['id']}/match-odds/market-group/Main_BannerMatrix")
    return res


def main():
    cfg = load_config()
    c = OddsPapi(cfg)
    tm = asyncio.run(tm_fetch([]))
    # tenisz piacok
    for mid, recs in tm["odds"].items():
        print("== Tippmix tenisz-meccs piacai:")
        for r in recs or []:
            if r.get("_type") == "MARKET":
                print("  ", r.get("bettingTypeId"), r.get("eventPartId"), r.get("name"), r.get("numberOfOutcomes"), r.get("paramFloat1"))
        outs = {r["id"]: r for r in recs or [] if r.get("_type") == "OUTCOME"}
        print("  kimenetel-kódok:", sorted({(o.get("typeId"), o.get("code")[:3]) for o in outs.values()})[:20])
    # OddsPapi piacok
    mk = c.markets()
    for sid in (12,):
        rows = [m for m in mk if m.get("sportId") == sid and not m.get("playerProp")]
        print(f"\n== OddsPapi piacok sport {sid} (első 40):")
        for m in rows[:40]:
            print("  ", m["marketId"], m.get("marketType"), m.get("handicap"), m.get("period"), m.get("marketName"), [(o["outcomeId"], o["outcomeName"]) for o in m["outcomes"]])
    m101 = next(m for m in mk if m["marketId"] == 101)
    print("  101 kimenetelek:", [(o["outcomeId"], o["outcomeName"]) for o in m101["outcomes"]])
    m1010 = next(m for m in mk if m["marketId"] == 1010)
    print("  1010 kimenetelek:", [(o["outcomeId"], o["outcomeName"]) for o in m1010["outcomes"]])
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"), exist_ok=True)
    json.dump(mk, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "oddspapi_markets.json"), "w"), ensure_ascii=False)
    # párosítás
    now = dt.datetime.now(dt.timezone.utc)
    end = dt.datetime.now(TZ).replace(hour=23, minute=59).astimezone(dt.timezone.utc)
    for sid, hu in ((10, "Labdarúgás"), (12, "Tenisz")):
        fx = c.fixtures(sid, now, end, "pinnacle")
        tms = [{"id": m["id"], "home": m["homeParticipantName"], "away": m["awayParticipantName"],
                "start": dt.datetime.fromtimestamp(m["startTime"] / 1000, dt.timezone.utc), "name": m["name"]}
               for m in tm["matches"] if m["sportName"] == hu and m["startTime"] / 1000 > now.timestamp()]
        pairs = match_fixtures(tms, fx)
        print(f"\n== {hu}: párosítva {len(pairs)}/{len(tms)} (Pinnacle: {len(fx)})")
        for m in tms:
            p = pairs.get(m["id"])
            print(f"  {'✅' if p else '❌'} {m['name']}  <->  {p[0]['participant1Name'] + ' - ' + p[0]['participant2Name'] + f' ({p[1]})' if p else '-'}")
        if pairs:
            f = next(iter(pairs.values()))[0]
            h = c.historical_odds(f["fixtureId"], "pinnacle")
            snap = snapshot((h.get("bookmakers") or {}).get("pinnacle"))
            print("  Pinnacle-minta piacai:", sorted((snap or {}).get("markets", {}).keys(), key=int)[:60])
    print("Fizetős kérések:", c.calls, "ingyenes:", c.free_calls)


if __name__ == "__main__":
    main()
