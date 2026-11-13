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
import tippmixfeed
import classicfeed
from freefeed import snapshot, steam_from_history
from matcher import match_fixtures, odds_consistent
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


def scan_sports(cfg):
    """A figyelt sportágak (OddsPapi-nevek); "all" = minden sportág, amelyet a Tippmix is kínál."""
    sp = cfg["scan"]["sports"]
    return list(tippmixfeed.SPORT_HU) if sp in ("all", ["all"]) else list(sp)


def sport_ids(client, cfg, now):
    rows = cached("sports", 24 * 30, client.sports, now)
    want = {s.lower() for s in scan_sports(cfg)}
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


def daily_bet_cap(cfg, now):
    """Napi szimulált-fogadás plafon: a hónapban megmaradt fizetős keretet osztja el a hátralévő napokra
    (minden fogadás elszámolása 1 kérés, a meccslista sportáganként napi 1). Legalább a configban megadott alap."""
    base = (cfg.get("free") or {}).get("max_bets_per_day", 3)
    q = load_json("quota.json", {})
    if not q.get("limit") or q.get("remaining") is None:
        return base
    local = to_local(now, cfg)
    next_month = (local.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    days_left = max(1, (next_month.date() - local.date()).days)
    fixed = days_left + 6          # meccslisták + piaclista-frissítés
    spare = q["remaining"] - cfg["api"]["quota_reserve"] - fixed
    return max(base, int(spare / days_left))


def place(cands, bets, cfg, now, tours, channel="pro"):
    """Szimulált fogadás a jelöltekre (EV szerint), a tőke- és meccskorlátokkal.
    channel: "pro" (Tippmixpro) vagy "classic" (lottózós Tippmix) – külön tőke, külön korlátok, külön kimutatás."""
    placed = []
    all_bets, bets = bets, ledger.channel(bets, channel)
    br = ledger.bankroll(bets, cfg)
    pending = "pending" if (cfg.get("execution") or {}).get("enabled") else "ok"
    cap = daily_bet_cap(cfg, now) if cfg["api"].get("mode") in ("free", "hu") else None
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
             "signal_odds": c["odds"], "signal_ev": c["ev"], "link": fx.get("tm_url") or so.get("fixturePath"), "tm_id": fx.get("tm_id"), "tm_url": fx.get("tm_url"), **c}
        b["trigger"] = "steam" if c.get("steam") else "normál"
        b["tm_tip"] = ((((so.get("markets") or {}).get(str(c["market_id"])) or {}).get("outcomes") or {})
                       .get(str(c["outcome_id"])) or {}).get("tm")
        b["min_odds"] = round((1 + (cfg.get("execution") or {}).get("min_ev_after_move", 0.01)) / c["fair_prob"], 2)
        b["expected_profit"] = round(stake * c["ev"], 2)
        if channel != "pro":
            b["channel"] = channel
        bets.append(b)
        all_bets.append(b)
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


# --- magyar üzemmód: Tippmixpro közvetlenül, Pinnacle az OddsPapiból ------------------

_TM = None


def tm_session():
    """Egy Tippmix-kapcsolat futásonként (a demóban/tesztben szintetikus)."""
    global _TM
    if _TM is None:
        if os.getenv("MOCK_API") == "1":
            from mockapi import MockTippmix
            _TM = MockTippmix()
        else:
            _TM = tippmixfeed.Session().__enter__()
    return _TM


def close_tm():
    global _TM
    if _TM is not None and hasattr(_TM, "__exit__"):
        try:
            _TM.__exit__(None, None, None)
        except Exception:
            pass
    _TM = None


def end_of_day(now, cfg):
    """A figyelt időszak vége: ma éjfél + hu.days_ahead nap (0 = csak ma, 1 = ma és holnap)."""
    local = to_local(now, cfg)
    days = (cfg.get("hu") or {}).get("days_ahead", 0)
    return (local.replace(hour=23, minute=59, second=59, microsecond=0) + dt.timedelta(days=days)).astimezone(dt.timezone.utc)


def hu_fixtures(client, cfg, now):
    """A mai Pinnacle-meccsek (sportáganként napi 1 fizetős kérés)."""
    today = to_local(now, cfg).date().isoformat()
    c = load_json("cache/fixtures_hu.json", None)
    key = [scan_sports(cfg), ref_books(cfg), cfg["hu"].get("days_ahead", 0)]
    if c and c.get("date") == today and c.get("key") == key:
        return c["data"]
    if paid_calls_left(client, cfg) < 3:
        print("[meccslista] a havi keret elfogyott – a régi listával megy tovább")
        return (c or {}).get("data", {})
    out, want = {}, set(scan_sports(cfg))
    by_id = {v: k for k, v in tippmixfeed.OP_IDS.items()}
    # EGYETLEN fizetős kérés az összes sportágra (sportId nélkül)
    for f in client.fixtures(None, now, end_of_day(now, cfg), ",".join(ref_books(cfg))) or []:
        sname = by_id.get(f.get("sportId")) or f.get("sportName")
        if sname not in want:
            continue
        out[f["fixtureId"]] = {k: f.get(k) for k in ("fixtureId", "startTime", "participant1Name", "participant2Name",
                                                      "tournamentId", "tournamentName")}
        out[f["fixtureId"]]["sportName"] = sname
    save_json("cache/fixtures_hu.json", {"date": today, "time": now.isoformat(), "key": key, "data": out})
    return out


def ref_books(cfg):
    """Viszonyítási irodák: a Pinnacle + a konszenzushoz használt további irodák (legfeljebb 3 az ingyenes végponton)."""
    return [cfg["api"]["sharp_book"]] + list((cfg.get("hu") or {}).get("consensus_books") or [])[:2]


def reference_book(books, meta, cfg):
    """A fair ár forrása piaconként: ha a Pinnacle árazza, a Pinnacle; különben a többi iroda árrés-mentes átlaga
    (legalább consensus_min_books irodából). A konszenzusos piacokat megjelöli (szigorúbb EV-küszöb)."""
    pin = snapshot(books.get(cfg["api"]["sharp_book"])) or {"bookmakerIsActive": True, "markets": {}}
    out = {"bookmakerIsActive": True, "suspended": False, "markets": dict(pin.get("markets") or {})}
    hu = cfg.get("hu") or {}
    others = [snapshot(books.get(b)) for b in hu.get("consensus_books") or [] if books.get(b)]
    others = [o for o in others if o]
    if not others:
        return out
    meth, lim = cfg["scan"].get("devig_method", "power"), cfg["scan"]["max_sharp_overround"]
    mids = set().union(*[set((o.get("markets") or {}).keys()) for o in others])
    for mid in mids:
        m = meta.get(int(mid))
        if not m:
            continue
        oids = [o["outcomeId"] for o in m["outcomes"]]
        if market_prices(out, int(mid), oids):
            continue                      # a Pinnacle árazza – az az elsődleges
        probs = []
        for o in others:
            pr = market_prices(o, int(mid), oids)
            if pr and 0.98 <= sum(1 / x for x in pr) <= lim + 0.04:
                probs.append(devig(pr, meth))
        if len(probs) >= hu.get("consensus_min_books", 2):
            avg = [sum(p[i] for p in probs) / len(probs) for i in range(len(oids))]
            out["markets"][str(mid)] = {"marketActive": True, "consensus": len(probs), "outcomes": {
                str(oid): {"players": {"0": {"price": round(1 / p, 4), "active": True}}} for oid, p in zip(oids, avg)}}
    return out


def fetch_hu(client, cfg, pair, now, meta):
    """Egy párosított meccs: (fixture a bothoz, Tippmix-könyv, Pinnacle-könyv, steam_fn)."""
    sess = tm_session()
    m, fx = pair["tm"], pair["fx"]
    tm_sport = tippmixfeed.SPORT_HU.get(fx["sportName"], fx["sportName"])
    names = {}
    tm_markets = sess.match_markets({**m, "sport": tm_sport}, names)
    so = tippmixfeed.to_op_book(tm_markets, tm_sport, tippmixfeed.op_index(meta), names)
    try:
        h = client.historical_odds(fx["fixtureId"], ",".join(ref_books(cfg))) or {}
    except RuntimeError as e:
        if "HTTP 400" not in str(e):
            raise
        h = client.historical_odds(fx["fixtureId"], cfg["api"]["sharp_book"]) or {}
    books = h.get("bookmakers") or {}
    sh = books.get(cfg["api"]["sharp_book"])
    ho = reference_book(books, meta, cfg)
    fxb = {"fixtureId": fx["fixtureId"], "startTime": fx["startTime"], "participant1Name": m["home"],
           "participant2Name": m["away"], "tournamentName": m.get("tournament") or fx.get("tournamentName"),
           "tournamentId": fx.get("tournamentId"), "sportName": fx["sportName"], "tm_id": m["id"],
           "tm_url": tippmixfeed.match_url({**m, "sport": tm_sport})}
    cl = pair.get("cl")
    if cl and (cfg.get("classic") or {}).get("enabled"):
        try:
            cn = {}
            cm = classicfeed.event_markets(cl["id"], tm_sport, cn)
            fxb["cl_book"] = tippmixfeed.to_op_book(cm, tm_sport, tippmixfeed.op_index(meta), cn)
            fxb["cl_id"] = cl["id"]
        except Exception as e:
            print(f"[klasszikus] {cl.get('id')}: {e.__class__.__name__}: {str(e)[:120]}")
    return fxb, so, ho, (lambda mid, oids: steam_from_history(sh, mid, oids, now, cfg))


def main_consistent(so, ho, meta, cfg):
    """Téves párosítás elleni védelem a fő piacon (1X2 / győztes)."""
    for mid in (101, 121, 111, 151, 113, 153):
        m = meta.get(mid)
        if not m:
            continue
        oids = [o["outcomeId"] for o in m["outcomes"]]
        sp, hp = market_prices(so, mid, oids), market_prices(ho, mid, oids)
        if sp and hp:
            return odds_consistent(sp, hp, cfg["hu"]["max_prob_diff"])
    return None     # a fő piac nem összevethető (pl. a viszonyítás nem árazza) – a piaconkénti szűrő véd


def check_boosts(cfg, now, stats):
    """Oddsrakéta-figyelő: új emelt odds esetén Telegram-értesítés (egyszer ajánlatonként)."""
    items, raw = tm_session().boosts()
    seen = load_json("cache/boosts_seen.json", {})
    new = [b for b in items if b["key"] not in seen]
    stats["boosts"] = len(items)
    if raw and not load_json("cache/boost_sample.json", None):
        save_json("cache/boost_sample.json", raw[:2])      # szerkezet-minta a későbbi EV-számításhoz
    if new:
        lines = ["🚀 Új Oddsrakéta (Tippmixpro odds-emelés):"]
        for b in new[:10]:
            lines.append(f"• {b['title']}\n   {b['market'] or ''} → {b['tip']} @ {str(b['odds']).replace('.', ',')}")
        lines.append("ℹ️ Az emelt odds gyakran a fair ár fölött van, de jellemzően alacsony a maximális tét. EV-t még nem számolok hozzá.")
        notify.send("\n".join(lines), buttons=[("🟢 Oddsrakéta a Tippmixpro-n", "https://www.tippmixpro.hu/hu/fogadas/i/oddsraketa/101")])
        for b in new:
            seen[b["key"]] = now.isoformat()
        save_json("cache/boosts_seen.json", seen)


def scan_hu(client, bets, cfg, now, stats, signals, fast=False):
    meta = markets_meta(client, now)
    if fast:
        return scan_hu_pairs(client, bets, cfg, now, stats, signals, meta, load_json("cache/pairs_hu.json", {}), fast=True)
    fixtures = hu_fixtures(client, cfg, now)
    sports_hu = [tippmixfeed.SPORT_HU.get(s, s) for s in scan_sports(cfg)]
    days = cfg["hu"].get("days_ahead", 0)
    c = load_json("cache/tm_matches.json", None)
    if c and c.get("date") == to_local(now, cfg).date().isoformat() and \
            now - parse_time(c["time"]) < dt.timedelta(hours=cfg["hu"].get("full_list_hours", 3)) and c.get("sports") == sports_hu \
            and c.get("days") == days and c.get("v") == 2:
        tm = [{**m, "start": parse_time(m["start"])} for m in c["data"] if parse_time(m["start"]) > now]
        tm_ids = {m["id"] for m in tm}
        tm += [m for m in tm_session().today_matches(now, cfg["timezone"], sports_hu, days) if m["id"] not in tm_ids]
    else:   # teljes lista bajnokságonként (kb. 1 perc), néhány óránként
        tm = tm_session().all_today_matches(now, cfg["timezone"], sports_hu, days)
        save_json("cache/tm_matches.json", {"date": to_local(now, cfg).date().isoformat(), "time": now.isoformat(),
                                            "sports": sports_hu, "days": days, "v": 2, "data": [{**m, "start": m["start"].isoformat()} for m in tm]})
    pairs = {}
    for sp_en in scan_sports(cfg):
        sp_hu = tippmixfeed.SPORT_HU.get(sp_en, sp_en)
        fx = [f for f in fixtures.values() if f["sportName"] == sp_en]
        tms = [m for m in tm if m["sport"] == sp_hu]
        for tid, (f, score) in match_fixtures(tms, fx, threshold=cfg["hu"]["match_threshold"]).items():
            m = next(x for x in tms if x["id"] == tid)
            pairs[f["fixtureId"]] = {"tm": {**m, "start": m["start"].isoformat()}, "fx": f, "score": score}
    if (cfg.get("classic") or {}).get("enabled") and os.getenv("MOCK_API") != "1":
        try:
            pair_classic(pairs, tm, cfg, now, stats)
        except Exception as e:
            print(f"[klasszikus lista] {e.__class__.__name__}: {str(e)[:150]}")
    save_json("cache/pairs_hu.json", pairs)
    stats.update({"tippmix_matches": len(tm), "pinnacle_fixtures": len(fixtures), "paired": len(pairs),
                  "by_sport": {sp: [sum(1 for m in tm if m["sport"] == tippmixfeed.SPORT_HU.get(sp, sp)),
                                    sum(1 for p in pairs.values() if p["fx"]["sportName"] == sp)] for sp in scan_sports(cfg)}})

    return scan_hu_pairs(client, bets, cfg, now, stats, signals, meta, pairs)


def classic_events(cfg, now, sports_hu):
    """A klasszikus Tippmix meccslistája (néhány óránként frissítve, gyorsítótárazva)."""
    c = load_json("cache/cl_events.json", None)
    if c and now - parse_time(c["time"]) < dt.timedelta(hours=cfg["hu"].get("full_list_hours", 3)) and c.get("sports") == sports_hu:
        return [{**e, "start": parse_time(e["start"])} for e in c["data"] if parse_time(e["start"]) > now]
    until = end_of_day(now, cfg)
    ev = classicfeed.events(now, until, sports_hu)
    save_json("cache/cl_events.json", {"time": now.isoformat(), "sports": sports_hu,
                                       "data": [{**e, "start": e["start"].isoformat()} for e in ev]})
    return ev


def pair_classic(pairs, tm, cfg, now, stats):
    """A már párosított Tippmixpro-meccsekhez a klasszikus Tippmix eseménye (azonos magyar nevek, azonos kezdés)."""
    sports_hu = sorted({m["sport"] for m in tm})
    ev = classic_events(cfg, now, sports_hu)
    by_tm = {p["tm"]["id"]: fid for fid, p in pairs.items()}
    n = 0
    for sp in sports_hu:
        tms = [m for m in tm if m["sport"] == sp and m["id"] in by_tm]
        fx = [{"fixtureId": e["id"], "startTime": e["start"].isoformat(), "participant1Name": e["home"],
               "participant2Name": e["away"], "_e": e} for e in ev if e["sport"] == sp]
        for tid, (f, score) in match_fixtures(tms, fx, threshold=cfg["hu"]["match_threshold"]).items():
            e = f["_e"]
            pairs[by_tm[tid]]["cl"] = {"id": e["id"], "name": e["name"], "score": score}
            n += 1
    stats["classic"] = {"events": len(ev), "paired": n}


def scan_hu_pairs(client, bets, cfg, now, stats, signals, meta, pairs, fast=False):
    # melyik párokat nézzük most (Pinnacle-előzmény: 5 mp/kérés)
    fr, sc = cfg["free"], cfg["scan"]
    checked = {k: v for k, v in load_json("cache/checked.json", {}).items() if k in pairs}
    near, far = [], []
    for fid, p in pairs.items():
        mins = (parse_time(p["fx"]["startTime"]) - now).total_seconds() / 60
        if mins < sc["min_minutes_before_start"]:
            continue
        if fast:      # gyors kör: csak a hamarosan kezdődők
            if mins <= cfg["hu"]["fast_window_hours"] * 60:
                near.append((mins, fid))
            continue
        last = checked.get(fid)
        if mins <= fr["near_start_hours"] * 60:
            near.append((mins, fid))
        elif not last or now - parse_time(last) >= dt.timedelta(minutes=fr["far_recheck_minutes"]):
            far.append((mins, fid))
    order = [f for _, f in sorted(near)] + [f for _, f in sorted(far)]
    order = order[: fr["max_fixtures_per_run"]]

    cands, hot, sharp_now, both, rejected, near_miss, rej_ex, no_main = [], [], {}, 0, 0, [], [], 0
    cl_cands, cl_miss, cl_checked = [], [], 0
    LOOSE = {**cfg, "filters": {**cfg["filters"], "min_odds": 1.01, "max_odds": 50, "max_ev": 0.25,
                                "ev_bands": [{"max_odds": 999, "min_ev": -1.0}]}}
    for fid in order:
        try:
            fx, so, ho, steam_fn = fetch_hu(client, cfg, pairs[fid], now, meta)
        except Exception as e:
            print(f"[hu] {fid}: {e.__class__.__name__}: {str(e)[:150]}")
            continue
        checked[fid] = now.isoformat()
        if ho:
            sharp_now[fid] = ho
        if not so or not ho or not so.get("markets"):
            continue
        ok_main = main_consistent(so, ho, meta, cfg)
        if ok_main is None:
            no_main += 1
        if ok_main is False:
            rejected += 1
            if len(rej_ex) < 8:
                tmm = pairs[fid]["tm"]
                fx0 = pairs[fid]["fx"]
                mk = next((mid for mid in (101, 121, 111, 151) if str(mid) in (so.get("markets") or {})), None)
                oids = [o["outcomeId"] for o in meta[mk]["outcomes"]] if mk in meta else []
                rej_ex.append(f"{tmm.get('name')} <-> {fx0.get('participant1Name')} - {fx0.get('participant2Name')} "
                              f"[{pairs[fid].get('score')}] TM {market_prices(so, mk, oids) if mk else '-'} / ref {market_prices(ho, mk, oids) if mk else '-'}")
            continue
        both += 1
        cs, steamy = evaluate(fx, so, ho, meta, None, now, cfg, steam_fn)
        cands += [(fx, so, c) for c in cs]
        if steamy and len(ledger.active_on_fixture(bets, fid)) < cfg["filters"]["max_bets_per_fixture"]:
            hot.append(fid)
        for c in find_values(fx, so, ho, meta, LOOSE):          # átláthatóság: a legjobb EV-k küszöbtől függetlenül
            near_miss.append((c["ev"], f"{fx['participant1Name']} – {fx['participant2Name']} · {outcome_label(fx, c)} @{c['odds']} (fair {c['fair_odds']})"))
        cb = fx.get("cl_book")
        if cb and cb.get("markets") and main_consistent(cb, ho, meta, cfg) is not False:
            cl_checked += 1
            fxc = {**fx, "tm_url": classicfeed.HOME, "tm_id": fx.get("cl_id")}
            cs2, _ = evaluate(fxc, cb, ho, meta, None, now, cfg, steam_fn)
            cl_cands += [(fxc, cb, c) for c in cs2]
            for c in find_values(fxc, cb, ho, meta, LOOSE):
                cl_miss.append((c["ev"], f"{fx['participant1Name']} – {fx['participant2Name']} · {outcome_label(fx, c)} @{c['odds']} (fair {c['fair_odds']})"))
    save_json("cache/checked.json", checked)
    stats["top_ev"] = [f"{ev * 100:+.1f}% {txt}" for ev, txt in sorted(near_miss, reverse=True)[:5]]
    stats["rejected_examples"] = rej_ex
    stats["no_main_market"] = no_main
    update_closing(bets, sharp_now, meta, now, cfg)
    update_closing(signals, sharp_now, meta, now, cfg)
    stats.update({"fast": fast, "fixtures_checked": len(order), "fixtures_both": both, "rejected_mismatch": rejected,
                  "candidates": len(cands), "steam_fixtures": len(hot)})
    new = place(cands, bets, cfg, now, {})
    record_signals(signals, cands, ledger.channel(bets, "pro"), now)
    if (cfg.get("classic") or {}).get("enabled"):
        stats["classic_checked"] = cl_checked
        stats["classic_candidates"] = len(cl_cands)
        stats["top_ev_classic"] = [f"{ev * 100:+.1f}% {txt}" for ev, txt in sorted(cl_miss, reverse=True)[:5]]
        new += place(cl_cands, bets, cfg, now, {}, channel="classic")
    return new, hot


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
    if not fresh_enough and paid_calls_left(client, cfg) < len(scan_sports(cfg)) + 2:
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
        for c in find_values(fx, so, ho, meta, LOOSE):          # átláthatóság: a legjobb EV-k küszöbtől függetlenül
            near_miss.append((c["ev"], f"{fx['participant1Name']} – {fx['participant2Name']} · {outcome_label(fx, c)} @{c['odds']} (fair {c['fair_odds']})"))
    save_json("cache/checked.json", checked)
    stats["top_ev"] = [f"{ev * 100:+.1f}% {txt}" for ev, txt in sorted(near_miss, reverse=True)[:5]]
    stats["rejected_examples"] = rej_ex
    stats["no_main_market"] = no_main
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

MOVED = []   # a 3 perces ellenőrzésnél módosult (de még megérő) ajánlások – Telegram-frissítéshez


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
    free = api.get("mode") in ("free", "hu")
    fixtures = load_json("cache/fixtures.json", {}).get("data", {}) if free else {}
    for fid in fids:
        try:
            if api.get("mode") == "hu":
                pr = load_json("cache/pairs_hu.json", {}).get(fid)
                if not pr:
                    continue
                pr = {**pr, "tm": {**pr["tm"], "start": parse_time(pr["tm"]["start"])}}
                fresh[fid] = fetch_hu(client, cfg, pr, now, meta)
            elif free:   # díjmentes előzmény-végpont
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
        if b.get("channel") == "classic":
            so = fx.get("cl_book") or {}
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
            # friss Pinnacle-valószínűség (steamnél a fair ár is elmozdul) – különben elavult, akár negatív EV-t mutatna
            b["fair_prob"] = round(p_now, 5)
            b["fair_odds"] = round(1 / p_now, 3)
            b["min_odds"] = round((1 + ex.get("min_ev_after_move", 0.01)) / p_now, 2)
            b["ev"] = round(exec_odds * p_now - 1, 5)
            MOVED.append(b)
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
    if os.getenv("MOCK_API") != "1" and not os.getenv("ODDSPAPI_KEY"):
        print("[bot] ODDSPAPI_KEY nincs beállítva (Settings → Secrets → Actions) – a futás kimarad.")
        return {"time": now.isoformat(), "skipped": "nincs API-kulcs"}
    client = make_client(cfg)
    bets = ledger.load()
    hist = load_json(HIST, {})
    signals = load_json(SIGNALS, [])
    stats = {"time": now.isoformat(), "mode": cfg["api"].get("mode", "paid")}
    url = (cfg.get("dashboard") or {}).get("url") if (cfg.get("dashboard") or {}).get("enabled") else None
    new, gone = [], []
    MOVED.clear()
    try:
        last_full = load_json("cache/last_full.json", {}).get("time")
        quick = cfg["api"].get("mode") == "hu" and bool(last_full) and \
            now - parse_time(last_full) < dt.timedelta(minutes=cfg["hu"].get("full_every_minutes", 20) - 1)
        settled = [] if quick else settle_due(client, bets, cfg, now)
        if not quick and os.getenv("MOCK_API") != "1":
            paid_calls_left(client, cfg)         # kvóta-állapot frissítése (ingyenes hívás) a napi plafonhoz
        stats["settled"] = len(settled)
        if (cfg.get("execution") or {}).get("enabled"):
            _, g = recheck(client, bets, cfg, now, [], hist, stats)       # előző futásból maradt függő fogadások
            gone += g
        if cfg["api"].get("mode") == "hu" and not quick and (cfg.get("hu") or {}).get("watch_boosts", True) \
                and os.getenv("MOCK_API") != "1":
            try:
                check_boosts(cfg, now, stats)
            except Exception as e:
                print(f"[oddsrakéta] {e.__class__.__name__}: {str(e)[:150]}")
        if cfg["api"].get("mode") == "hu":
            last_full = load_json("cache/last_full.json", {}).get("time")
            fast = bool(last_full) and now - parse_time(last_full) < dt.timedelta(minutes=cfg["hu"].get("full_every_minutes", 20) - 1) \
                and os.getenv("FORCE_FULL") != "1"
            new, hot = scan_hu(client, bets, cfg, now, stats, signals, fast=fast)
            if not fast:
                save_json("cache/last_full.json", {"time": now.isoformat()})
        elif cfg["api"].get("mode") == "free":
            new, hot = scan_free(client, bets, cfg, now, stats, signals)
        else:
            new, hot = scan(client, bets, cfg, now, stats, hist, signals)
        ledger.save(bets)
        for b in new:
            print(f"[fogadás] {b.get('home')} – {b.get('away')} | {b.get('market')} {b.get('outcome')} @{b.get('odds')} tét {b.get('stake')}")
        if new and cfg["notify"]["each_run_new_bets"]:
            notify.send(report.new_bets_message(new, bets, cfg))
            for b in new:
                txt, btn = report.bet_card(b, cfg)
                notify.send(txt, buttons=btn)
        if (cfg.get("execution") or {}).get("enabled") and (new or hot):
            sleep_minutes(cfg["execution"]["confirm_after_minutes"])
            now = now_utc()
            more, g = recheck(client, bets, cfg, now, hot, hist, stats)
            gone += g
            if more and cfg["notify"]["each_run_new_bets"]:
                notify.send(report.new_bets_message(more, bets, cfg, rechecked=True))
                for b in more:
                    txt, btn = report.bet_card(b, cfg)
                    notify.send(txt, buttons=btn)
            new += more
        stats["placed"] = len(new)
        if MOVED and cfg["notify"]["each_run_new_bets"]:
            notify.send(report.moved_message(MOVED, cfg))
        MOVED.clear()
        stats["not_executable"] = len(gone)
        if gone and cfg["notify"]["each_run_new_bets"]:
            notify.send(report.gone_message(gone))
    except Exception as e:
        stats["error"] = f"{e.__class__.__name__}: {str(e)[:200]}"
        traceback.print_exc()
    finally:
        close_tm()
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
