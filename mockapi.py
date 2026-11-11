"""Szintetikus OddsPapi-utánzat a demóhoz és a tesztekhez (MOCK_API=1).

Kitalált meccsek, kitalált "valódi" valószínűségekkel. A Pinnacle-ár a valódi esély zajos becslése, ami a kezdéshez
közeledve pontosodik; a Tippmix-ár nagyobb árréssel és nagyobb, lassan frissülő hibával áraz. Az eredményt a valódi
valószínűség szerint sorsolja. CSAK a működés bemutatására – a demó számai nem mondanak semmit a valódi piacról.
"""
import datetime as dt
import hashlib
import random

from common import UTC, now_utc

SPORTS = [{"sportId": 10, "sportName": "Soccer"}, {"sportId": 12, "sportName": "Tennis"}]
MARKETS = [
    {"marketId": 101, "marketName": "Full Time Result", "marketLength": 3, "playerProp": False, "sportId": 10,
     "handicap": 0, "period": "fulltime", "marketType": "1x2",
     "outcomes": [{"outcomeId": 101, "outcomeName": "1"}, {"outcomeId": 102, "outcomeName": "X"}, {"outcomeId": 103, "outcomeName": "2"}]},
    {"marketId": 1010, "marketName": "Over Under 2.5", "marketLength": 2, "playerProp": False, "sportId": 10,
     "handicap": 2.5, "period": "fulltime", "marketType": "totals",
     "outcomes": [{"outcomeId": 1010, "outcomeName": "Over"}, {"outcomeId": 1011, "outcomeName": "Under"}]},
    {"marketId": 121, "marketName": "Winner", "marketLength": 2, "playerProp": False, "sportId": 12,
     "handicap": 0, "period": "fulltime", "marketType": "12",
     "outcomes": [{"outcomeId": 121, "outcomeName": "1"}, {"outcomeId": 122, "outcomeName": "2"}]},
]
TOURNAMENTS = {  # tournamentId -> (sportId, név, meccs/nap)
    17: (10, "Premier League", 4), 8: (10, "LaLiga", 4), 35: (10, "Bundesliga", 3), 23: (10, "Serie A", 3),
    2000: (12, "ATP Challenger", 6),
}
TEAMS = ["Arsenal", "Chelsea", "Liverpool", "Everton", "Fulham", "Brighton", "Sevilla", "Betis", "Girona", "Villarreal",
         "Mainz", "Freiburg", "Bochum", "Augsburg", "Torino", "Bologna", "Lecce", "Monza", "Genoa", "Empoli"]
PLAYERS = ["Fucsovics M.", "Marozsán F.", "Piros Zs.", "Valkusz M.", "Nagy P.", "Rublev A.", "Ruud C.", "Draper J.",
           "Musetti L.", "Cerúndolo F.", "Baez S.", "Lehecka J."]


def _rng(*key):
    return random.Random(int(hashlib.sha256("|".join(map(str, key)).encode()).hexdigest()[:12], 16))


def _norm(ps):
    s = sum(ps)
    return [p / s for p in ps]


def _day_fixtures(day):
    out = []
    for tid, (sid, tname, n) in TOURNAMENTS.items():
        for i in range(n):
            r = _rng("fx", day, tid, i)
            start = dt.datetime(day.year, day.month, day.day, r.choice([11, 13, 15, 17, 18, 19]), r.choice([0, 30]), tzinfo=UTC)
            names = PLAYERS if sid == 12 else TEAMS
            a, b = r.sample(names, 2)
            if sid == 10:
                h = r.uniform(0.22, 0.68); d = r.uniform(0.20, 0.30)
                true = {101: _norm([h, d, max(0.06, 1 - h - d)]), 1010: _norm([r.uniform(0.4, 0.62), 0.5])}
            else:
                p = r.uniform(0.25, 0.80)
                true = {121: [p, 1 - p]}
            out.append({"fixtureId": f"mock-{day.isoformat()}-{tid}-{i}", "sportId": sid, "tournamentId": tid,
                        "tournamentName": tname, "participant1Name": a, "participant2Name": b,
                        "startTime": start, "_true": true})
    return out


def _fixtures_between(start, end):
    out, d = [], start.date() - dt.timedelta(days=1)
    while d <= end.date():
        out += [f for f in _day_fixtures(d) if start <= f["startTime"] <= end]
        d += dt.timedelta(days=1)
    return out


def _fixture(fid):
    _, y, m, d, tid, i = fid.split("-")
    return next(f for f in _day_fixtures(dt.date(int(y), int(m), int(d))) if f["fixtureId"] == fid)


def _prices(f, book, now):
    hours_left = max(0.0, (f["startTime"] - now).total_seconds() / 3600)
    markets = {}
    for mid, true in f["_true"].items():
        if book == "pinnacle":
            sd = 0.004 + 0.02 * min(hours_left, 36) / 36           # a kezdéshez közeledve pontosodik
            r = _rng("pin", f["fixtureId"], mid, int(hours_left))
            est = _norm([max(0.02, p + r.gauss(0, sd)) for p in true])
            odds = [round(1 / (p * 1.025), 3) for p in est]
        else:
            r = _rng("tm", f["fixtureId"], mid, int(hours_left // 8))  # ritkán frissül
            est = _norm([max(0.03, p + r.gauss(0, 0.035)) for p in true])
            odds = [max(1.01, round(1 / (p * 1.075), 2)) for p in est]
        outs = [o["outcomeId"] for o in next(m for m in MARKETS if m["marketId"] == mid)["outcomes"]]
        markets[str(mid)] = {"marketActive": True, "outcomes": {
            str(oid): {"players": {"0": {"active": True, "price": od, "changedAt": now.isoformat()}}}
            for oid, od in zip(outs, odds)}}
    return {"bookmakerIsActive": True, "suspended": False, "fixturePath": f"https://example.invalid/{book}/{f['fixtureId']}",
            "markets": markets}


def _public(f):
    return {k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in f.items() if not k.startswith("_")}


class MockOddsPapi:
    def __init__(self, cfg=None, api_key=None):
        self.calls = 0

    def account(self):
        return {"request_limit": 100000, "request_count": 0}

    def sports(self):
        self.calls += 1
        return SPORTS

    def markets(self):
        self.calls += 1
        return MARKETS

    def fixtures(self, sport_id, start, end, bookmakers):
        self.calls += 1
        return [_public(f) for f in _fixtures_between(start, end) if f["sportId"] == sport_id and f["startTime"] > now_utc()]

    def odds_by_tournaments(self, tournament_ids, bookmaker):
        self.calls += 1
        now = now_utc()
        fx = [f for f in _fixtures_between(now - dt.timedelta(hours=1), now + dt.timedelta(days=3))
              if f["tournamentId"] in set(map(int, tournament_ids)) and f["startTime"] > now]
        return [{**_public(f), "bookmakerOdds": {bookmaker: _prices(f, bookmaker, now)}} for f in fx]

    def settlements(self, fixture_id):
        self.calls += 1
        f = _fixture(fixture_id)
        markets = {}
        if now_utc() < f["startTime"] + dt.timedelta(hours=2):
            for mid, true in f["_true"].items():
                outs = next(m for m in MARKETS if m["marketId"] == mid)["outcomes"]
                markets[str(mid)] = {"outcomes": {str(o["outcomeId"]): {"players": {"0": {"result": "UNDECIDED"}}} for o in outs}}
            return {"fixtureId": fixture_id, "markets": markets}
        for mid, true in f["_true"].items():
            outs = next(m for m in MARKETS if m["marketId"] == mid)["outcomes"]
            win = _rng("res", fixture_id, mid).choices(range(len(true)), weights=true)[0]
            markets[str(mid)] = {"outcomes": {str(o["outcomeId"]): {"players": {"0": {"result": "WIN" if k == win else "LOSE"}}}
                                              for k, o in enumerate(outs)}}
        return {"fixtureId": fixture_id, "markets": markets}
