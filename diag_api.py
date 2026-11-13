"""Diagnosztika: lehet-e EGY fizetős kéréssel az összes sportág meccslistáját lekérni (sportId nélkül), és mennyibe kerül."""
import collections
import datetime as dt

from common import load_config
from oddsapi import OddsPapi

cfg = load_config()
c = OddsPapi(cfg)
a0 = c.account(); print("előtte:", a0.get("request_count"), "/", a0.get("request_limit"))
now = dt.datetime.now(dt.timezone.utc)
end = (now + dt.timedelta(hours=30)).replace(minute=0, second=0, microsecond=0)
try:
    r = c._get("/v4/fixtures", **{"from": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
               statusId=0, hasOdds="true", bookmakers="pinnacle,sbobet,marathonbet")
    print("sportId nélkül:", len(r), "meccs")
    print(collections.Counter((f.get("sportId"), f.get("sportName")) for f in r).most_common(40))
except Exception as e:
    print("sportId nélkül hiba:", str(e)[:400])
a1 = c.account(); print("utána:", a1.get("request_count"), "/", a1.get("request_limit"))
