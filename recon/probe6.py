"""Próba 6: kosár-piacok, OddsPapi kosár/jégkorong piacok, teljes mai kínálat a matches/{bajnokság} lekérdezéssel."""
import collections
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed  # noqa: E402
from common import load_config  # noqa: E402
from oddsapi import OddsPapi  # noqa: E402

now = dt.datetime.now(dt.timezone.utc)
end = now.astimezone(tippmixfeed.ZoneInfo("Europe/Budapest")).replace(hour=23, minute=59).astimezone(dt.timezone.utc)
SPORTS = {"Labdarúgás": "1", "Tenisz": "3", "Kosárlabda": "8", "Jégkorong": "6"}
with tippmixfeed.Session() as s:
    cal = s.today_matches(now, sports=tuple(SPORTS))
    cal_ids = {m["id"] for m in cal}
    sids = {}
    for m in cal:
        pass
    # sportId-k a naptárból
    recs = s.dump(f"/sports/2901/hu/live-calendar-matches-by-timestamp/all-disciplines/{int(now.timestamp())}-{int(end.timestamp())}/500") or []
    for r in recs:
        if r.get("_type") == "MATCH":
            sids[r["sportName"]] = r["sportId"]
    print("Tippmix sportId-k:", sids)
    for sport, sid in sids.items():
        if sport not in SPORTS:
            continue
        t0 = time.time()
        tours = [r for r in (s.dump(f"/sports/2901/hu/tournaments/{sid}") or []) if r.get("_type") == "TOURNAMENT"]
        today, extra = 0, []
        for tr in tours:
            if not tr.get("numberOfUpcomingMatches"):
                continue
            for m in s.dump(f"/sports/2901/hu/matches/{tr['id']}") or []:
                if m.get("_type") == "MATCH" and now.timestamp() < m["startTime"] / 1000 <= end.timestamp():
                    today += 1
                    if m["id"] not in cal_ids:
                        extra.append(m)
        print(f"{sport}: {len(tours)} bajnokság, mai hátralévő meccs: {today}, naptárban nincs: {len(extra)} ({time.time() - t0:.0f} mp)")
        if extra:
            m = extra[0]
            mm = {"id": m["id"], "sport": sport, "home_pid": m.get("homeParticipantId"), "away_pid": m.get("awayParticipantId")}
            print("   pl.", m.get("name"), "| piacok:", s.match_markets(mm))
    for m in [x for x in cal if x["sport"] == "Kosárlabda"][:6]:
        recs = s.dump(f"/sports/2901/hu/{m['id']}/match-odds/market-group/Main_BannerMatrix") or []
        mk = [(r.get("bettingTypeId"), r.get("eventPartId"), r.get("name")) for r in recs if r.get("_type") == "MARKET"]
        if mk:
            print("\n== Kosár:", m["name"]); [print("  ", x) for x in mk[:15]]
            outs = {r["id"]: r.get("code") for r in recs if r.get("_type") == "OUTCOME"}
            print("   kódok:", sorted(set(outs.values()))[:10])
            break
    print("WAMP-hívások:", s.calls)

c = OddsPapi(load_config())
mk = c.markets()
for sid in (11, 15):
    rows = [m for m in mk if m.get("sportId") == sid and not m.get("playerProp")]
    print(f"\n== OddsPapi piacok sport {sid} ({len(rows)} db), első 14 + totals minta:")
    for m in rows[:14] + [r for r in rows if "total" in str(r.get("marketType"))][:3]:
        print("  ", m["marketId"], m.get("marketType"), m.get("handicap"), m.get("period"), m.get("marketName"), [(o["outcomeId"], o["outcomeName"]) for o in m["outcomes"]])
print("Fizetős kérések:", c.calls)
