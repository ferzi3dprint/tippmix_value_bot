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
    {"marketId": 1010, "marketName": "Over Under Full Time", "marketLength": 2, "playerProp": False, "sportId": 10,
     "handicap": 2.5, "period": "fulltime", "marketType": "totals",
     "outcomes": [{"outcomeId": 1010, "outcomeName": "Over"}, {"outcomeId": 1011, "outcomeName": "Under"}]},
    {"marketId": 121, "marketName": "Winner", "marketLength": 2, "playerProp": False, "sportId": 12,
     "handicap": 0, "period": "result", "marketType": "moneyline",
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


def _news(f):
    """A meccsek ~35%-ánál a kezdés előtt 1–10 órával "hír" jön (sérülés, kezdőcsapat): a valódi esély elmozdul.
    A Pinnacle azonnal követi; a Tippmix 0–90 perc késéssel (vagy a következő rendszeres frissítéskor)."""
    r = _rng("news", f["fixtureId"])
    if r.random() > 0.35:
        return None
    t = f["startTime"] - dt.timedelta(hours=r.uniform(1, 10))
    shift = {}
    for mid, true in f["_true"].items():
        k = r.randrange(len(true))
        new = list(true)
        new[k] = min(0.92, true[k] + r.uniform(0.05, 0.10))
        shift[mid] = _norm([p if i == k else max(0.03, p - (new[k] - true[k]) / (len(true) - 1)) for i, p in enumerate(new)])
    return {"time": t, "true": shift, "tm_delay": dt.timedelta(minutes=r.uniform(0, 90))}


def _true_at(f, t):
    n = _news(f)
    return n["true"] if n and t >= n["time"] else f["_true"]


def _prices(f, book, now):
    hours_left = max(0.0, (f["startTime"] - now).total_seconds() / 3600)
    markets = {}
    truth_now = _true_at(f, now)
    n = _news(f)
    for mid in f["_true"]:
        if book == "pinnacle":
            sd = 0.004 + 0.02 * min(hours_left, 36) / 36           # a kezdéshez közeledve pontosodik
            r = _rng("pin", f["fixtureId"], mid, int(hours_left))
            est = _norm([max(0.02, p + r.gauss(0, sd)) for p in truth_now[mid]])
            odds = [round(1 / (p * 1.025), 3) for p in est]
        else:
            k = int(hours_left // 8)                                  # rendszeres frissítés 8 óránként
            last_update = f["startTime"] - dt.timedelta(hours=(k + 1) * 8)
            if n and now >= n["time"] + n["tm_delay"]:
                last_update = max(last_update, n["time"] + n["tm_delay"])
            r = _rng("tm", f["fixtureId"], mid, k)
            est = _norm([max(0.03, p + r.gauss(0, 0.03)) for p in _true_at(f, last_update)[mid]])
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
        self.free_calls = 0

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

    def odds(self, fixture_id, bookmakers):
        self.calls += 1
        f, now = _fixture(fixture_id), now_utc()
        return {**_public(f), "bookmakerOdds": {b: _prices(f, b, now) for b in bookmakers.split(",")}}

    def historical_odds(self, fixture_id, bookmakers):
        """Ingyenes előzmény: az utolsó 4,5 óra félóránkénti állapota + a mostani, csak a változásokkal."""
        self.free_calls += 1
        f, now = _fixture(fixture_id), now_utc()
        times = [now - dt.timedelta(minutes=30 * k) for k in range(9, 0, -1)] + [now]
        out = {}
        for book in bookmakers.split(","):
            hist = {}
            for t in times:
                if t > f["startTime"]:
                    continue
                for mid, m in _prices(f, book, t)["markets"].items():
                    for oid, o in m["outcomes"].items():
                        rows = hist.setdefault(mid, {}).setdefault(oid, [])
                        p = o["players"]["0"]["price"]
                        if not rows or rows[-1]["price"] != p:
                            rows.append({"createdAt": t.isoformat(), "price": p, "active": True, "limit": None})
            out[book] = {"markets": {mid: {"outcomes": {oid: {"players": {"0": list(reversed(rows))}}
                                                         for oid, rows in outs.items()}} for mid, outs in hist.items()}}
        return {"fixtureId": fixture_id, "bookmakers": out}

    def settlements(self, fixture_id):
        self.calls += 1
        f = _fixture(fixture_id)
        markets = {}
        if now_utc() < f["startTime"] + dt.timedelta(hours=2):
            for mid, true in f["_true"].items():
                outs = next(m for m in MARKETS if m["marketId"] == mid)["outcomes"]
                markets[str(mid)] = {"outcomes": {str(o["outcomeId"]): {"players": {"0": {"result": "UNDECIDED"}}} for o in outs}}
            return {"fixtureId": fixture_id, "markets": markets}
        for mid, true in _true_at(f, f["startTime"]).items():
            outs = next(m for m in MARKETS if m["marketId"] == mid)["outcomes"]
            win = _rng("res", fixture_id, mid).choices(range(len(true)), weights=true)[0]
            markets[str(mid)] = {"outcomes": {str(o["outcomeId"]): {"players": {"0": {"result": "WIN" if k == win else "LOSE"}}}
                                              for k, o in enumerate(outs)}}
        return {"fixtureId": fixture_id, "markets": markets}


class MockTippmix:
    """A Tippmix-adatfolyam utánzata: ugyanazokat a kitalált meccseket adja magyar sportnévvel, a "tippmixpro" árakkal."""
    calls = 0

    def today_matches(self, now, tz="Europe/Budapest", sports=("Labdarúgás", "Tenisz"), days_ahead=0):
        from zoneinfo import ZoneInfo
        end = (now.astimezone(ZoneInfo(tz)).replace(hour=23, minute=59, second=59) + dt.timedelta(days=days_ahead)).astimezone(UTC)
        hu = {10: "Labdarúgás", 12: "Tenisz"}
        return [{"id": "tm-" + f["fixtureId"], "sport": hu[f["sportId"]], "name": f"{f['participant1Name']} - {f['participant2Name']}",
                 "home": f["participant1Name"], "away": f["participant2Name"], "home_pid": "h", "away_pid": "a",
                 "tournament": f["tournamentName"], "start": f["startTime"], "_fid": f["fixtureId"]}
                for f in _fixtures_between(now, end) if f["startTime"] > now and hu[f["sportId"]] in sports]

    def all_today_matches(self, now, tz="Europe/Budapest", sports=("Labdarúgás", "Tenisz"), days_ahead=0):
        return self.today_matches(now, tz, sports, days_ahead)

    def match_markets(self, match):
        f = _fixture(match["id"][3:])
        book = _prices(f, "tippmixpro", now_utc())
        out = {}
        keys = {101: ("main", 0.0, ["home", "draw", "away"]), 1010: ("ou", 2.5, ["over", "under"]), 121: ("main", 0.0, ["home", "away"])}
        for mid, m in book["markets"].items():
            kind, line, names = keys[int(mid)]
            out[(kind, line)] = {n: o["players"]["0"]["price"] for n, o in zip(names, m["outcomes"].values())}
        return out
