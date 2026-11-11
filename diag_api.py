"""Diagnosztika: a fiók előfizetése, mely irodák érhetők el, és melyik végpont mit enged (kevés fizetős kéréssel)."""
import datetime as dt
import json

from common import load_config, now_utc
from oddsapi import OddsPapi


def short(x, n=900):
    return json.dumps(x, ensure_ascii=False)[:n]


def tryit(label, fn):
    try:
        r = fn()
        print(f"✅ {label}: {short(r, 600)}")
        return r
    except Exception as e:
        print(f"❌ {label}: {str(e)[:400]}")


cfg = load_config()
c = OddsPapi(cfg)
acc = c.account()
acc.pop("api_key", None)
acc.pop("email", None)
print("FIÓK:", short(acc, 3000))
bk = tryit("bookmakers", lambda: c._get("/v4/bookmakers"))
if isinstance(bk, list):
    print("Irodák száma:", len(bk))
    for b in bk:
        s = json.dumps(b, ensure_ascii=False).lower()
        if "tippmix" in s or "pinnacle" in s:
            print("   ", short(b, 400))
now = now_utc()
fx = tryit("fixtures pinnacle", lambda: c.fixtures(10, now + dt.timedelta(hours=1), now + dt.timedelta(hours=30), "pinnacle"))
if fx:
    fid = fx[0]["fixtureId"]
    tryit("historical tippmixpro+pinnacle", lambda: c.historical_odds(fid, "tippmixpro,pinnacle"))
    tryit("historical tippmixpro", lambda: c.historical_odds(fid, "tippmixpro"))
print("Fizetős kérések:", c.calls, "| keret:", short({k: v for k, v in c.account().items() if "request" in k}))
