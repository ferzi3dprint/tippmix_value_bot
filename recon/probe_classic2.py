"""Klasszikus Tippmix: piacnevek sportonként (minta)."""
import json, os, requests, datetime as dt
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"); os.makedirs(OUT, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 Chrome/130.0", "Origin": "https://www.tippmix.hu", "Referer": "https://www.tippmix.hu/",
     "Content-Type": "application/json", "Accept": "application/json"}
A = "https://api.tippmix.hu"
evs = []
for page in range(1, 30):
    b = {"search": None, "sportId": None, "competitionGroupId": None, "competitionOrAliasId": None, "eventTypes": [], "marketTypes": [],
         "minDate": None, "maxDate": None, "pageSize": 100, "page": page, "minOdds": None, "maxOdds": None}
    r = requests.post(f"{A}/v2/tippmix/events?compatibility=v1", json=b, headers=H, timeout=30).json()
    evs += r["events"]
    if page >= r["_meta"]["pageCount"]:
        break
print("összes esemény", len(evs))
import collections
print(collections.Counter(e["sportName"] for e in evs if not e["isLive"]).most_common(15))
dates = sorted(e["eventDate"] for e in evs if not e["isLive"]); print("dátum", dates[0], dates[-1])
print("min combi>1 főpiac:", collections.Counter((e["sportName"], e["markets"][0]["minCombiCount"]) for e in evs if e["markets"] and not e["isLive"]).most_common(20))
json.dump(evs, open(os.path.join(OUT, "cl_all.json"), "w"), ensure_ascii=False)
done = {}
for e in evs:
    s = e["sportName"]
    if e["isLive"] or s not in ("Labdarúgás", "Tenisz", "Kosárlabda", "Jégkorong") or done.get(s, 0) >= 2 or e["totalMarketCount"] < 10:
        continue
    d = requests.get(f"{A}/v2/tippmix/event/{e['eventId']}?compatibility=v1", headers=H, timeout=30).json()["event"]
    done[s] = done.get(s, 0) + 1
    print("\n=====", s, d["eventName"], d["eventDate"], "betradar", d["betradarId"], "piacok", d["totalMarketCount"])
    for g in d["marketGroups"]:
        for m in g["markets"]:
            print(f"  [{g.get('name')}] {m['marketName']!r} sub={m['marketSubType']} type={m['marketType']} spec={m['specialOddsValue']!r} "
                  f"code={m['marketRealNo']} min={m['minCombiCount']} st={m['marketStatus']} ->",
                  [(o['outcomeName'], o['fixedOdds'], o['outcomeRealNo']) for o in m['outcomes']][:4])
    json.dump(d, open(os.path.join(OUT, f"cl_{s}_{done[s]}.json"), "w"), ensure_ascii=False)
