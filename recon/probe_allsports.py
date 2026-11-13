"""Felderítés: minden sportág – Tippmixpro és klasszikus Tippmix piacai (győztes / 1X2 / gólszám / hendikep)."""
import collections, datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import tippmixfeed as tf
import classicfeed as cf
now = dt.datetime.now(dt.timezone.utc)
print("===== TIPPMIXPRO")
with tf.Session() as s:
    t0 = int(now.timestamp()); t1 = t0 + 30 * 3600; recs = []
    for a in range(t0, t1, 7200):
        recs += s.dump(f"/sports/{tf.OP}/{tf.LANG}/live-calendar-matches-by-timestamp/all-disciplines/{a}-{a + 7199}/500") or []
    ms = [r for r in recs if r.get("_type") == "MATCH" and r.get("statusName") in (None, "Pending")]
    c = collections.Counter((r.get("sportName"), r.get("sportId")) for r in ms)
    print(c.most_common())
    done = set()
    for r in ms:
        sp = r.get("sportName")
        if sp in done or sp in ("Labdarúgás",):
            continue
        done.add(sp)
        mr = s.dump(f"/sports/{tf.OP}/{tf.LANG}/{r['id']}/match-odds/market-group/Main_BannerMatrix") or []
        by = collections.defaultdict(list)
        for x in mr: by[x.get("_type")].append(x)
        outs = {o["id"]: o for o in by["OUTCOME"]}
        rel = collections.defaultdict(list)
        for x in by["MARKET_OUTCOME_RELATION"]: rel[x["marketId"]].append(x["outcomeId"])
        print(f"\n--- {sp} (id {r.get('sportId')}): {r.get('name')} home_pid={r.get('homeParticipantId')}")
        for m in by["MARKET"][:25]:
            print(f"   bt={m.get('bettingTypeId')} part={m.get('eventPartId')} p1={m.get('paramFloat1')} {m.get('name')!r} ->",
                  [str(outs.get(o, {}).get('code')) for o in rel[m['id']]][:4])
print("\n===== KLASSZIKUS")
ev, page = [], 1
while True:
    b = {"search": None, "sportId": None, "competitionGroupId": None, "competitionOrAliasId": None, "eventTypes": [], "marketTypes": [],
         "minDate": None, "maxDate": None, "pageSize": 100, "page": page, "minOdds": None, "maxOdds": None}
    r = cf._post("/v2/tippmix/events?compatibility=v1", b); ev += r["events"]
    if page >= r["_meta"]["pageCount"]: break
    page += 1
ev = [e for e in ev if not e["isLive"] and not e.get("isOutright")]
print(collections.Counter(e["sportName"] for e in ev).most_common())
done = set()
for e in ev:
    sp = e["sportName"]
    if sp in done or sp in ("Labdarúgás", "Tenisz", "Kosárlabda", "Jégkorong") or len(e.get("eventParticipants") or []) != 2:
        continue
    done.add(sp)
    d = cf._get(f"/v2/tippmix/event/{e['eventId']}?compatibility=v1")["event"]
    print(f"\n--- {sp}: {d['eventName']}")
    seen = set()
    for g in d["marketGroups"]:
        for m in g["markets"]:
            k = (m["marketName"], m["marketSubType"])
            if k in seen: continue
            seen.add(k)
            print(f"   sub={m['marketSubType']} spec={m['specialOddsValue']!r} min={m['minCombiCount']} {m['marketName']!r} ->",
                  [(o['outcomeName'], o['outcomeRealNo']) for o in m['outcomes']][:3])
