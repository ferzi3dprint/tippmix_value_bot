"""A szimulált fogadások naplója (data/bets.json) és a tőke számítása."""
import datetime as dt

from common import load_json, save_json, parse_time

FILE = "bets.json"
FINAL = {"WIN", "LOSE", "HALFWIN", "HALFLOSS", "PUSH", "CANCELLED"}


def load():
    return load_json(FILE, [])


def save(bets):
    save_json(FILE, bets)


def bankroll(bets, cfg):
    return cfg["staking"]["start_bankroll"] + sum(b.get("profit") or 0 for b in bets if b["status"] == "settled")


def open_stake(bets):
    return sum(b["stake"] for b in bets if b["status"] == "open")


def payout(result, stake, odds):
    return {
        "WIN": stake * odds,
        "HALFWIN": stake / 2 * odds + stake / 2,
        "PUSH": stake,
        "CANCELLED": stake,
        "VOID": stake,
        "HALFLOSS": stake / 2,
        "LOSE": 0.0,
    }[result]


def settle(bet, result, when):
    bet["status"] = "settled"
    bet["result"] = result
    bet["payout"] = round(payout(result, bet["stake"], bet["odds"]), 2)
    bet["profit"] = round(bet["payout"] - bet["stake"], 2)
    bet["settled_at"] = when.isoformat()


def bets_on_fixture(bets, fixture_id):
    return [b for b in bets if b["fixture_id"] == fixture_id]


def active_on_fixture(bets, fixture_id):
    """A meccs élő (nem kimaradt) fogadásai – a meccsenkénti korláthoz."""
    return [b for b in bets if b["fixture_id"] == fixture_id and b["status"] != "skipped"]


def already_bet(bets, fixture_id, market_id, outcome_id):
    return any(b["fixture_id"] == fixture_id and b["market_id"] == market_id and b["outcome_id"] == outcome_id for b in bets)


def is_win(b):
    return b.get("result") in ("WIN", "HALFWIN")


def is_counted(b):
    """Találati arányba számít-e (a visszajáró téteket kihagyjuk)."""
    return b["status"] == "settled" and b.get("result") in ("WIN", "LOSE", "HALFWIN", "HALFLOSS")


def start_time(b):
    return parse_time(b["start"])


def settle_day(b, tz):
    return parse_time(b["settled_at"]).astimezone(tz).date() if b.get("settled_at") else None


def placed_day(b, tz):
    return parse_time(b["placed_at"]).astimezone(tz).date()


def execution_stats(bets):
    """Végrehajthatóság: a jelzések közül hány volt még megtehető pár perccel később, és mennyit csúszott az odds."""
    chk = [b for b in bets if b.get("exec_status") in ("ok", "moved", "gone")]
    if not chk:
        return {"checked": 0}
    done = [b for b in chk if b["exec_status"] != "gone"]
    slip = [b["odds"] / b["signal_odds"] - 1 for b in done if b.get("signal_odds")]
    return {"checked": len(chk), "ok": sum(b["exec_status"] == "ok" for b in chk),
            "moved": sum(b["exec_status"] == "moved" for b in chk), "gone": sum(b["exec_status"] == "gone" for b in chk),
            "rate": len(done) / len(chk), "avg_slippage": sum(slip) / len(slip) if slip else 0.0}


def summary(bets, cfg):
    """Összesített mutatók. A „nem volt megtehető” (skipped) jelzések nem számítanak bele, csak a végrehajthatóságba."""
    allb = bets
    bets = [b for b in bets if b["status"] != "skipped"]
    st = [b for b in bets if b["status"] == "settled"]
    counted = [b for b in st if is_counted(b)]
    staked = sum(b["stake"] for b in st)
    profit = sum(b["profit"] for b in st)
    exp = sum(b["stake"] * b["ev"] for b in st)
    clv = [b["clv"] for b in bets if b.get("clv") is not None]
    br = bankroll(bets, cfg)
    return {
        "bets": len(bets), "settled": len(st), "open": sum(1 for b in bets if b["status"] == "open"),
        "won": sum(1 for b in counted if is_win(b)), "lost": sum(1 for b in counted if not is_win(b)),
        "void": len(st) - len(counted),
        "hit_rate": (sum(1 for b in counted if is_win(b)) / len(counted)) if counted else None,
        "expected_hit_rate": (sum(b["fair_prob"] for b in counted) / len(counted)) if counted else None,
        "avg_odds": (sum(b["odds"] for b in st) / len(st)) if st else None,
        "staked": round(staked, 2), "profit": round(profit, 2), "expected_profit": round(exp, 2),
        "roi": (profit / staked) if staked else None, "avg_ev": (sum(b["ev"] for b in bets) / len(bets)) if bets else None,
        "avg_clv": (sum(clv) / len(clv)) if clv else None, "clv_positive": (sum(1 for c in clv if c > 0) / len(clv)) if clv else None,
        "clv_n": len(clv),
        "bankroll": round(br, 2), "start_bankroll": cfg["staking"]["start_bankroll"],
        "open_stake": round(open_stake(bets), 2),
        "steam_bets": sum(1 for b in bets if b.get("trigger") == "steam"),
        "execution": execution_stats(allb),
    }
