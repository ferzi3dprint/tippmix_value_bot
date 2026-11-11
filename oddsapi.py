"""OddsPapi v4 kliens (https://oddspapi.io/en/docs).

Csak az itt használt végpontok: account, sports, markets, fixtures, odds-by-tournaments, settlements.
Végpontonkénti várakozás (cooldown) a dokumentáció szerint; 429-nél egyszer újrapróbál.
"""
import os
import time

import requests

COOLDOWN = {  # másodperc, a dokumentáció szerint
    "/v4/fixtures": 2.0,
    "/v4/odds-by-tournaments": 1.0,
    "/v4/settlements": 2.0,
    "/v4/markets": 1.0,
}


class QuotaExceeded(Exception):
    pass


class OddsPapi:
    def __init__(self, cfg, api_key=None):
        self.base = cfg["api"]["base_url"].rstrip("/")
        self.key = api_key or os.environ["ODDSPAPI_KEY"]
        self.calls = 0          # ebben a futásban elhasznált (számlázott) kérések
        self._last = {}
        self.s = requests.Session()

    def _get(self, path, **params):
        wait = COOLDOWN.get(path, 0.5) - (time.monotonic() - self._last.get(path, -1e9))
        if wait > 0:
            time.sleep(wait)
        params = {k: v for k, v in params.items() if v is not None}
        params["apiKey"] = self.key
        for attempt in range(2):
            r = self.s.get(self.base + path, params=params, timeout=40)
            self._last[path] = time.monotonic()
            if path != "/v4/account":
                self.calls += 1
            if r.status_code == 429:
                body = r.text[:300]
                if "REQUEST_LIMIT_EXCEEDED" in body:
                    raise QuotaExceeded(body)
                time.sleep(COOLDOWN.get(path, 1.0) * 2)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{path} HTTP {r.status_code}: {r.text[:200].replace(self.key, '***')}")
            return r.json()
        raise RuntimeError(f"{path}: túl sok kérés (429)")

    # --- végpontok ---
    def account(self):
        return self._get("/v4/account")

    def sports(self):
        return self._get("/v4/sports")

    def markets(self):
        return self._get("/v4/markets")

    def fixtures(self, sport_id, start, end, bookmakers):
        return self._get("/v4/fixtures", sportId=sport_id, **{"from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")}, statusId=0, hasOdds="true", bookmakers=bookmakers)

    def odds_by_tournaments(self, tournament_ids, bookmaker):
        return self._get("/v4/odds-by-tournaments", tournamentIds=",".join(map(str, tournament_ids)),
                         bookmaker=bookmaker, oddsFormat="decimal", verbosity=2)

    def settlements(self, fixture_id):
        return self._get("/v4/settlements", fixtureId=fixture_id)
