"""Dashboard build: a fogadási naplóból egyetlen JSON, AES-256-GCM-mel titkosítva, majd közzététel egy nyilvános
GitHub Pages repóba (csak index.html + titkosított adatfájl, egyetlen committal, force-push).
Ugyanaz a megoldás, mint a piaci dashboardnál: PBKDF2-SHA256 kulcsszármaztatás, a böngésző Web Cryptóval fejt vissza.

    python -m dashboard.build                 # titkosított build + közzététel (DASHBOARD_PASSWORD, DASHBOARD_TOKEN)
    python -m dashboard.build --plain DIR     # titkosítás nélküli helyi előnézet (demóhoz)
"""
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import ledger  # noqa: E402
from common import load_config, load_json, parse_time, save_json  # noqa: E402
from valuefinder import market_hu  # noqa: E402

from tippmixfeed import SPORT_HU as _SH
SPORT_HU = {**_SH, "Soccer": "Foci"}

TEMPLATE = os.path.join(HERE, "index.html")
DATA_NAME = "data.enc.json"


# --- adatgyűjtés -------------------------------------------------------------

def _group(bets, key):
    rows = defaultdict(lambda: {"n": 0, "counted": 0, "won": 0, "staked": 0.0, "profit": 0.0, "exp": 0.0, "clv": []})
    for b in bets:
        if b["status"] != "settled":
            continue
        r = rows[key(b)]
        r["n"] += 1
        r["staked"] += b["stake"]
        r["profit"] += b["profit"]
        r["exp"] += b["stake"] * b["ev"]
        if b.get("clv") is not None:
            r["clv"].append(b["clv"])
        if ledger.is_counted(b):
            r["counted"] += 1
            r["won"] += ledger.is_win(b)
    out = []
    for k, r in sorted(rows.items()):
        out.append({"key": k, "n": r["n"], "won": r["won"], "counted": r["counted"],
                    "hit": r["won"] / r["counted"] if r["counted"] else None,
                    "staked": round(r["staked"], 2), "profit": round(r["profit"], 2), "expected": round(r["exp"], 2),
                    "roi": r["profit"] / r["staked"] if r["staked"] else None,
                    "clv": sum(r["clv"]) / len(r["clv"]) if r["clv"] else None})
    return out


def collect(cfg, channel="pro"):
    """Egy csatorna (Tippmixpro / klasszikus Tippmix) teljes kimutatása; a klasszikus a "classic" kulcs alá kerül."""
    tz = ZoneInfo(cfg["timezone"])
    all_bets = ledger.load()
    bets = ledger.channel(all_bets, channel)
    start = cfg["staking"]["start_bankroll"]
    # napi sorozat: lezárás napja szerint
    days = defaultdict(lambda: {"placed": 0, "settled": 0, "won": 0, "lost": 0, "void": 0, "staked": 0.0, "profit": 0.0, "exp": 0.0})
    for b in bets:
        days[ledger.placed_day(b, tz).isoformat()]["placed"] += 1
        if b["status"] == "settled":
            d = days[ledger.settle_day(b, tz).isoformat()]
            d["settled"] += 1
            d["staked"] += b["stake"]
            d["profit"] += b["profit"]
            d["exp"] += b["stake"] * b["ev"]
            if not ledger.is_counted(b):
                d["void"] += 1
            elif ledger.is_win(b):
                d["won"] += 1
            else:
                d["lost"] += 1
    series, cp, ce = [], 0.0, 0.0
    for day in sorted(days):
        d = days[day]
        cp += d["profit"]
        ce += d["exp"]
        series.append({"date": day, **{k: round(v, 2) if isinstance(v, float) else v for k, v in d.items()},
                       "cum_profit": round(cp, 2), "cum_expected": round(ce, 2), "bankroll": round(start + cp, 2),
                       "roi": d["profit"] / d["staked"] if d["staked"] else None})
    slim = lambda b: {k: b.get(k) for k in ("id", "placed_at", "status", "sport", "tournament", "home", "away", "start", "label",
                                             "market", "odds", "sharp_odds", "fair_odds", "fair_prob", "ev", "stake", "result",
                                             "profit", "clv", "closing_fair_odds", "settled_at", "band", "link",
                                             "trigger", "steam", "signal_odds", "exec_odds", "exec_status", "tm_tip", "channel")}
    runs = load_json("runs.json", [])
    if channel != "pro":
        return {"summary": ledger.summary(bets, cfg), "series": series,
                "bands": _group(bets, lambda b: b["band"]),
                "sports": _group(bets, lambda b: SPORT_HU.get(b.get("sport"), b.get("sport") or "?")),
                "markets": _group(bets, lambda b: market_hu(b.get("market") or "?")),
                "triggers": _group(bets, lambda b: "⚡ steam" if b.get("trigger") == "steam" else "normál"),
                "skipped": [slim(b) for b in sorted((b for b in bets if b["status"] == "skipped"), key=lambda b: b["placed_at"], reverse=True)[:100]],
                "open": [slim(b) for b in sorted((b for b in bets if b["status"] == "open"), key=lambda b: b["start"])],
                "settled": [slim(b) for b in sorted((b for b in bets if b["status"] == "settled"), key=lambda b: b["settled_at"], reverse=True)],
                "signals": {"n": 0}, "signal_rows": []}
    return {
        "classic": collect(cfg, "classic") if (cfg.get("classic") or {}).get("enabled") else None,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "timezone": cfg["timezone"],
        "summary": ledger.summary(bets, cfg),
        "series": series,
        "bands": _group(bets, lambda b: b["band"]),
        "sports": _group(bets, lambda b: SPORT_HU.get(b.get("sport"), b.get("sport") or "?")),
        "markets": _group(bets, lambda b: market_hu(b.get("market") or "?")),
        "triggers": _group(bets, lambda b: "⚡ steam" if b.get("trigger") == "steam" else "normál"),
        "skipped": [slim(b) for b in sorted((b for b in bets if b["status"] == "skipped"), key=lambda b: b["placed_at"], reverse=True)[:100]],
        "open": [slim(b) for b in sorted((b for b in bets if b["status"] == "open"), key=lambda b: b["start"])],
        "settled": [slim(b) for b in sorted((b for b in bets if b["status"] == "settled"), key=lambda b: b["settled_at"], reverse=True)],
        "signals": ledger.signal_stats(load_json("signals.json", [])),
        "signal_rows": sorted(load_json("signals.json", []), key=lambda x: x["first_at"], reverse=True)[:150],
        "last_run": runs[-1] if runs else None,
        "runs_24h": len([r for r in runs if parse_time(r["time"]) > dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)]),
        "quota": load_json("quota.json", None),
        "settings": {"steam": cfg.get("steam"), "execution": cfg.get("execution"), "min_odds": cfg["filters"]["min_odds"], "max_odds": cfg["filters"]["max_odds"],
                     "ev_bands": cfg["filters"]["ev_bands"], "kelly": cfg["staking"]["kelly_fraction"],
                     "max_stake_pct": cfg["staking"]["max_stake_pct"], "sports": (["minden sportág"] if cfg["scan"]["sports"] in ("all", ["all"]) else cfg["scan"]["sports"]),
                     "mode": cfg["api"].get("mode", "paid"), "max_bets_per_day": (cfg.get("free") or {}).get("max_bets_per_day"),
                     "soft": cfg["api"]["soft_book"], "sharp": cfg["api"]["sharp_book"], "devig": cfg["scan"]["devig_method"]},
        "demo": os.getenv("MOCK_API") == "1" or os.getenv("DATA_DIR", "data") != "data",
    }


# --- titkosítás --------------------------------------------------------------

def kdf_params(cfg):
    from_file = load_json("dashboard_kdf.json", {})
    iters = int(cfg["dashboard"].get("pbkdf2_iterations", 600000))
    if from_file.get("iterations") == iters and from_file.get("salt"):
        return base64.b64decode(from_file["salt"]), iters
    salt = os.urandom(16)
    save_json("dashboard_kdf.json", {"salt": base64.b64encode(salt).decode(), "iterations": iters})
    return salt, iters


def encrypt(obj, password, salt, iterations):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations).derive(password.encode())
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False, default=str).encode(), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iterations": iterations, "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def build(cfg, out_dir, password=None, plain=False):
    os.makedirs(out_dir, exist_ok=True)
    data = collect(cfg)
    html = open(TEMPLATE, encoding="utf-8").read()
    if plain:
        html = html.replace("/*PLAIN_DATA*/", "window.PLAIN_DATA = " + json.dumps(data, ensure_ascii=False, default=str) + ";")
    else:
        salt, iters = kdf_params(cfg)
        json.dump(encrypt(data, password, salt, iters), open(os.path.join(out_dir, DATA_NAME), "w", encoding="utf-8"))
    open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8").write(html)
    open(os.path.join(out_dir, ".nojekyll"), "w").close()
    return out_dir


def publish(cfg, out_dir, token, plain):
    """Force-push egyetlen committal. Alapból ugyanennek a repónak a gh-pages ágára (GITHUB_TOKEN-nel);
    ha a configban külön repó van megadva, oda (ehhez DASHBOARD_TOKEN kell)."""
    dcfg = cfg["dashboard"]
    repo = dcfg.get("repo") or os.getenv("GITHUB_REPOSITORY")
    branch = dcfg.get("branch") or "gh-pages"
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def git(*args):
        r = subprocess.run(["git", "-c", f"http.extraheader=AUTHORIZATION: basic {auth}", *args], cwd=out_dir,
                           capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"git {args[0]}: {(r.stderr or r.stdout).replace(token, '***').replace(auth, '***')[:300]}")

    git("init", "-q", "-b", branch)
    git("add", "index.html", ".nojekyll", *([] if plain else [DATA_NAME]))
    git("-c", "user.name=dashboard-bot", "-c", "user.email=dashboard-bot@users.noreply.github.com", "commit", "-q", "-m", "Dashboard frissítés")
    git("push", "-q", "--force", f"https://github.com/{repo}.git", f"HEAD:{branch}")


def main():
    cfg = load_config()
    if "--plain" in sys.argv:
        out = sys.argv[sys.argv.index("--plain") + 1]
        print("Előnézet:", build(cfg, out, plain=True))
        return
    pw = os.getenv("DASHBOARD_PASSWORD")
    plain = not pw    # jelszó nélkül nyílt oldal (nyilvános repónál a napló amúgy is látható)
    out = tempfile.mkdtemp(prefix="dash_")
    build(cfg, out, pw, plain=plain)
    token = os.getenv("DASHBOARD_TOKEN") if cfg["dashboard"].get("repo") else (os.getenv("DASHBOARD_TOKEN") or os.getenv("GITHUB_TOKEN"))
    if not token or os.getenv("DRY_RUN") == "1":
        print("[dashboard] helyi build, közzététel nélkül:", out)
        return
    try:
        publish(cfg, out, token, plain)
        print("[dashboard] közzétéve:", cfg["dashboard"].get("url"), "(jelszó nélkül)" if plain else "(jelszóval)")
    except Exception as e:
        print("[dashboard] közzététel sikertelen:", e)


if __name__ == "__main__":
    main()
