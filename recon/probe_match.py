"""Próba 3: teljes mai Tippmix-meccslista + párosítás az OddsPapi (Pinnacle) mai meccseivel."""
import asyncio
import collections
import datetime as dt
import difflib
import json
import os
import re
import sys
import unicodedata
from zoneinfo import ZoneInfo

import websockets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import load_config  # noqa: E402
from oddsapi import OddsPapi  # noqa: E402

URL, OP, LANG = "wss://sportsapi.tippmixpro.hu/v2", "2901", "hu"
HELLO = [1, "www.tippmixpro.hu", {"agent": "Wampy.js v6.2.2", "roles": {"caller": {"features": {}}}}]
TZ = ZoneInfo("Europe/Budapest")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|sc|ac|fk|sk|nk|cd|club|de|u\d\d|women|w|noi|\(w\))\b", " ", s)
    return re.sub(r"[^a-z0-9 ]", " ", s).split()


def sim(a, b):
    a, b = " ".join(norm(a)), " ".join(norm(b))
    return difflib.SequenceMatcher(None, a, b).ratio()


async def tm_calendar():
    now = dt.datetime.now(TZ)
    d0 = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    out, rid = {}, 100
    async with websockets.connect(URL, subprotocols=["wamp.2.json"], origin="https://www.tippmixpro.hu", max_size=2 ** 26) as ws:
        await ws.send(json.dumps(HELLO))
        await ws.recv()

        async def call(topic):
            nonlocal rid
            rid += 1
            await ws.send(json.dumps([48, rid, {}, "/sports#initialDump", [], {"topic": topic}]))
            try:
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 25))
                    if m[0] in (50, 8) and m[1] == rid:
                        return (m[4] or {}).get("records") or [] if m[0] == 50 else None
            except asyncio.TimeoutError:
                return None
        for h in range(0, 24, 2):
            recs = await call(f"/sports/{OP}/{LANG}/live-calendar-matches-by-timestamp/all-disciplines/{d0 + h * 3600}-{d0 + (h + 2) * 3600 - 1}/500")
            n = 0
            for r in recs or []:
                if r.get("_type") == "MATCH":
                    out[r["id"]] = r
                    n += 1
            print(f"  naptár {h:02d}-{h + 2:02d}h: {n} meccs")

    return list(out.values())


def main():
    tm = asyncio.run(tm_calendar())
    print("Tippmix mai meccsek sportáganként:", dict(collections.Counter(m["sportName"] for m in tm)))
    cfg = load_config()
    c = OddsPapi(cfg)
    now = dt.datetime.now(dt.timezone.utc)
    end = dt.datetime.now(TZ).replace(hour=23, minute=59).astimezone(dt.timezone.utc)
    for sport_id, tm_name in ((10, "Labdarúgás"), (12, "Tenisz")):
        for lang in ("en",):
            try:
                fx = c._get("/v4/fixtures", sportId=sport_id, **{"from": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
                            statusId=0, hasOdds="true", bookmakers="pinnacle", language=lang)
            except Exception as e:
                print("OddsPapi hiba:", e)
                continue
            tms = [m for m in tm if m["sportName"] == tm_name and m["startTime"] / 1000 > now.timestamp()]
            print(f"\n== {tm_name}: Tippmix {len(tms)} jövőbeli mai meccs, Pinnacle (OddsPapi, {lang}) {len(fx)}")
            matched, examples = 0, []
            for m in tms:
                cands = [f for f in fx if abs(dt.datetime.fromisoformat(f["startTime"].replace("Z", "+00:00")).timestamp() - m["startTime"] / 1000) <= 600]
                best = max(cands, key=lambda f: sim(m["homeParticipantName"], f["participant1Name"]) + sim(m["awayParticipantName"], f["participant2Name"]), default=None)
                score = (sim(m["homeParticipantName"], best["participant1Name"]) + sim(m["awayParticipantName"], best["participant2Name"])) / 2 if best else 0
                ok = score >= 0.6
                matched += ok
                if len(examples) < 70:
                    examples.append(f"{'✅' if ok else '❌'} {score:.2f} | {m['name']}  <->  {best['participant1Name'] + ' - ' + best['participant2Name'] if best else '-'} (jelöltek: {len(cands)})")
            print(f"Párosítva: {matched}/{len(tms)}")
            print("\n".join(examples))
    print("Fizetős kérések:", c.calls)


if __name__ == "__main__":
    main()
