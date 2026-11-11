"""Diagnosztika: mely irodák érhetők el a fiókkal, van-e Tippmix (vagy klónja), és a Pinnacle benne van-e."""
import json

from common import load_config
from oddsapi import OddsPapi

cfg = load_config()
c = OddsPapi(cfg)
acc = c.account()
subs = acc.get("subscriptions") or []
mine = sorted((subs[0].get("bookmakers") or {}).keys()) if subs else []
print("Előfizetés:", {k: v for k, v in (subs[0] if subs else {}).items() if k != "bookmakers"})
print("Elérhető irodák száma:", len(mine))
print("Pinnacle-féle elérhető:", [b for b in mine if "pinn" in b or b in ("ps3838", "pin88", "bet487", "ole777", "asports.bet", "bolsadeaposta-spb", "piwi247-spb")])
print("Elérhető irodák:", ", ".join(mine))
other = {k: v for k, v in acc.items() if k not in ("subscriptions", "api_key", "email")}
print("Fiók egyéb mezői:", json.dumps(other, ensure_ascii=False)[:800])
for q in (None, "tippmixpro"):
    try:
        r = c._get("/v4/bookmakers", **({"bookmakers": q} if q else {}))
        hits = [b for b in r if any(s in json.dumps(b).lower() for s in ("tipp", "szerencse", "hungar", "everymatrix"))]
        print(f"/v4/bookmakers {q or ''}: {len(r)} db, Tippmix-találat: {hits}")
    except Exception as e:
        print("hiba:", str(e)[:300])
print("Fizetős kérések ebben a futásban:", c.calls)
