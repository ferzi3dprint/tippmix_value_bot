import collections, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed
now = dt.datetime.now(dt.timezone.utc)
with tippmixfeed.Session() as s:
    cal = s.today_matches(now, sports=("Labdarúgás", "Kosárlabda", "Jégkorong"))
    for sport in ("Labdarúgás", "Kosárlabda", "Jégkorong"):
        shown = 0
        for m in [x for x in cal if x["sport"] == sport]:
            recs = s.dump(f"/sports/2901/hu/{m['id']}/match-odds/market-group/Main_BannerMatrix") or []
            mk = [r for r in recs if r.get("_type") == "MARKET"]
            if len(mk) < 5:
                continue
            outs = {r["id"]: r for r in recs if r.get("_type") == "OUTCOME"}
            rel = collections.defaultdict(list)
            for x in recs:
                if x.get("_type") == "MARKET_OUTCOME_RELATION":
                    o = outs.get(x["outcomeId"], {})
                    rel[x["marketId"]].append((o.get("code"), o.get("translatedName"), o.get("paramFloat1")))
            print(f"\n== {sport}: {m['name']} home_pid={m['home_pid']} away_pid={m['away_pid']}")
            seen = set()
            for r in mk:
                key = (r.get("bettingTypeId"), r.get("eventPartId"))
                if key in seen and r.get("bettingTypeId") in ("47", "48"):
                    continue
                seen.add(key)
                print("  ", r.get("bettingTypeId"), r.get("eventPartId"), r.get("name"), r.get("paramFloat1"), rel.get(r["id"]))
            shown += 1
            if shown >= 1:
                break
