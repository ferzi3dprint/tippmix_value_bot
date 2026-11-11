"""A value-keresés matematikája: árrés-mentesítés (devig), várható érték, szűrők, tétméret.

Fair valószínűség: a Pinnacle (sharp) piac összes kimenetelének oddsából, az árrés kivétele után.
EV = Tippmix-odds × fair valószínűség − 1.
"""
import math


# --- devig -----------------------------------------------------------------

def devig_multiplicative(odds):
    inv = [1 / o for o in odds]
    s = sum(inv)
    return [p / s for p in inv]


def devig_power(odds):
    """p_i = (1/o_i)^k, ahol k-t úgy választjuk, hogy Σp = 1. Az esélytelenekből több árrést vesz ki, mint a favoritokból
    (a favorit–esélytelen torzítás miatt ez általában pontosabb, mint az arányos módszer)."""
    inv = [1 / o for o in odds]
    if sum(inv) <= 1:
        return devig_multiplicative(odds)
    lo, hi = 1.0, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(p ** k for p in inv) > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    ps = [p ** k for p in inv]
    s = sum(ps)
    return [p / s for p in ps]


def devig(odds, method="power"):
    return devig_power(odds) if method == "power" else devig_multiplicative(odds)


def overround(odds):
    return sum(1 / o for o in odds)


# --- szűrők és tét ---------------------------------------------------------

def min_ev_for(odds, cfg):
    for band in cfg["filters"]["ev_bands"]:
        if odds <= band["max_odds"]:
            return band["min_ev"]
    return cfg["filters"]["ev_bands"][-1]["min_ev"]


def odds_band(odds):
    for lo, hi in ((1.0, 1.5), (1.5, 1.8), (1.8, 2.1), (2.1, 2.5), (2.5, 99)):
        if lo < odds <= hi:
            return f"{lo:.2f}–{hi:.2f}" if hi < 99 else f"{lo:.2f}+"
    return "?"


def kelly_stake(prob, odds, bankroll, available, cfg):
    st = cfg["staking"]
    f = (prob * odds - 1) / (odds - 1)
    if f <= 0:
        return 0
    stake = bankroll * f * st["kelly_fraction"]
    stake = min(stake, bankroll * st["max_stake_pct"], available)
    stake = math.floor(stake / st["round_to"]) * st["round_to"]
    return stake if stake >= st["min_stake"] else 0


# --- odds kinyerése --------------------------------------------------------

def _price(outcome):
    p = ((outcome or {}).get("players") or {}).get("0") or {}
    if p.get("active") is False:
        return None
    try:
        v = float(p.get("price"))
    except (TypeError, ValueError):
        return None
    return v if v > 1.0 else None


def market_prices(book_odds, market_id, outcome_ids):
    """A piac összes kimenetelének oddsa, vagy None, ha bármelyik hiányzik / a piac nem aktív."""
    if not book_odds or book_odds.get("suspended") or book_odds.get("bookmakerIsActive") is False:
        return None
    m = (book_odds.get("markets") or {}).get(str(market_id))
    if not m or m.get("marketActive") is False:
        return None
    prices = [_price((m.get("outcomes") or {}).get(str(o))) for o in outcome_ids]
    return prices if all(prices) else None


def find_values(fixture, soft, sharp, markets_meta, cfg):
    """Egy meccs összes közös piacán megkeresi a value kimeneteleket. Visszaad: jelöltlista (EV szerint csökkenő)."""
    out = []
    flt, sc = cfg["filters"], cfg["scan"]
    for mid, meta in markets_meta.items():
        if sc.get("skip_player_props", True) and meta.get("playerProp"):
            continue
        oids = [o["outcomeId"] for o in meta["outcomes"]]
        if len(oids) < 2:
            continue
        sp, hp = market_prices(soft, mid, oids), market_prices(sharp, mid, oids)
        if not sp or not hp:
            continue
        ovr = overround(hp)
        if ovr > sc["max_sharp_overround"] or ovr < 0.98:
            continue
        fair = devig(hp, sc.get("devig_method", "power"))
        for oid, oname, so, p, ho in zip(oids, [o["outcomeName"] for o in meta["outcomes"]], sp, fair, hp):
            if not (flt["min_odds"] <= so <= flt["max_odds"]):
                continue
            ev = so * p - 1
            if ev < min_ev_for(so, cfg) or ev > flt["max_ev"]:
                continue
            out.append({"market_id": int(mid), "market": meta["marketName"], "outcome_id": int(oid), "outcome": oname,
                        "odds": so, "sharp_odds": ho, "fair_prob": round(p, 5), "fair_odds": round(1 / p, 3),
                        "ev": round(ev, 5), "sharp_overround": round(ovr, 4)})
    return sorted(out, key=lambda x: -x["ev"])


MARKET_HU = [("Full Time Result", "1X2"), ("Both Teams To Score", "Mindkét csapat szerez gólt"), ("Over Under Full Time", "Gólszám"),
             ("Over Under", "Gólszám"), ("Double Chance", "Kétesély"), ("Draw No Bet", "Döntetlennél visszajár"),
             ("Asian Handicap", "Ázsiai hendikep"), ("Handicap", "Hendikep"), ("Winner", "Győztes"), ("Moneyline", "Győztes"),
             ("Totals", "Összpontszám")]
OUTCOME_HU = {"over": "Több", "under": "Kevesebb", "yes": "Igen", "no": "Nem"}


def market_hu(name):
    for en, hu in MARKET_HU:
        if str(name).startswith(en):
            return hu + str(name)[len(en):]
    return str(name)


def outcome_label(fixture, cand):
    """Olvasható kimenetel: '1' -> hazai csapat neve stb."""
    o = str(cand["outcome"])
    o = OUTCOME_HU.get(o.lower(), o)
    if o == "1":
        o = f"{fixture.get('participant1Name')} győz"
    elif o == "2":
        o = f"{fixture.get('participant2Name')} győz"
    elif o.upper() == "X":
        o = "Döntetlen"
    return f"{market_hu(cand['market'])}: {o}"
