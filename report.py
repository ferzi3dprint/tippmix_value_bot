"""Telegram-szövegek: új ajánlások (futásonként) és napi kimutatás."""
from zoneinfo import ZoneInfo

import ledger
from common import load_json, huf, signed_huf, pct, parse_time, odds_fmt as o2
from valuefinder import odds_band

ICON = {"WIN": "✅", "HALFWIN": "✅½", "LOSE": "❌", "HALFLOSS": "❌½", "PUSH": "↩️", "CANCELLED": "↩️", "VOID": "↩️"}


def _match(b):
    return f"{b['home']} – {b['away']}"


SPORT_ICON = {"Soccer": "⚽", "Tennis": "🎾", "Basketball": "🏀", "Ice Hockey": "🏒"}


TM_URL = None      # a meccsoldal pontos címformátuma még nincs meg – addig a főoldal
TM_HOME = "https://www.tippmixpro.hu/hu/fogadas"


def bet_card(b, cfg):
    """Egy ajánlás „kész szelvényként”: mit, mekkora téttel, és meddig éri meg. + gomb a Tippmixpro meccsoldalára."""
    tz = ZoneInfo(cfg["timezone"])
    t = parse_time(b["start"]).astimezone(tz).strftime("%m.%d %H:%M")
    lines = [f"{SPORT_ICON.get(b['sport'], '🏟️')} {_match(b)}", f"   {b['tournament']} · kezdés {t}"]
    if b.get("trigger") == "steam":
        lines.append(f"⚡ STEAM: a Pinnacle-ár mozog (+{(b.get('steam') or 0) * 100:.0f}%) – siess")
    lines += [f"🎯 Tipp: {b['label']}"] + ([f"🔎 A Tippmixpro-n: {b['tm_tip']}"] if b.get("tm_tip") else []) + [
              f"💵 Tét: {huf(b['stake'])}",
              f"📈 Tippmix-odds most: {o2(b['odds'])}  (fair: {o2(b['fair_odds'])}, {b.get('ref') or 'Pinnacle'}, EV {pct(b['ev'])})"]
    if b.get("min_odds"):
        lines.append(f"✅ Megéri legalább {o2(b['min_odds'])}-es oddsig. Ha ennél kisebb, hagyd ki.")
    if b.get("tm_url"):
        buttons = [("🟢 Meccs megnyitása a Tippmixpro-n", b["tm_url"])]
    else:
        buttons = [("🟢 Tippmixpro megnyitása", TM_HOME)]
    return "\n".join(lines), buttons


def new_bets_message(new, bets, cfg, rechecked=False):
    steam = sum(1 for b in new if b.get("trigger") == "steam")
    head = f"🎯 {len(new)} új value-ajánlás" + (" (steam-újraellenőrzésből)" if rechecked else "")
    lines = [head + (f" – ebből ⚡ {steam} gyors" if steam else "")]
    s = ledger.summary(bets, cfg)
    lines.append(f"💰 Szimulált tőke: {huf(s['bankroll'])} | nyitott: {s['open']} db, {huf(s['open_stake'])}")
    if (cfg.get("execution") or {}).get("enabled") and not rechecked:
        lines.append(f"⏱️ {cfg['execution']['confirm_after_minutes']} perc múlva ellenőrzöm, megtehető-e még.")
    return "\n".join(lines)


def gone_message(gone):
    L = [f"⌛ {len(gone)} ajánlás már nem volt megtehető (az odds esett vagy a piac lezárt) – kimarad az eredményből:"]
    for b in gone:
        now = o2(b["exec_odds"]) if b.get("exec_odds") else "nincs"
        L.append(f"• {_match(b)} · {b['label']}: {o2(b['signal_odds'])} → {now}")
    return "\n".join(L)


def moved_message(moved, cfg):
    L = ["🔄 Módosult ajánlás – a Tippmix lejjebb vitte az oddsot, de a friss piaci ár alapján még megéri:"]
    for b in moved:
        L.append(f"• {_match(b)}\n  🎯 {b.get('tm_tip') or b['label']}\n"
                 f"  odds {o2(b['signal_odds'])} → {o2(b['odds'])} · új fair {o2(b['fair_odds'])} · EV {b['ev']*100:+.1f}%\n"
                 f"  ✅ Most már legalább {o2(b['min_odds'])}-es oddsig éri meg")
    return "\n".join(L)


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
    today_placed = [b for b in bets if ledger.placed_day(b, tz) == day and b["status"] != "skipped"]
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
    ex = s["execution"]
    if ex.get("checked"):
        L.append(f"Végrehajthatóság: a jelzések {ex['rate'] * 100:.0f}%-a volt megtehető {ex['checked']}-ből "
                 f"(változatlan {ex['ok']}, rosszabb oddsszal {ex['moved']}, elesett {ex['gone']}); átlagos csúszás {pct(ex['avg_slippage'])}")
    if s["avg_clv"] is not None:
        L.append(f"CLV (záró Pinnacle-árhoz mérve): átlag {pct(s['avg_clv'])}, a fogadások {s['clv_positive'] * 100:.0f}%-a verte a záró árat ({s['clv_n']} db)")
    ss = ledger.signal_stats(load_json("signals.json", []))
    if ss["measured"]:
        L.append(f"📡 Összes value-jelzés (fogadás nélkül is): {ss['n']} db, mért CLV {ss['measured']} db: átlag {pct(ss['avg_clv'])}, "
                 f"{ss['clv_positive'] * 100:.0f}% verte a záró árat")
    steam_st = [b for b in bets if b["status"] == "settled" and b.get("trigger") == "steam"]
    norm_st = [b for b in bets if b["status"] == "settled" and b.get("trigger") != "steam"]
    if steam_st:
        roi = lambda xs: pct(sum(b["profit"] for b in xs) / sum(b["stake"] for b in xs)) if xs else "–"
        clv = lambda xs: (lambda c: pct(sum(c) / len(c)) if c else "–")([b["clv"] for b in xs if b.get("clv") is not None])
        L.append(f"⚡ Steam: {len(steam_st)} db, ROI {roi(steam_st)}, CLV {clv(steam_st)} | normál: {len(norm_st)} db, ROI {roi(norm_st)}, CLV {clv(norm_st)}")
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
