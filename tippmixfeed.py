"""Tippmixpro oddsok közvetlenül a tippmixpro.hu nyilvános sport-adatfolyamából (WAMP websocket, csak olvasás,
bejelentkezés nélkül – ugyanaz, amit a weboldal is betölt).

  today_matches()   -> a mai, még el nem kezdődött meccsek (foci, tenisz, …)
  match_markets()   -> egy meccs fő piacai normalizálva: {(fajta, vonal): {kimenetel: odds}}
  to_op_book()      -> ugyanez OddsPapi-szerkezetben, hogy a value-logika változatlanul működjön

A Tippmix a piacokat "bettingTypeId" + "eventPartId" párral azonosítja (a recon/probe*.py felderítés alapján):
  foci   1X2 rendes játékidő: 69 / 3, kimenetelek #P<hazai>, #D, #P<vendég>
         gólszám:              47 / 3, paramFloat1 = vonal, kimenetelek #O / #U
  tenisz győztes:              466 / 20, kimenetelek #P<1. játékos>, #P<2. játékos>
         játékszám:            47 / 20
"""
import asyncio
import datetime as dt
import json
from zoneinfo import ZoneInfo

URL, OP, LANG = "wss://sportsapi.tippmixpro.hu/v2", "2901", "hu"
ORIGIN = "https://www.tippmixpro.hu"
SPORT_HU = {"Soccer": "Labdarúgás", "Tennis": "Tenisz", "Basketball": "Kosárlabda", "Ice Hockey": "Jégkorong"}
MARKETS = {  # (sportName, bettingTypeId, eventPartId) -> fajta
    ("Labdarúgás", "69", "3"): "main",
    ("Labdarúgás", "47", "3"): "ou",
    ("Tenisz", "466", "20"): "main",
    ("Tenisz", "47", "20"): "ou",
}


class Session:
    """Egy websocket-kapcsolat a futás idejére (szinkron felülettel)."""

    def __init__(self, timeout=25):
        self.timeout, self.ws, self.rid = timeout, None, 100
        self.loop = asyncio.new_event_loop()
        self.calls = 0

    def __enter__(self):
        self.loop.run_until_complete(self._open())
        return self

    def __exit__(self, *a):
        try:
            self.loop.run_until_complete(self.ws.close())
        except Exception:
            pass
        self.loop.close()

    async def _open(self):
        import websockets
        self.ws = await websockets.connect(URL, subprotocols=["wamp.2.json"], origin=ORIGIN, max_size=2 ** 26, open_timeout=30)
        await self.ws.send(json.dumps([1, "www.tippmixpro.hu", {"roles": {"caller": {"features": {}}}}]))
        welcome = json.loads(await asyncio.wait_for(self.ws.recv(), self.timeout))
        if welcome[0] != 2:
            raise RuntimeError(f"Tippmix WAMP: nem jött WELCOME ({str(welcome)[:120]})")

    async def _dump(self, topic):
        self.rid += 1
        self.calls += 1
        rid = self.rid
        await self.ws.send(json.dumps([48, rid, {}, "/sports#initialDump", [], {"topic": topic}]))
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), self.timeout))
            if m[0] in (50, 8) and m[1] == rid:
                if m[0] == 8:
                    raise RuntimeError(f"Tippmix hiba: {str(m)[:200]}")
                return (m[4] or {}).get("records") or []

    def dump(self, topic):
        try:
            return self.loop.run_until_complete(self._dump(topic))
        except asyncio.TimeoutError:
            return None

    # --- lekérdezések ---
    def today_matches(self, now, tz="Europe/Budapest", sports=("Labdarúgás", "Tenisz")):
        local = now.astimezone(ZoneInfo(tz))
        t0 = int(now.timestamp())
        t1 = int(local.replace(hour=23, minute=59, second=59, microsecond=0).timestamp())
        seen = {}
        for a in range(t0, t1 + 1, 7200):    # 2 órás ablakok (egy válasz legfeljebb 300 meccs)
            recs = self.dump(f"/sports/{OP}/{LANG}/live-calendar-matches-by-timestamp/all-disciplines/{a}-{min(a + 7199, t1)}/500")
            for r in recs or []:
                if r.get("_type") == "MATCH" and r.get("sportName") in sports and r.get("statusName") in (None, "Pending"):
                    seen[r["id"]] = {"id": r["id"], "sport": r["sportName"], "name": r.get("name"),
                                     "home": r.get("homeParticipantName"), "away": r.get("awayParticipantName"),
                                     "home_pid": r.get("homeParticipantId"), "away_pid": r.get("awayParticipantId"),
                                     "tournament": r.get("shortParentName") or r.get("parentName"),
                                     "start": dt.datetime.fromtimestamp(r["startTime"] / 1000, dt.timezone.utc)}
        return list(seen.values())

    def match_markets(self, match):
        recs = self.dump(f"/sports/{OP}/{LANG}/{match['id']}/match-odds/market-group/Main_BannerMatrix")
        return parse_markets(recs or [], match)


def parse_markets(recs, match):
    """{(fajta, vonal): {"home"/"draw"/"away"/"over"/"under": odds}} – csak elérhető, nyitott ajánlatok."""
    by_type = {}
    for r in recs:
        by_type.setdefault(r.get("_type"), []).append(r)
    outcomes = {o["id"]: o for o in by_type.get("OUTCOME", [])}
    offers = {b["outcomeId"]: b for b in by_type.get("BETTING_OFFER", [])
              if b.get("isAvailable", True) and str(b.get("statusId", "1")) == "1" and not b.get("isLive")}
    rel = {}
    for x in by_type.get("MARKET_OUTCOME_RELATION", []):
        rel.setdefault(x["marketId"], []).append(x["outcomeId"])
    out = {}
    for m in by_type.get("MARKET", []):
        kind = MARKETS.get((match["sport"], str(m.get("bettingTypeId")), str(m.get("eventPartId"))))
        if not kind or m.get("isClosed") or m.get("isAvailable") is False:
            continue
        line = float(m["paramFloat1"]) if kind == "ou" and m.get("paramFloat1") is not None else 0.0
        prices = {}
        for oid in rel.get(m["id"], []):
            o, b = outcomes.get(oid), offers.get(oid)
            if not o or not b or not b.get("odds"):
                continue
            code = str(o.get("code") or "")
            if kind == "ou":
                key = "over" if code.startswith("#O") else "under" if code.startswith("#U") else None
            elif code == "#D":
                key = "draw"
            elif code == f"#P{match.get('home_pid')}":
                key = "home"
            elif code == f"#P{match.get('away_pid')}":
                key = "away"
            else:
                key = None
            if key:
                prices[key] = float(b["odds"])
        if prices:
            out[(kind, line)] = prices
    return out


# --- OddsPapi-szerkezet -------------------------------------------------------

OP_SPORT = {"Labdarúgás": 10, "Tenisz": 12}
NAME_KEY = {"1": "home", "X": "draw", "2": "away", "Over": "over", "Under": "under"}


def op_index(markets_meta):
    """(tippmix-sportnév, fajta, vonal) -> (OddsPapi marketId, {kimenetel: outcomeId}) a /v4/markets listából."""
    idx = {}
    for m in markets_meta.values():
        sid, mt, per = m.get("sportId"), m.get("marketType"), m.get("period")
        sport = {10: "Labdarúgás", 12: "Tenisz"}.get(sid)
        if not sport or m.get("playerProp"):
            continue
        kind = None
        if sid == 10 and m["marketId"] == 101:
            kind, line = "main", 0.0
        elif sid == 12 and mt == "moneyline" and per == "result":
            kind, line = "main", 0.0
        elif sid == 10 and mt == "totals" and per == "fulltime" and str(m.get("marketName", "")).startswith("Over Under Full Time"):
            kind, line = "ou", float(m.get("handicap") or 0)
        elif sid == 12 and mt == "totals-games" and per == "result":
            kind, line = "ou", float(m.get("handicap") or 0)
        if kind:
            outs = {NAME_KEY.get(o["outcomeName"]): o["outcomeId"] for o in m["outcomes"] if NAME_KEY.get(o["outcomeName"])}
            idx[(sport, kind, line)] = (int(m["marketId"]), outs)
    return idx


def to_op_book(tm_markets, sport, index):
    """A Tippmix-piacok OddsPapi-szerkezetben (markets → outcomes → players['0'].price), hogy a meglévő logika használja."""
    markets = {}
    for (kind, line), prices in tm_markets.items():
        hit = index.get((sport, kind, line))
        if not hit:
            continue
        mid, outs = hit
        markets[str(mid)] = {"marketActive": True, "outcomes": {
            str(outs[k]): {"players": {"0": {"price": p, "active": True}}} for k, p in prices.items() if k in outs}}
    return {"bookmakerIsActive": True, "suspended": False, "markets": markets}
