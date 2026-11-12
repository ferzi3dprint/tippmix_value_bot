"""Próba 5: kosár/jégkorong piacok a Tippmixen, teljes meccskínálat bajnokságonként, OddsPapi sport- és piacazonosítók."""
import collections
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed  # noqa: E402
from common import load_config  # noqa: E402
from oddsapi import OddsPapi  # noqa: E402

now = dt.datetime.now(dt.timezone.utc)
with tippmixfeed.Session() as s:
    cal = s.today_matches(now, sports=("Labdarúgás", "Tenisz", "Kosárlabda", "Jégkorong"))
    print("Naptár (mai, élő-naptár) sportáganként:", dict(collections.Counter(m["sport"] for m in cal)))
    for sport in ("Kosárlabda", "Jégkorong"):
        ms = [m for m in cal if m["sport"] == sport][:1]
        for m in ms:
            recs = s.dump(f"/sports/2901/hu/{m['id']}/match-odds/market-group/Main_BannerMatrix") or []
            print(f"\n== {sport}: {m['name']}")
            outs = {r["id"]: r for r in recs if r.get("_type") == "OUTCOME"}
            rel = collections.defaultdict(list)
            for x in recs:
                if x.get("_type") == "MARKET_OUTCOME_RELATION":
                    rel[x["marketId"]].append(outs.get(x["outcomeId"], {}).get("code"))
            for r in recs:
                if r.get("_type") == "MARKET":
                    print("  ", r.get("bettingTypeId"), r.get("eventPartId"), r.get("name"), r.get("paramFloat1"), rel.get(r["id"]))
    # teljes kínálat: bajnokságok foci
    t = s.dump("/sports/2901/hu/tournaments/1") or []
    tours = [r for r in t if r.get("_type") == "TOURNAMENT"]
    print("\nFoci bajnokságok:", len(tours), "| minta mezők:", list(tours[0].keys())[:40] if tours else "-")
    up = sorted(tours, key=lambda r: -(r.get("numberOfUpcomingMatches") or 0))[:3]
    for tr in up:
        print("  ", tr.get("id"), tr.get("name"), tr.get("numberOfUpcomingMatches"))
    for tr in up[:2]:
        for topic in (f"/sports/2901/hu/{tr['id']}/tournament-odds/7/1", f"/sports/2901/hu/tournament-aggregator-main/{tr['id']}/default-event-info",
                      f"/sports/2901/hu/matches/{tr['id']}"):
            recs = s.dump(topic)
            cnt = collections.Counter(r.get("_type") for r in (recs or []))
            mt = [r for r in (recs or []) if r.get("_type") == "MATCH"]
            today = [m for m in mt if dt.datetime.fromtimestamp(m["startTime"] / 1000, dt.timezone.utc).date() == now.date()]
            print(f"   {topic}: {None if recs is None else dict(cnt)} | MATCH ma: {len(today)}")
    # a mai összes foci-meccs becslése: bajnokságonként a tournament-odds-ból
    total_today, cal_ids = 0, {m["id"] for m in cal}
    extra = 0
    for tr in tours[:60]:
        if not tr.get("numberOfUpcomingMatches"):
            continue
        recs = s.dump(f"/sports/2901/hu/{tr['id']}/tournament-odds/7/1") or []
        for m in recs:
            if m.get("_type") == "MATCH" and dt.datetime.fromtimestamp(m["startTime"] / 1000, dt.timezone.utc).date() == now.date() \
                    and m["startTime"] / 1000 > now.timestamp():
                total_today += 1
                extra += m["id"] not in cal_ids
    print(f"\nFoci, első 60 bajnokság alapján: ma még {total_today} meccs, ebből a naptárban NINCS: {extra}")
    print("WAMP-hívások:", s.calls)

c = OddsPapi(load_config())
sp = c.sports()
print("\nOddsPapi sportok:", [(x["sportId"], x["sportName"]) for x in sp if x["sportName"] in ("Basketball", "Ice Hockey", "Soccer", "Tennis")])
mk = c.markets()
for sid in [x["sportId"] for x in sp if x["sportName"] in ("Basketball", "Ice Hockey")]:
    rows = [m for m in mk if m.get("sportId") == sid and not m.get("playerProp")]
    print(f"\n== OddsPapi piacok sport {sid} (első 25):")
    for m in rows[:25]:
        print("  ", m["marketId"], m.get("marketType"), m.get("handicap"), m.get("period"), m.get("marketName"), [(o["outcomeId"], o["outcomeName"]) for o in m["outcomes"]])
print("Fizetős kérések:", c.calls)
