"""Klasszikus Tippmix (tippmix.hu) nyilvános API felderítése – csak olvasás."""
import json, os, re, requests
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"); os.makedirs(OUT, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36", "Origin": "https://www.tippmix.hu",
     "Referer": "https://www.tippmix.hu/", "Content-Type": "application/json", "Accept": "application/json"}
A = "https://api.tippmix.hu"
def save(n, t): open(os.path.join(OUT, n), "w", encoding="utf-8").write(t)
body = {"search": None, "sportId": None, "competitionGroupId": None, "competitionOrAliasId": None, "eventTypes": [], "marketTypes": [],
        "minDate": None, "maxDate": None, "pageSize": 100, "page": 1, "minOdds": None, "maxOdds": None}
r = requests.post(f"{A}/v2/tippmix/events?compatibility=v1", json=body, headers=H, timeout=30)
print("events", r.status_code, len(r.text)); save("cl_events.json", r.text)
ev = r.json()["events"]; pre = [e for e in ev if not e.get("isLive")] or ev
eid = pre[0]["eventId"]; print("eventId", eid, pre[0]["eventName"], pre[0].get("totalMarketCount"))
for path in [f"/v2/tippmix/events/{eid}?compatibility=v1", f"/v2/tippmix/events/{eid}", f"/v2/tippmix/event/{eid}?compatibility=v1",
             f"/tippmix/event/{eid}", f"/v2/tippmix/events/{eid}/markets?compatibility=v1", f"/tippmix/event-details/{eid}"]:
    for m in ("GET", "POST"):
        try:
            x = requests.request(m, A + path, json={} if m == "POST" else None, headers=H, timeout=20)
            print(m, path, x.status_code, len(x.text), x.text[:150].replace("\n", " "))
            if x.status_code == 200 and len(x.text) > 1000:
                save(f"cl_detail_{m}_{abs(hash(path))%1000}.json", path + "\n\n" + x.text)
        except Exception as e:
            print(m, path, "ERR", e)
# JS végpontok
h = requests.get("https://www.tippmix.hu/", headers=H, timeout=30).text
js = set(re.findall(r'src="([^"]+\.js[^"]*)"', h)); found = set()
for s in js:
    u = s if s.startswith("http") else "https://www.tippmix.hu" + s
    try:
        t = requests.get(u, headers=H, timeout=30).text
        found |= set(re.findall(r'["\'`](/(?:v2/)?tippmix/[a-zA-Z0-9_\-/${}.]+)', t))
    except Exception:
        pass
print("JS végpontok:", sorted(found)[:200]); save("cl_endpoints.txt", "\n".join(sorted(found)))
