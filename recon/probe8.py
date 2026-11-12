"""Próba 8: eredmények lejátszott meccsekről a Tippmixen + elérhető irodák listája az OddsPapi-fiókban."""
import collections, datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed
from common import load_config
from oddsapi import OddsPapi

now = dt.datetime.now(dt.timezone.utc)
c = load_json = None
with tippmixfeed.Session() as s:
    t0 = int((now - dt.timedelta(hours=14)).timestamp())
    t1 = int((now - dt.timedelta(hours=3)).timestamp())
    recs = s.dump(f"/sports/2901/hu/live-calendar-matches-by-timestamp/all-disciplines/{t0}-{t1}/500") or []
    ms = [r for r in recs if r.get("_type") == "MATCH" and r.get("sportName") in ("Labdarúgás", "Tenisz", "Kosárlabda", "Jégkorong")]
    print("Lejátszott (naptár, 3-14 órája):", len(ms), dict(collections.Counter(r.get("statusName") for r in ms)))
    if ms:
        print("MATCH mezők:", json.dumps(ms[0], ensure_ascii=False)[:1500])
    for m in ms[:2]:
        for topic in (f"/sports/2901/hu/{m['id']}/match-odds/", f"/sports/2901/hu/event-info/{m['id']}", f"/sports/2901/hu/{m['id']}/event-info",
                      f"/sports/2901/hu/match/{m['id']}", f"/sports/2901/hu/{m['id']}/statistics", f"/sports/2901/hu/event-results/{m['id']}",
                      f"/sports/2901/hu/{m['id']}/match-results"):
            r = s.dump(topic)
            cnt = dict(collections.Counter(x.get("_type") for x in (r or [])))
            print(f"  {topic}: {None if r is None else cnt}")
            for x in (r or []):
                if x.get("_type") in ("EVENT_INFO", "EVENT_STATISTICS", "SCORE", "EVENT_RESULT", "MATCH_RESULT") or "core" in str(x.get("_type")):
                    print("     ", json.dumps(x, ensure_ascii=False)[:500])
                    break

cl = OddsPapi(load_config())
acc = cl.account()
subs = (acc.get("subscriptions") or [{}])[0].get("bookmakers") or {}
cands = [b for b in subs if any(k in b for k in ("betfair", "sbo", "bet365", "matchbook", "smarkets", "betdaq", "1xbet", "marathon", "pinnacle", "bwin", "unibet", "williamhill"))]
print("\nJelölt irodák a fiókban:", sorted(cands))
