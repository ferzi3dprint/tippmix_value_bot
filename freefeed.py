"""Ingyenes adatforrás: az OddsPapi /v4/historical-odds végpontja (nem számít bele a havi keretbe).

Egy kérés = egy meccs teljes odds-előzménye legfeljebb 3 irodától. Ebből rakjuk össze:
  - a "most érvényes" oddsot (kimenetelenként a legutolsó bejegyzés) ugyanabban a szerkezetben, mint a /v4/odds,
  - a Pinnacle fair árát egy korábbi időpontban (steam-felismeréshez).
"""
import datetime as dt

from common import parse_time
from valuefinder import devig, market_prices


def _entries(players):
    rows = (players or {}).get("0") or []
    if isinstance(rows, dict):          # biztonság kedvéért, ha egyetlen objektum jönne
        rows = [rows]
    return sorted((r for r in rows if r.get("createdAt")), key=lambda r: r["createdAt"])


def snapshot(book_hist, at=None):
    """A könyv állapota egy adott időpontban (alapból: a legfrissebb), /v4/odds-szerű szerkezetben."""
    if not book_hist:
        return None
    markets = {}
    for mid, m in (book_hist.get("markets") or {}).items():
        outs = {}
        for oid, o in (m.get("outcomes") or {}).items():
            rows = _entries(o.get("players"))
            if at is not None:
                rows = [r for r in rows if parse_time(r["createdAt"]) <= at]
            if rows:
                last = rows[-1]
                outs[str(oid)] = {"players": {"0": {"price": last.get("price"), "active": last.get("active", True),
                                                    "changedAt": last["createdAt"]}}}
        if outs:
            markets[str(mid)] = {"marketActive": True, "outcomes": outs}
    return {"bookmakerIsActive": True, "suspended": False, "markets": markets}


def first_complete_time(book_hist, market_id, outcome_ids):
    """Az az időpont, amikortól a piac minden kimenetelének van ára."""
    m = ((book_hist or {}).get("markets") or {}).get(str(market_id)) or {}
    firsts = []
    for oid in outcome_ids:
        rows = _entries(((m.get("outcomes") or {}).get(str(oid)) or {}).get("players"))
        if not rows:
            return None
        firsts.append(parse_time(rows[0]["createdAt"]))
    return max(firsts)


def steam_from_history(sharp_hist, market_id, outcome_ids, now, cfg):
    """Kimenetelenként a Pinnacle fair valószínűségének relatív emelkedése az ablak elejéhez képest (ha ≥ min_move)."""
    sc = cfg.get("steam") or {}
    if not sc.get("enabled") or not sharp_hist:
        return {}
    start = first_complete_time(sharp_hist, market_id, outcome_ids)
    if start is None:
        return {}
    base_t = max(start, now - dt.timedelta(hours=sc["window_hours"]))
    if now - base_t < dt.timedelta(minutes=sc["min_age_minutes"]):
        return {}
    old = market_prices(snapshot(sharp_hist, base_t), market_id, outcome_ids)
    cur = market_prices(snapshot(sharp_hist), market_id, outcome_ids)
    if not old or not cur:
        return {}
    lim = cfg["scan"]["max_sharp_overround"]
    if not (0.98 <= sum(1 / o for o in old) <= lim and 0.98 <= sum(1 / o for o in cur) <= lim):
        return {}
    meth = cfg["scan"].get("devig_method", "power")
    b, c = devig(old, meth), devig(cur, meth)
    return {i: round(y / x - 1, 4) for i, (x, y) in enumerate(zip(b, c)) if x > 0 and y / x - 1 >= sc["min_move"]}
