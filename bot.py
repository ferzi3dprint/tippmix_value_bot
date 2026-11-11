"""Egy futás: elszámolás → odds-letöltés → value-keresés → szimulált fogadás → értesítés → (este) napi kimutatás.

Futtatás:  python bot.py            (éles adat, ODDSPAPI_KEY kell)
           MOCK_API=1 python bot.py (szintetikus adat, kulcs nélkül)
"""
import datetime as dt
import os
import sys
import traceback
import uuid
from zoneinfo import ZoneInfo

import ledger
import notify
import report
from common import load_config, load_json, save_json, now_utc, parse_time, to_local
from valuefinder import devig, find_values, kelly_stake, market_prices, odds_band, outcome_label


def make_client(cfg):
    if os.getenv("MOCK_API") == "1":
        from mockapi import MockOddsPapi
        return MockOddsPapi(cfg)
    from oddsapi import OddsPapi
    return OddsPapi(cfg)


# --- gyorsítótárak (kevesebb fizetős kérés) ---------------------------------

def cached(name, max_age_h, fetch, now):
    c = load_json(f"cache/{name}.json", None)
    if c and now - parse_time(c["time"]) < dt.timedelta(hours=max_age_h):
        return c["data"]
    data = fetch()
    save_json(f"cache/{name}.json", {"time": now.isoformat(), "data": data})
    return data


def markets_meta(client, now):
    rows = cached("markets", 24 * 7, client.markets, now)
    return {int(m["marketId"]): m for m in rows}


def sport_ids(client, cfg, now):
    rows = cached("sports", 24 * 7, client.sports, now)
    want = {s.lower() for s in cfg["scan"]["sports"]}
    return {int(s["sportId"]): s.get("sportName") or s.get("sportSlug") for s in rows
            if str(s.get("sportName") or s.get("sportSlug") or "").lower() in want}


def tournaments(client, cfg, now):
    def fetch():
        books = f"{cfg['api']['soft_book']},{cfg['api']['sharp_book']}"
        end = now + dt.timedelta(hours=cfg["scan"]["lookahead_hours"])
        ids = {}
        for sid, sname in sport_ids(client, cfg, now).items():
            for f in client.fixtures(sid, now, end, books) or []:
                ids[str(f["tournamentId"])] = sname
        return ids
    return cached("tournaments", cfg["api"]["tournaments_refresh_hours"], fetch, now)


# --- kvóta ------------------------------------------------------------------

def quota_ok(client, cfg, now, planned_calls):
    """A havi keret egyenletes elosztása a hónap hátralévő napjaira. Visszaad: (mehet-e, szöveges állapot)."""
    try:
        acc = client.account()
    except Exception as e:
        return True, f"kvóta ismeretlen ({e.__class__.__name__})"
    limit, used = acc.get("request_limit"), acc.get("request_count")
    if limit is None or used is None:
        return True, "kvóta ismeretlen"
    remaining = limit - used
    local = to_local(now, cfg)
    q = load_json("quota.json", {})
    if q.get("date") != local.date().isoformat():
        q = {"date": local.date().isoformat(), "start_remaining": remaining}
    next_month = (local.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    days_left = (next_month.date() - local.date()).days
    allowed_today = max(0, (q["start_remaining"] - cfg["api"]["quota_reserve"])) / max(1, days_left)
    used_today = q["start_remaining"] - remaining
    q.update({"remaining": remaining, "limit": limit, "allowed_today": round(allowed_today, 1), "used_today": used_today})
    save_json("quota.json", q)
    ok = used_today + planned_calls <= allowed_today and remaining - planned_calls > cfg["api"]["quota_reserve"]
    return ok, f"havi keret: {used}/{limit}, ma: {used_today}/{allowed_today:.0f}"


# --- lépések ----------------------------------------------------------------

def settle_due(client, bets, cfg, now):
    st = cfg["settlement"]
    due = sorted({b["fixture_id"]: b for b in bets if b["status"] == "open"
                  and now >= ledger.start_time(b) + dt.timedelta(hours=st["settle_after_hours"])}.values(),
                 key=lambda b: b["start"])
    settled = []
    for b0 in due[: cfg["api"]["max_settlement_calls_per_run"]]:
        fid = b0["fixture_id"]
        try:
            res = client.settlements(fid) or {}
        except Exception as e:
            print(f"[elszámolás] {fid}: {e}")
            continue
        for b in ledger.bets_on_fixture(bets, fid):
            if b["status"] != "open":
                continue
            r = ((((res.get("markets") or {}).get(str(b["market_id"])) or {}).get("outcomes") or {})
                 .get(str(b["outcome_id"])) or {}).get("players", {}).get("0", {}).get("result")
            if r in ledger.FINAL:
                ledger.settle(b, r, now)
                settled.append(b)
            elif now >= ledger.start_time(b) + dt.timedelta(hours=st["give_up_after_hours"]):
                ledger.settle(b, "VOID", now)
                settled.append(b)
    return settled


def _index(rows, slug):
    out = {}
    for f in rows or []:
        bo = f.get("bookmakerOdds") or {}
        odds = bo.get(slug) or (next(iter(bo.values())) if len(bo) == 1 else None)
        if odds:
            out[f["fixtureId"]] = (f, odds)
    return out


def scan(client, bets, cfg, now, stats):
    api, sc = cfg["api"], cfg["scan"]
    meta = markets_meta(client, now)
    tours = tournaments(client, cfg, now)
    ids = sorted(tours, key=int)
    n = api["tournaments_per_call"]
    chunks = [ids[i:i + n] for i in range(0, len(ids), n)]
    ok, qtxt = quota_ok(client, cfg, now, 2 * len(chunks))
    stats["quota"] = qtxt
    if not ok:
        stats["skipped"] = "napi kvótakeret elfogyott"
        return []
    soft, sharp = {}, {}
    for ch in chunks:
        soft.update(_index(client.odds_by_tournaments(ch, api["soft_book"]), api["soft_book"]))
        sharp.update(_index(client.odds_by_tournaments(ch, api["sharp_book"]), api["sharp_book"]))
    common_ids = soft.keys() & sharp.keys()
    stats["fixtures_soft"], stats["fixtures_sharp"], stats["fixtures_both"] = len(soft), len(sharp), len(common_ids)

    # záró ár (CLV) frissítése a még el nem kezdődött nyitott fogadásoknál
    for b in bets:
        if b["status"] == "open" and b["fixture_id"] in sharp and ledger.start_time(b) > now:
            m = meta.get(b["market_id"])
            if not m:
                continue
            oids = [o["outcomeId"] for o in m["outcomes"]]
            hp = market_prices(sharp[b["fixture_id"]][1], b["market_id"], oids)
            if hp:
                p = devig(hp, sc.get("devig_method", "power"))[oids.index(b["outcome_id"])]
                b.update({"closing_fair_prob": round(p, 5), "closing_fair_odds": round(1 / p, 3),
                          "closing_sharp_odds": hp[oids.index(b["outcome_id"])], "closing_at": now.isoformat(),
                          "clv": round(b["odds"] * p - 1, 5)})

    cands = []
    for fid in common_ids:
        fx, so = soft[fid]
        _, ho = sharp[fid]
        start = parse_time(fx["startTime"])
        mins = (start - now).total_seconds() / 60
        if mins < sc["min_minutes_before_start"] or mins > sc["lookahead_hours"] * 60:
            continue
        sub = {mid: meta[mid] for mid in map(int, (so.get("markets") or {}).keys()) if mid in meta}
        for c in find_values(fx, so, ho, sub, cfg):
            cands.append((fx, so, c))
    stats["candidates"] = len(cands)

    placed = []
    br = ledger.bankroll(bets, cfg)
    for fx, so, c in sorted(cands, key=lambda x: -x[2]["ev"]):
        fid = fx["fixtureId"]
        if ledger.already_bet(bets, fid, c["market_id"], c["outcome_id"]):
            continue
        if len(ledger.bets_on_fixture(bets, fid)) >= cfg["filters"]["max_bets_per_fixture"]:
            continue
        open_st = ledger.open_stake(bets)
        available = min(br - open_st, br * cfg["staking"]["max_open_exposure_pct"] - open_st)
        stake = kelly_stake(c["fair_prob"], c["odds"], br, available, cfg)
        if stake <= 0:
            continue
        b = {"id": uuid.uuid4().hex[:10], "placed_at": now.isoformat(), "status": "open",
             "fixture_id": fid, "sport": tours.get(str(fx.get("tournamentId"))) or fx.get("sportName") or "",
             "tournament": fx.get("tournamentName") or str(fx.get("tournamentId")),
             "home": fx.get("participant1Name"), "away": fx.get("participant2Name"), "start": fx["startTime"],
             "label": outcome_label(fx, c), "band": odds_band(c["odds"]), "stake": stake,
             "expected_profit": round(stake * c["ev"], 2), "link": so.get("fixturePath"), **c}
        bets.append(b)
        placed.append(b)
    return placed


def daily_due(cfg, now):
    """Melyik napról kell kimutatás (ha esedékes és még nem ment ki)."""
    local = to_local(now, cfg)
    sent = load_json("sent.json", {})
    h = cfg["notify"]["daily_report_hour"]
    if local.hour >= h:
        day = local.date()
    elif local.hour < 5:
        day = local.date() - dt.timedelta(days=1)   # késő GitHub-futás éjfél után
    else:
        return None
    return None if sent.get("daily") == day.isoformat() else day


def run(cfg):
    now = now_utc()
    client = make_client(cfg)
    bets = ledger.load()
    stats = {"time": now.isoformat()}
    try:
        settled = settle_due(client, bets, cfg, now)
        stats["settled"] = len(settled)
        new = scan(client, bets, cfg, now, stats)
        stats["placed"] = len(new)
    except Exception as e:
        stats["error"] = f"{e.__class__.__name__}: {str(e)[:200]}"
        traceback.print_exc()
        new = []
    finally:
        ledger.save(bets)
    stats["api_calls"] = client.calls
    runs = load_json("runs.json", [])
    runs.append(stats)
    save_json("runs.json", runs[-300:])
    print("[futás]", stats)

    url = (cfg.get("dashboard") or {}).get("url") if (cfg.get("dashboard") or {}).get("enabled") else None
    if new and cfg["notify"]["each_run_new_bets"]:
        notify.send(report.new_bets_message(new, bets, cfg))
    day = daily_due(cfg, now)
    if day:
        sent = notify.send(report.daily_message(bets, cfg, day, url))
        if sent or os.getenv("MOCK_API") == "1" or os.getenv("DRY_RUN") == "1":
            save_json("sent.json", {**load_json("sent.json", {}), "daily": day.isoformat()})
    return stats


if __name__ == "__main__":
    st = run(load_config())
    sys.exit(1 if st.get("error") and not st.get("placed") and not st.get("settled") else 0)
