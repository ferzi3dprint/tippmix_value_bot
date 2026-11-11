"""Telegram-szövegek: új ajánlások (futásonként) és napi kimutatás."""
from zoneinfo import ZoneInfo

import ledger
from common import huf, signed_huf, pct, parse_time, odds_fmt as o2
from valuefinder import odds_band

ICON = {"WIN": "✅", "HALFWIN": "✅½", "LOSE": "❌", "HALFLOSS": "❌½", "PUSH": "↩️", "CANCELLED": "↩️", "VOID": "↩️"}


def _match(b):
    return f"{b['home']} – {b['away']}"


def new_bets_message(new, bets, cfg):
    tz = ZoneInfo(cfg["timezone"])
    lines = [f"🎯 {len(new)} új value-ajánlás (szimulált fogadás)", ""]
    for b in new:
        t = parse_time(b["start"]).astimezone(tz).strftime("%m.%d %H:%M")
        lines += [f"⚽ {_match(b)} ({b['tournament']}, {t})" if b["sport"] == "Soccer" else
                  f"🎾 {_match(b)} ({b['tournament']}, {t})" if b["sport"] == "Tennis" else f"🏟️ {_match(b)} ({b['tournament']}, {t})",
                  f"   {b['label']}",
                  f"   Tippmix {o2(b['odds'])} | fair {o2(b['fair_odds'])} (Pinnacle {o2(b['sharp_odds'])}) | EV {pct(b['ev'])}",
                  f"   Tét: {huf(b['stake'])}", ""]
    s = ledger.summary(bets, cfg)
    lines.append(f"💰 Szimulált tőke: {huf(s['bankroll'])} | nyitott: {s['open']} db, {huf(s['open_stake'])}")
    return "\n".join(lines)


def _band_rows(bets):
    rows = {}
    for b in bets:
        if b["status"] != "settled":
            continue
        r = rows.setdefault(odds_band(b["odds"]), {"n": 0, "won": 0, "counted": 0, "staked": 0, "profit": 0})
        r["n"] += 1
        r["staked"] += b["stake"]
        r["profit"] += b["profit"]
        if ledger.is_counted(b):
            r["counted"] += 1
            r["won"] += ledger.is_win(b)
    return dict(sorted(rows.items()))


def daily_message(bets, cfg, day, dashboard_url=None):
    tz = ZoneInfo(cfg["timezone"])
    today_settled = [b for b in bets if b["status"] == "settled" and ledger.settle_day(b, tz) == day]
    today_placed = [b for b in bets if ledger.placed_day(b, tz) == day]
    counted = [b for b in today_settled if ledger.is_counted(b)]
    won = sum(1 for b in counted if ledger.is_win(b))
    staked = sum(b["stake"] for b in today_settled)
    profit = sum(b["profit"] for b in today_settled)
    s = ledger.summary(bets, cfg)

    L = [f"📊 Tippmix value-bot – napi kimutatás ({day.isoformat()})", "(szimuláció, valódi pénz nélkül)", ""]
    if today_settled:
        exp_hit = sum(b["fair_prob"] for b in counted) / len(counted) if counted else None
        L.append(f"Ma lezárult: {len(today_settled)} fogadás – ✅ {won} bejött, ❌ {len(counted) - won} nem"
                 + (f", ↩️ {len(today_settled) - len(counted)} visszajár" if len(today_settled) > len(counted) else ""))
        if counted:
            L.append(f"Találati arány: {won / len(counted) * 100:.0f}% (a fair esélyek alapján várt: {exp_hit * 100:.0f}%)")
        L.append(f"Mai eredmény: {signed_huf(profit)} (feltett tét: {huf(staked)}"
                 + (f", ROI {pct(profit / staked)})" if staked else ")"))
    else:
        L.append("Ma nem zárult le fogadás.")
    if today_placed:
        L.append(f"Ma ajánlott új fogadás: {len(today_placed)} db, átlag odds {o2(sum(b['odds'] for b in today_placed) / len(today_placed))}, "
                 f"átlag EV {pct(sum(b['ev'] for b in today_placed) / len(today_placed))}")
    L.append(f"Nyitott fogadások: {s['open']} db ({huf(s['open_stake'])})")
    L.append("")
    L.append("📈 Indulás óta")
    if s["settled"]:
        L.append(f"Lezárt: {s['settled']} | bejött {s['won']}/{s['won'] + s['lost']}"
                 + (f" ({s['hit_rate'] * 100:.0f}%, várt {s['expected_hit_rate'] * 100:.0f}%)" if s["hit_rate"] is not None else ""))
        L.append(f"Profit: {signed_huf(s['profit'])} | ROI {pct(s['roi'])} | várt profit: {signed_huf(s['expected_profit'])}")
    L.append(f"Tőke: {huf(s['start_bankroll'])} → {huf(s['bankroll'])}")
    if s["avg_clv"] is not None:
        L.append(f"CLV (záró Pinnacle-árhoz mérve): átlag {pct(s['avg_clv'])}, a fogadások {s['clv_positive'] * 100:.0f}%-a verte a záró árat ({s['clv_n']} db)")
    bands = _band_rows(bets)
    if bands:
        L += ["", "🎚️ Odds-sávonként (lezárt)"]
        for band, r in bands.items():
            hit = f"{r['won'] / r['counted'] * 100:.0f}%" if r["counted"] else "–"
            L.append(f"{band}: {r['n']} db, találat {hit}, ROI {pct(r['profit'] / r['staked']) if r['staked'] else '–'}")
    if today_settled:
        L += ["", "🧾 Ma lezárult"]
        for b in sorted(today_settled, key=lambda b: b["start"])[:20]:
            L.append(f"{ICON.get(b['result'], '•')} {_match(b)} · {b['label']} @{o2(b['odds'])} · {huf(b['stake'])} → {signed_huf(b['profit'])}")
        if len(today_settled) > 20:
            L.append(f"… és még {len(today_settled) - 20}")
    if s["settled"] < 300:
        L += ["", "ℹ️ Kevés a lezárt fogadás: az eredmény még nagyrészt szerencse. A CLV a gyorsabb, megbízhatóbb jelző."]
    if dashboard_url:
        L += ["", f"🔗 {dashboard_url}"]
    return "\n".join(L)
