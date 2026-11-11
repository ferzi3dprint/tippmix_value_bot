"""Egy futás: elszámolás → odds-letöltés → value- és steam-keresés → szimulált fogadás → értesítés →
 pár perc múlva végrehajthatóság-ellenőrzés + steam-es meccsek újranézése → (este) napi kimutatás.

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
from common import load_config, load_json, save_json, now_utc, parse_time, sleep_minutes, to_local
from freefeed import snapshot, steam_from_history
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
    rows = cached("sports", 24 * 30, client.sports, now)
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

def paid_calls_left(client, cfg):
    """Ennyi fizetős kérés használható még a hónapban (a tartalék felett). A /v4/account hívás ingyenes."""
    try:
        acc = client.account()
        left = acc["request_limit"] - acc["request_count"] - cfg["api"]["quota_reserve"]
        save_json("quota.json", {**load_json("quota.json", {}), "limit": acc["request_limit"],
                                 "remaining": acc["request_limit"] - acc["request_count"]})
        return max(0, left)
    except Exception:
        return 10 ** 6


def settle_due(client, bets, cfg, now):
    st = cfg["settlement"]
    retry = dt.timedelta(hours=st.get("retry_hours", 2))
    due = sorted({b["fixture_id"]: b for b in bets if b["status"] == "open"
                  and now >= ledger.start_time(b) + dt.timedelta(hours=st["settle_after_hours"])
                  and (not b.get("settle_tried_at") or now - parse_time(b["settle_tried_at"]) >= retry)}.values(),
                 key=lambda b: b["start"])
    settled = []
    budget = min(cfg["api"]["max_settlement_calls_per_run"], paid_calls_left(client, cfg)) if due else 0
    for b0 in due[:budget]:
        fid = b0["fixture_id"]
        for b in ledger.bets_on_fixture(bets, fid):
            b["settle_tried_at"] = now.isoformat()
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


# --- ⚡ steam: a Pinnacle fair árának előzménye ------------------------------

HIST = "cache/sharp_history.json"


def record_sharp(hist, fid, fx, sharp_odds, meta, now, cfg):
    """Elmenti a meccs piacainak aktuális Pinnacle fair valószínűségeit (a steam-felismeréshez)."""
    e = hist.setdefault(fid, {"start": fx["startTime"], "m": {}})
    for mid in map(int, (sharp_odds.get("markets") or {}).keys()):
        m = meta.get(mid)
        if not m:
            continue
        hp = market_prices(sharp_odds, mid, [o["outcomeId"] for o in m["outcomes"]])
        if hp and 0.98 <= sum(1 / o for o in hp) <= cfg["scan"]["max_sharp_overround"]:
            pts = e["m"].setdefault(str(mid), [])
            if not pts or pts[-1][0] != now.isoformat():
                pts.append([now.isoformat(), [round(p, 5) for p in devig(hp, cfg["scan"].get("devig_method", "power"))]])


def prune_history(hist, now, cfg):
    keep = dt.timedelta(hours=cfg["steam"]["window_hours"] + 1)
    for fid in list(hist):
        if parse_time(hist[fid]["start"]) < now:
            del hist[fid]
            continue
        for mid, pts in hist[fid]["m"].items():
            hist[fid]["m"][mid] = [p for p in pts if now - parse_time(p[0]) <= keep]


def steam_moves(hist, fid, mid, now, cfg):
    """Kimenetelenként a fair valószínűség relatív emelkedése az ablak legrégebbi pontjához képest (ha ≥ min_move)."""
    sc = cfg.get("steam") or {}
    if not sc.get("enabled"):
        return {}
    pts = ((hist.get(fid) or {}).get("m") or {}).get(str(mid)) or []
    if len(pts) < 2:
        return {}
    win, min_age = dt.timedelta(hours=sc["window_hours"]), dt.timedelta(minutes=sc["min_age_minutes"])
    old = [p for p in pts if win >= now - parse_time(p[0]) >= min_age]
    if not old:
        return {}
    base, cur = old[0][1], pts[-1][1]
    return {i: round(c / b - 1, 4) for i, (b, c) in enumerate(zip(base, cur)) if b > 0 and c / b - 1 >= sc["min_move"]}


def evaluate(fx, so, ho, meta, hist, now, cfg, steam_fn=None):
    """Egy meccs value-jelöltjei, steam-jelöléssel. Visszaad: (jelöltek, van-e steam a meccsen).
    steam_fn(market_id, outcome_ids) -> {index: mozgás}; alapból a saját előzményből (fizetős mód)."""
    sc = cfg["scan"]
    start = parse_time(fx["startTime"])
    mins = (start - now).total_seconds() / 60
    if mins < sc["min_minutes_before_start"] or mins > sc["lookahead_hours"] * 60:
        return [], False
    sub = {mid: meta[mid] for mid in map(int, (so.get("markets") or {}).keys()) if mid in meta}
    cands, steamy = [], False
    for mid, m in sub.items():
        oids = [o["outcomeId"] for o in m["outcomes"]]
        mv = steam_fn(mid, oids) if steam_fn else steam_moves(hist, fx["fixtureId"], mid, now, cfg)
        steamy = steamy or bool(mv)
        for c in find_values(fx, so, ho, {mid: m}, cfg):
            idx = [o["outcomeId"] for o in m["outcomes"]].index(c["outcome_id"])
            c["steam"] = mv.get(idx)
            cands.append(c)
    return cands, steamy


def place(cands, bets, cfg, now, tours):
    """Szimulált fogadás a jelöltekre (EV szerint), a tőke- és meccskorlátokkal."""
    placed = []
    br = ledger.bankroll(bets, cfg)
    pending = "pending" if (cfg.get("execution") or {}).get("enabled") else "ok"
    cap = (cfg.get("free") or {}).get("max_bets_per_day") if cfg["api"].get("mode") == "free" else None
    tz = ZoneInfo(cfg["timezone"])
    today = to_local(now, cfg).date()
    for fx, so, c in sorted(cands, key=lambda x: -x[2]["ev"]):
        fid = fx["fixtureId"]
        if cap is not None and sum(1 for b in bets if b["status"] != "skipped" and ledger.placed_day(b, tz) == today) >= cap:
            break
        if ledger.already_bet(bets, fid, c["market_id"], c["outcome_id"]):
            continue
        if len(ledger.active_on_fixture(bets, fid)) >= cfg["filters"]["max_bets_per_fixture"]:
            continue
        open_st = ledger.open_stake(bets)
        available = min(br - open_st, br * cfg["staking"]["max_open_exposure_pct"] - open_st)
        stake = kelly_stake(c["fair_prob"], c["odds"], br, available, cfg)
        if stake <= 0:
            continue
        b = {"id": uuid.uuid4().hex[:10], "placed_at": now.isoformat(), "status": "open", "exec_status": pending,
             "fixture_id": fid, "sport": tours.get(str(fx.get("tournamentId"))) or fx.get("sportName") or "",
             "tournament": fx.get("tournamentName") or str(fx.get("tournamentId")),
             "home": fx.get("participant1Name"), "away": fx.get("participant2Name"), "start": fx["startTime"],
             "label": outcome_label(fx, c), "band": odds_band(c["odds"]), "stake": stake,
             "signal_odds": c["odds"], "signal_ev": c["ev"], "link": so.get("fixturePath"), **c}
        b["trigger"] = "steam" if c.get("steam") else "normál"
        b["expected_profit"] = round(stake * c["ev"], 2)
        bets.append(b)
        placed.append(b)
    return placed


def update_closing(rows, sharp, meta, now, cfg):
    """Záró ár (CLV) frissítése a még el nem kezdődött nyitott fogadásoknál / jelzéseknél. sharp: {fixtureId: könyv}."""
    for b in rows:
        if b.get("status", "open") == "open" and b["fixture_id"] in sharp and ledger.start_time(b) > now:
            m = meta.get(b["market_id"])
            if not m:
                continue
            oids = [o["outcomeId"] for o in m["outcomes"]]
            hp = market_prices(sharp[b["fixture_id"]], b["market_id"], oids)
            if hp and b["outcome_id"] in oids:
                i = oids.index(b["outcome_id"])
                p = devig(hp, cfg["scan"].get("devig_method", "power"))[i]
                b.update({"closing_fair_prob": round(p, 5), "closing_fair_odds": round(1 / p, 3),
                          "closing_sharp_odds": hp[i], "closing_at": now.isoformat(), "clv": round(b["odds"] * p - 1, 5)})


# --- árnyék-jelzések: minden value-jelzés CLV-je, fogadástól függetlenül ------

SIGNALS = "signals.json"


def record_signals(signals, cands, bets, now):
    have = {(x["fixture_id"], x["market_id"], x["outcome_id"]) for x in signals}
    for fx, so, c in cands:
        key = (fx["fixtureId"], c["market_id"], c["outcome_id"])
        if key in have:
            continue
        have.add(key)
        signals.append({"fixture_id": fx["fixtureId"], "market_id": c["market_id"], "outcome_id": c["outcome_id"],
                        "first_at": now.isoformat(), "start": fx["startTime"], "home": fx.get("participant1Name"),
                        "away": fx.get("participant2Name"), "tournament": fx.get("tournamentName"),
                        "label": outcome_label(fx, c), "odds": c["odds"], "fair_prob": c["fair_prob"], "ev": c["ev"],
                        "band": odds_band(c["odds"]), "trigger": "steam" if c.get("steam") else "normál"})
    for x in signals:
        x["bet"] = ledger.already_bet(bets, x["fixture_id"], x["market_id"], x["outcome_id"])


def prune_signals(signals, now, days=60):
    return [x for x in signals if now - parse_time(x["start"]) < dt.timedelta(days=days)]


# --- ingyenes mód --------------------------------------------------------------

def free_fixtures(client, cfg, now):
    """Napi meccslista (sportáganként 1 fizetős kérés), gyorsítótárazva."""
    fr = cfg["free"]

    def fetch():
        books = f"{cfg['api']['soft_book']},{cfg['api']['sharp_book']}"
        end = now + dt.timedelta(hours=fr["discovery_window_hours"])
        out = {}
        for sid, sname in sport_ids(client, cfg, now).items():
            for f in client.fixtures(sid, now, end, books) or []:
                out[f["fixtureId"]] = {k: f.get(k) for k in ("fixtureId", "startTime", "participant1Name", "participant2Name",
                                                              "tournamentId", "tournamentName", "sportName")}
                out[f["fixtureId"]]["sportName"] = out[f["fixtureId"]].get("sportName") or sname
        return out
    c = load_json("cache/fixtures.json", None)
    fresh_enough = c and now - parse_time(c["time"]) < dt.timedelta(hours=fr["discovery_hours"])
    if not fresh_enough and paid_calls_left(client, cfg) < len(cfg["scan"]["sports"]) + 2:
        print("[meccslista] a havi keret elfogyott – a régi listával megy tovább")
        return (c or {}).get("data", {})
    return cached("fixtures", fr["discovery_hours"], fetch, now)


def pick_fixtures(fixtures, cfg, now):
    """Melyik meccseket nézzük meg most (ingyenes kérések, 5 mp/kérés): a közelieket mindig, a távoliakat ritkábban."""
    fr, sc = cfg["free"], cfg["scan"]
    checked = load_json("cache/checked.json", {})
    near, far = [], []
    for fid, f in fixtures.items():
        mins = (parse_time(f["startTime"]) - now).total_seconds() / 60
        if mins < sc["min_minutes_before_start"] or mins > sc["lookahead_hours"] * 60:
            continue
        last = checked.get(fid)
        if mins <= fr["near_start_hours"] * 60:
            near.append((mins, fid))
        elif not last or now - parse_time(last) >= dt.timedelta(minutes=fr["far_recheck_minutes"]):
            far.append((parse_time(last) if last else dt.datetime.min.replace(tzinfo=dt.timezone.utc), fid))
    return [fid for _, fid in sorted(near)] + [fid for _, fid in sorted(far)]


def fetch_free(client, cfg, fid, fixtures, now):
    """Egy meccs a díjmentes előzmény-végpontból: (fixture, tippmix-könyv, pinnacle-könyv, steam_fn)."""
    api = cfg["api"]
    r = client.historical_odds(fid, f"{api['soft_book']},{api['sharp_book']}") or {}
    books = r.get("bookmakers") or {}
    sh, sf = books.get(api["sharp_book"]), books.get(api["soft_book"])
    fx = dict(fixtures.get(fid) or {"fixtureId": fid})
    return fx, snapshot(sf), snapshot(sh), (lambda mid, oids: steam_from_history(sh, mid, oids, now, cfg))


def scan_free(client, bets, cfg, now, stats, signals):
    meta = markets_meta(client, now)
    fixtures = free_fixtures(client, cfg, now)
    order = pick_fixtures(fixtures, cfg, now)[: cfg["free"]["max_fixtures_per_run"]]
    checked = {k: v for k, v in load_json("cache/checked.json", {}).items() if k in fixtures}
    cands, hot, sharp_now, both = [], [], {}, 0
    for fid in order:
        try:
            fx, so, ho, steam_fn = fetch_free(client, cfg, fid, fixtures, now)
        except Exception as e:
            print(f"[ingyenes odds] {fid}: {e}")
            continue
        checked[fid] = now.isoformat()
        if ho:
            sharp_now[fid] = ho
        if not so or not ho:
            continue
        both += 1
        cs, steamy = evaluate(fx, so, ho, meta, None, now, cfg, steam_fn)
        cands += [(fx, so, c) for c in cs]
        if steamy and len(ledger.active_on_fixture(bets, fid)) < cfg["filters"]["max_bets_per_fixture"]:
            hot.append(fid)
    save_json("cache/checked.json", checked)
    update_closing(bets, sharp_now, meta, now, cfg)
    update_closing(signals, sharp_now, meta, now, cfg)
    stats.update({"fixtures_listed": len(fixtures), "fixtures_checked": len(order), "fixtures_both": both,
                  "candidates": len(cands), "steam_fixtures": len(hot)})
    new = place(cands, bets, cfg, now, {})
    record_signals(signals, cands, bets, now)
    return new, hot


def scan(client, bets, cfg, now, stats, hist, signals):
    api = cfg["api"]
    meta = markets_meta(client, now)
    tours = tournaments(client, cfg, now)
    ids = sorted(tours, key=int)
    n = api["tournaments_per_call"]
    chunks = [ids[i:i + n] for i in range(0, len(ids), n)]
    ok, qtxt = quota_ok(client, cfg, now, 2 * len(chunks))
    stats["quota"] = qtxt
    if not ok:
        stats["skipped"] = "napi kvótakeret elfogyott"
        return [], []
    soft, sharp = {}, {}
    for ch in chunks:
        soft.update(_index(client.odds_by_tournaments(ch, api["soft_book"]), api["soft_book"]))
        sharp.update(_index(client.odds_by_tournaments(ch, api["sharp_book"]), api["sharp_book"]))
    common_ids = soft.keys() & sharp.keys()
    stats["fixtures_soft"], stats["fixtures_sharp"], stats["fixtures_both"] = len(soft), len(sharp), len(common_ids)

    for fid, (fx, ho) in sharp.items():
        record_sharp(hist, fid, fx, ho, meta, now, cfg)
    update_closing(bets, {k: v[1] for k, v in sharp.items()}, meta, now, cfg)
    update_closing(signals, {k: v[1] for k, v in sharp.items()}, meta, now, cfg)

    cands, hot = [], []
    for fid in common_ids:
        fx, so = soft[fid]
        cs, steamy = evaluate(fx, so, sharp[fid][1], meta, hist, now, cfg)
        cands += [(fx, so, c) for c in cs]
        if steamy and len(ledger.active_on_fixture(bets, fid)) < cfg["filters"]["max_bets_per_fixture"]:
            hot.append(fid)
    stats["candidates"] = len(cands)
    stats["steam_fixtures"] = len(hot)
    new = place(cands, bets, cfg, now, tours)
    record_signals(signals, cands, bets, now)
    return new, hot


# --- végrehajthatóság és gyors újraellenőrzés -------------------------------

def recheck(client, bets, cfg, now, hot, hist, stats):
    """A jelzés után pár perccel: (1) megerősíti a függő fogadásokat a friss Tippmix-oddsszal,
    (2) újranézi a steam-es meccseket (meccsenként 1 kérés, mindkét iroda). Visszaad: (új fogadások, elesett fogadások)."""
    api, ex = cfg["api"], cfg.get("execution") or {}
    meta = markets_meta(client, now)
    tours = load_json("cache/tournaments.json", {}).get("data", {})
    due = [b for b in bets if b.get("exec_status") == "pending" and b["status"] == "open"
           and now - parse_time(b["placed_at"]) >= dt.timedelta(minutes=ex.get("confirm_after_minutes", 3))]
    fids = list(dict.fromkeys([b["fixture_id"] for b in due] + hot[: cfg["steam"]["recheck_fixtures"]]))
    fresh = {}
    free = api.get("mode") == "free"
    fixtures = load_json("cache/fixtures.json", {}).get("data", {}) if free else {}
    for fid in fids:
        try:
            if free:   # díjmentes előzmény-végpont
                fresh[fid] = fetch_free(client, cfg, fid, fixtures, now)
            else:      # fizetős: 1 kérés meccsenként, mindkét iroda
                r = client.odds(fid, f"{api['soft_book']},{api['sharp_book']}")
                bo = r.get("bookmakerOdds") or {}
                fresh[fid] = (r, bo.get(api["soft_book"]), bo.get(api["sharp_book"]), None)
        except Exception as e:
            print(f"[újraellenőrzés] {fid}: {e}")
    stats["rechecked"] = len(fresh)

    gone = []
    for b in due:
        if b["fixture_id"] not in fresh:
            continue
        fx, so, ho, _ = fresh[b["fixture_id"]]
        m = meta.get(b["market_id"])
        oids = [o["outcomeId"] for o in m["outcomes"]] if m else []
        sp = market_prices(so, b["market_id"], oids) if m else None
        hp = market_prices(ho, b["market_id"], oids) if m else None
        i = oids.index(b["outcome_id"]) if m else 0
        p_now = devig(hp, cfg["scan"].get("devig_method", "power"))[i] if hp else b["fair_prob"]
        exec_odds = sp[i] if sp else None
        b["exec_checked_at"], b["exec_odds"] = now.isoformat(), exec_odds
        if exec_odds is not None and exec_odds >= b["signal_odds"]:
            b["exec_status"] = "ok"
        elif exec_odds is not None and exec_odds * p_now - 1 >= ex.get("min_ev_after_move", 0.0):
            b["exec_status"] = "moved"
            b["odds"] = exec_odds
            b["ev"] = round(exec_odds * b["fair_prob"] - 1, 5)
            b["band"] = odds_band(exec_odds)
            b["expected_profit"] = round(b["stake"] * b["ev"], 2)
            if b.get("clv") is not None and b.get("closing_fair_prob"):
                b["clv"] = round(exec_odds * b["closing_fair_prob"] - 1, 5)
        else:
            b["exec_status"], b["status"] = "gone", "skipped"
            gone.append(b)

    # steam-es meccsek újranézése: lehet, hogy a Pinnacle tovább mozdult, a Tippmix még nem
    cands = []
    for fid in hot[: cfg["steam"]["recheck_fixtures"]]:
        if fid not in fresh or not fresh[fid][1] or not fresh[fid][2]:
            continue
        fx, so, ho, steam_fn = fresh[fid]
        if not free:
            record_sharp(hist, fid, fx, ho, meta, now, cfg)
        cs, _ = evaluate(fx, so, ho, meta, hist, now, cfg, steam_fn)
        cands += [(fx, so, c) for c in cs]
    new = place(cands, bets, cfg, now, tours)
    for b in new:
        b["exec_status"] = "pending"   # a következő futás erősíti meg
    return new, gone


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
    hist = load_json(HIST, {})
    signals = load_json(SIGNALS, [])
    stats = {"time": now.isoformat(), "mode": cfg["api"].get("mode", "paid")}
    url = (cfg.get("dashboard") or {}).get("url") if (cfg.get("dashboard") or {}).get("enabled") else None
    new, gone = [], []
    try:
        settled = settle_due(client, bets, cfg, now)
        stats["settled"] = len(settled)
        if (cfg.get("execution") or {}).get("enabled"):
            _, g = recheck(client, bets, cfg, now, [], hist, stats)       # előző futásból maradt függő fogadások
            gone += g
        if cfg["api"].get("mode") == "free":
            new, hot = scan_free(client, bets, cfg, now, stats, signals)
        else:
            new, hot = scan(client, bets, cfg, now, stats, hist, signals)
        ledger.save(bets)
        if new and cfg["notify"]["each_run_new_bets"]:
            notify.send(report.new_bets_message(new, bets, cfg))
        if (cfg.get("execution") or {}).get("enabled") and (new or hot):
            sleep_minutes(cfg["execution"]["confirm_after_minutes"])
            now = now_utc()
            more, g = recheck(client, bets, cfg, now, hot, hist, stats)
            gone += g
            if more and cfg["notify"]["each_run_new_bets"]:
                notify.send(report.new_bets_message(more, bets, cfg, rechecked=True))
            new += more
        stats["placed"] = len(new)
        stats["not_executable"] = len(gone)
        if gone and cfg["notify"]["each_run_new_bets"]:
            notify.send(report.gone_message(gone))
    except Exception as e:
        stats["error"] = f"{e.__class__.__name__}: {str(e)[:200]}"
        traceback.print_exc()
    finally:
        ledger.save(bets)
        prune_history(hist, now, cfg)
        save_json(HIST, hist)
        save_json(SIGNALS, prune_signals(signals, now))
    stats["api_calls"] = client.calls
    stats["free_calls"] = getattr(client, "free_calls", 0)
    runs = load_json("runs.json", [])
    runs.append(stats)
    save_json("runs.json", runs[-300:])
    print("[futás]", stats)

    day = daily_due(cfg, now)
    if day:
        sent = notify.send(report.daily_message(bets, cfg, day, url))
        if sent or os.getenv("MOCK_API") == "1" or os.getenv("DRY_RUN") == "1":
            save_json("sent.json", {**load_json("sent.json", {}), "daily": day.isoformat()})
    return stats


if __name__ == "__main__":
    st = run(load_config())
    sys.exit(1 if st.get("error") and not st.get("placed") and not st.get("settled") else 0)
