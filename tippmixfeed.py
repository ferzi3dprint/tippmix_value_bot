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
import re
import datetime as dt
import json
from zoneinfo import ZoneInfo

URL, OP, LANG = "wss://sportsapi.tippmixpro.hu/v2", "2901", "hu"
ORIGIN = "https://www.tippmixpro.hu"
SPORT_HU = {"Soccer": "Labdarúgás", "Tennis": "Tenisz", "Basketball": "Kosárlabda", "Ice Hockey": "Jégkorong"}
TM_SPORT_ID = {"Labdarúgás": "1", "Tenisz": "3", "Jégkorong": "6", "Kosárlabda": "8"}
MARKETS = {  # (sportName, bettingTypeId, eventPartId) -> fajta  (foci és tenisz: a felderítés alapján)
    ("Labdarúgás", "69", "3"): "main",
    ("Labdarúgás", "47", "3"): "ou",
    ("Labdarúgás", "76", "3"): "btts",
    ("Labdarúgás", "48", "3"): "ah",
    ("Tenisz", "466", "20"): "main",
    ("Tenisz", "47", "20"): "ou",
}
# jégkorong és kosár: a piac neve alapján („… - Rendes játékidő” / „… - Teljes esemény”)
NAMED = {
    ("Jégkorong", "69", "Rendes játékidő"): "1x2reg",
    ("Jégkorong", "466", "Teljes esemény"): "main",
    ("Jégkorong", "47", "Rendes játékidő"): "ou",
    ("Jégkorong", "48", "Rendes játékidő"): "ah",
    ("Kosárlabda", "466", "Teljes esemény"): "main",
    ("Kosárlabda", "69", "Rendes játékidő"): "1x2reg",
    ("Kosárlabda", "47", "Teljes esemény"): "ou",
    ("Kosárlabda", "48", "Teljes esemény"): "ah",
}


def market_kind(sport, m):
    k = MARKETS.get((sport, str(m.get("bettingTypeId")), str(m.get("eventPartId"))))
    if k:
        return k
    part = str(m.get("name") or "").rsplit(" - ", 1)[-1].strip()
    return NAMED.get((sport, str(m.get("bettingTypeId")), part))


def slug(text):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "x"


def match_url(m):
    """A Tippmixpro meccsoldala, pl. /hu/fogadas/i/esemenyek/1/labdarugas/vilag/valogatott-merkozes/malta-eswatini/<id>/nepszeru"""
    if not m.get("tm_sport_id") or not m.get("id"):
        return None
    return ("https://www.tippmixpro.hu/hu/fogadas/i/esemenyek/{}/{}/{}/{}/{}/{}/nepszeru"
            .format(m["tm_sport_id"], slug(m.get("sport")), slug(m.get("category")), slug(m.get("tournament")),
                    slug(m.get("name") or f"{m.get('home')} - {m.get('away')}"), m["id"]))


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
    def today_matches(self, now, tz="Europe/Budapest", sports=("Labdarúgás", "Tenisz"), days_ahead=0):
        local = now.astimezone(ZoneInfo(tz))
        t0 = int(now.timestamp())
        t1 = int((local.replace(hour=23, minute=59, second=59, microsecond=0) + dt.timedelta(days=days_ahead)).timestamp())
        seen = {}
        for a in range(t0, t1 + 1, 7200):    # 2 órás ablakok (egy válasz legfeljebb 300 meccs)
            recs = self.dump(f"/sports/{OP}/{LANG}/live-calendar-matches-by-timestamp/all-disciplines/{a}-{min(a + 7199, t1)}/500")
            for r in recs or []:
                if r.get("_type") == "MATCH" and r.get("sportName") in sports and r.get("statusName") in (None, "Pending"):
                    seen[r["id"]] = {"id": r["id"], "sport": r["sportName"], "name": r.get("name"),
                                     "home": r.get("homeParticipantName"), "away": r.get("awayParticipantName"),
                                     "home_pid": r.get("homeParticipantId"), "away_pid": r.get("awayParticipantId"),
                                     "tournament": r.get("shortParentName") or r.get("parentName"),
                                     "category": r.get("categoryName") or r.get("venueName"), "tm_sport_id": r.get("sportId"),
                                     "start": dt.datetime.fromtimestamp(r["startTime"] / 1000, dt.timezone.utc)}
        return list(seen.values())

    def all_today_matches(self, now, tz="Europe/Budapest", sports=("Labdarúgás", "Tenisz"), days_ahead=0):
        """A teljes kínálat bajnokságonként (a naptár csak az élőben is fogadható meccseket adja)."""
        end = now.astimezone(ZoneInfo(tz)).replace(hour=23, minute=59, second=59, microsecond=0) + dt.timedelta(days=days_ahead)
        out = {m["id"]: m for m in self.today_matches(now, tz, sports, days_ahead)}
        for sport in sports:
            sid = TM_SPORT_ID.get(sport)
            if not sid:
                continue
            for tr in self.dump(f"/sports/{OP}/{LANG}/tournaments/{sid}") or []:
                if tr.get("_type") != "TOURNAMENT" or not tr.get("numberOfUpcomingMatches"):
                    continue
                for r in self.dump(f"/sports/{OP}/{LANG}/matches/{tr['id']}") or []:
                    if r.get("_type") != "MATCH" or r["id"] in out:
                        continue
                    if not (now.timestamp() < r["startTime"] / 1000 <= end.timestamp()):
                        continue
                    out[r["id"]] = {"id": r["id"], "sport": sport, "name": r.get("name"),
                                    "home": r.get("homeParticipantName"), "away": r.get("awayParticipantName"),
                                    "home_pid": r.get("homeParticipantId"), "away_pid": r.get("awayParticipantId"),
                                    "tournament": r.get("shortParentName") or tr.get("name"),
                                    "category": r.get("categoryName") or r.get("venueName"), "tm_sport_id": r.get("sportId") or sid,
                                    "start": dt.datetime.fromtimestamp(r["startTime"] / 1000, dt.timezone.utc)}
        return list(out.values())

    def boosts(self):
        """Oddsrakéta (Tippmixpro odds-emelés, 101-es kategória): [{key, title, tip, odds, start}] – bármelyik ismert helyről."""
        out, raw = [], []
        for t in self.dump(f"/sports/{OP}/{LANG}/tournaments/101") or []:
            if t.get("_type") != "TOURNAMENT":
                continue
            recs = []
            for m in self.dump(f"/sports/{OP}/{LANG}/matches/{t['id']}") or []:
                if m.get("_type") == "MATCH":
                    recs += self.dump(f"/sports/{OP}/{LANG}/{m['id']}/match-odds/") or []
            recs += self.dump(f"/sports/{OP}/{LANG}/{t['id']}/tournament-odds/7/1") or []
            if recs:
                raw.append({"tournament": t, "records": recs[:200]})
            outs = {r["id"]: r for r in recs if r.get("_type") == "OUTCOME"}
            mk_of = {x["outcomeId"]: x["marketId"] for x in recs if x.get("_type") == "MARKET_OUTCOME_RELATION"}
            mks = {r["id"]: r for r in recs if r.get("_type") == "MARKET"}
            for b in recs:
                if b.get("_type") != "BETTING_OFFER" or not b.get("odds") or b.get("isAvailable") is False:
                    continue
                o = outs.get(b["outcomeId"], {})
                mk = mks.get(mk_of.get(b["outcomeId"]), {})
                out.append({"key": f"{t['id']}:{b['outcomeId']}", "title": t.get("name"), "market": mk.get("name"),
                            "tip": str(o.get("translatedName") or o.get("name") or "").strip(), "odds": float(b["odds"]),
                            "start": t.get("startTime")})
        return out, raw

    def match_markets(self, match, names=None):
        recs = self.dump(f"/sports/{OP}/{LANG}/{match['id']}/match-odds/market-group/Main_BannerMatrix")
        return parse_markets(recs or [], match, names)


def parse_markets(recs, match, names=None):
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
        kind = market_kind(match["sport"], m)
        if not kind or m.get("isClosed") or m.get("isAvailable") is False:
            continue
        line = float(m["paramFloat1"]) if kind == "ou" and m.get("paramFloat1") is not None else 0.0
        prices, tip_names = {}, {}
        for oid in rel.get(m["id"], []):
            o, b = outcomes.get(oid), offers.get(oid)
            if not o or not b or not b.get("odds"):
                continue
            code = str(o.get("code") or "")
            if kind == "ou":
                key = "over" if code.startswith("#O") else "under" if code.startswith("#U") else None
            elif kind == "btts":
                key = "yes" if code == "#Y" else "no" if code == "#N" else None
            elif kind == "ah":
                # „#P<id> (-1.5)”: a zárójelben az adott csapat hendikepe; a vonal = a hazai csapat hendikepe
                mm = re.match(r"#P(\d+) \(([+-]?[\d.]+)\)$", code)
                key = None
                if mm and mm.group(1) == str(match.get("home_pid")):
                    key, line = "home", float(mm.group(2))
                elif mm and mm.group(1) == str(match.get("away_pid")):
                    key = "away"
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
                if names is not None:
                    tip_names[key] = f"{m.get('name')} → {str(o.get('translatedName') or o.get('name') or '').strip()}"
        if kind == "ah" and not ("home" in prices and "away" in prices):
            continue        # hendikepnél csak teljes pár (a vonalat a hazai kimenetel adja)
        if prices:
            out[(kind, line)] = prices
            if names is not None:
                names[(kind, line)] = tip_names
    return out


# --- OddsPapi-szerkezet -------------------------------------------------------

OP_SPORT = {"Labdarúgás": 10, "Kosárlabda": 11, "Tenisz": 12, "Jégkorong": 15}
NAME_KEY = {"1": "home", "X": "draw", "2": "away", "Over": "over", "Under": "under", "Yes": "yes", "No": "no"}


def op_index(markets_meta):
    """(tippmix-sportnév, fajta, vonal) -> (OddsPapi marketId, {kimenetel: outcomeId}) a /v4/markets listából."""
    idx = {}
    by_id = {v: k for k, v in OP_SPORT.items()}
    for m in markets_meta.values():
        sid, mt, per, name = m.get("sportId"), m.get("marketType"), m.get("period"), str(m.get("marketName", ""))
        sport = by_id.get(sid)
        if not sport or m.get("playerProp"):
            continue
        line = float(m.get("handicap") or 0)
        kind = None
        if mt == "spreads" and ((sid == 10 and per == "fulltime" and name == "Asian Handicap") or
                                (sid == 15 and per == "fulltime" and name == "Handicap") or
                                (sid == 11 and per == "result" and name == "Handicap (incl. overtime)")):
            kind = "ah"
        elif sid == 10:
            if m["marketId"] == 101:
                kind = "main"
            elif m["marketId"] == 104:
                kind = "btts"
            elif mt == "totals" and per == "fulltime" and name.startswith("Over Under Full Time"):
                kind = "ou"
        elif sid == 12:
            if mt == "moneyline" and per == "result":
                kind = "main"
            elif mt == "totals-games" and per == "result":
                kind = "ou"
        elif sid == 11:
            if mt == "moneyline" and per == "result":
                kind = "main"
            elif mt == "1x2" and per == "fulltime":
                kind = "1x2reg"
            elif mt == "totals" and per == "result" and "incl. overtime" in name:
                kind = "ou"
        elif sid == 15:
            if mt == "moneyline" and per == "result":
                kind = "main"
            elif mt == "1x2" and per == "fulltime":
                kind = "1x2reg"
            elif mt == "totals" and per == "fulltime" and name == "Total":
                kind = "ou"
        if kind:
            if kind in ("main", "1x2reg") and line:
                continue
            outs = {NAME_KEY.get(o["outcomeName"]): o["outcomeId"] for o in m["outcomes"] if NAME_KEY.get(o["outcomeName"])}
            idx.setdefault((sport, kind, line), (int(m["marketId"]), outs))
    return idx


def to_op_book(tm_markets, sport, index, names=None):
    """A Tippmix-piacok OddsPapi-szerkezetben (markets → outcomes → players['0'].price), hogy a meglévő logika használja."""
    markets = {}
    for (kind, line), prices in tm_markets.items():
        hit = index.get((sport, kind, line))
        if not hit:
            continue
        mid, outs = hit
        markets[str(mid)] = {"marketActive": True, "outcomes": {
            str(outs[k]): {"players": {"0": {"price": p, "active": True}},
                           "tm": ((names or {}).get((kind, line)) or {}).get(k)} for k, p in prices.items() if k in outs}}
    return {"bookmakerIsActive": True, "suspended": False, "markets": markets}
