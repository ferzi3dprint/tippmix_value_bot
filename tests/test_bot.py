import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger  # noqa: E402
import valuefinder as vf  # noqa: E402
from common import load_config  # noqa: E402

CFG = load_config()


def test_devig_sums_to_one():
    for m in ("power", "multiplicative"):
        p = vf.devig([1.5, 4.2, 6.5], m)
        assert abs(sum(p) - 1) < 1e-9


def test_power_takes_more_margin_from_longshot():
    odds = [1.30, 4.00]
    mult, power = vf.devig(odds, "multiplicative"), vf.devig(odds, "power")
    assert power[0] > mult[0] and power[1] < mult[1]


def test_kelly_caps_and_rounding():
    s = vf.kelly_stake(0.6, 2.0, 100000, 100000, CFG)       # teljes Kelly 20% -> negyed 5% -> plafon 3%
    assert s == 3000
    assert vf.kelly_stake(0.5, 1.9, 100000, 100000, CFG) == 0  # negatív EV
    assert vf.kelly_stake(0.6, 2.0, 100000, 120, CFG) == 0     # nincs szabad keret


def test_min_ev_bands():
    assert vf.min_ev_for(1.4, CFG) == 0.02 and vf.min_ev_for(2.0, CFG) == 0.03 and vf.min_ev_for(2.4, CFG) == 0.04


def _book(prices, mid=101, oids=(101, 102, 103)):
    return {"bookmakerIsActive": True, "markets": {str(mid): {"marketActive": True, "outcomes": {
        str(o): {"players": {"0": {"active": True, "price": p}}} for o, p in zip(oids, prices)}}}}


META = {101: {"marketName": "Full Time Result", "playerProp": False,
              "outcomes": [{"outcomeId": 101, "outcomeName": "1"}, {"outcomeId": 102, "outcomeName": "X"}, {"outcomeId": 103, "outcomeName": "2"}]}}


def test_find_values_detects_value_and_respects_filters():
    fx = {"participant1Name": "A", "participant2Name": "B"}
    sharp = _book([1.80, 3.80, 4.80])               # fair ~ 0.54 / 0.25 / 0.20
    soft = _book([2.00, 3.40, 4.20])                # hazai 2.00 > fair ~1.85
    c = vf.find_values(fx, soft, sharp, META, CFG)
    assert len(c) == 1 and c[0]["outcome_id"] == 101 and c[0]["ev"] > 0.03
    assert vf.outcome_label(fx, c[0]) == "1X2: A győz"
    soft_high = _book([1.70, 3.40, 5.60])           # value az esélytelenen, de 5.60 > max_odds
    assert vf.find_values(fx, soft_high, sharp, META, CFG) == []


def test_find_values_skips_incomplete_or_suspicious_markets():
    fx = {}
    sharp = _book([1.80, 3.80, 4.80])
    soft = _book([2.00, 3.40, 4.20])
    soft["markets"]["101"]["outcomes"]["103"]["players"]["0"]["active"] = False
    assert vf.find_values(fx, soft, sharp, META, CFG) == []
    assert vf.find_values(fx, _book([2.0, 3.4, 4.2]), _book([1.6, 3.2, 4.0]), META, CFG) == []  # Pinnacle-árrés túl nagy


@pytest.mark.parametrize("res,profit", [("WIN", 900), ("LOSE", -1000), ("PUSH", 0), ("HALFWIN", 450), ("HALFLOSS", -500), ("VOID", 0)])
def test_settlement_payouts(res, profit):
    b = {"stake": 1000, "odds": 1.9}
    ledger.settle(b, res, dt.datetime.now(dt.timezone.utc))
    assert b["profit"] == pytest.approx(profit)


def test_full_mock_simulation(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import demo
    demo.main(4)
    bets = ledger.load()
    assert bets, "a szimuláció nem adott le fogadást"
    assert any(b["status"] == "settled" for b in bets)
    for b in bets:
        assert CFG["filters"]["min_odds"] <= b["signal_odds"] <= CFG["filters"]["max_odds"]
        assert b["signal_ev"] >= vf.min_ev_for(b["signal_odds"], CFG) and b["odds"] <= b["signal_odds"]
        assert b["stake"] <= CFG["staking"]["start_bankroll"] * 2 * CFG["staking"]["max_stake_pct"]
        assert len(ledger.active_on_fixture(bets, b["fixture_id"])) <= CFG["filters"]["max_bets_per_fixture"]
    # napi kimutatás és dashboard-adat elkészül
    import report
    from zoneinfo import ZoneInfo
    day = ledger.settle_day(next(b for b in bets if b["status"] == "settled"), ZoneInfo(CFG["timezone"]))
    txt = report.daily_message(bets, CFG, day, "https://x")
    assert "Ma lezárult" in txt and "Tőke" in txt
    from dashboard import build
    d = build.collect(CFG)
    live = [b for b in bets if b["status"] != "skipped"]
    assert d["summary"]["bets"] == len(live) and d["series"]
    assert d["summary"]["execution"]["checked"] > 0 and any(b.get("trigger") == "steam" for b in bets)


def test_dashboard_encryption_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from dashboard import build
    import base64, json
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    blob = build.encrypt({"a": 1}, "titok", b"0" * 16, 1000)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(blob["salt"]), iterations=1000).derive(b"titok")
    assert json.loads(AESGCM(key).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["ct"]), None)) == {"a": 1}


def test_quota_throttle(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FAKE_NOW", "2026-11-11T12:00:00+00:00")
    import bot
    from common import now_utc

    class C:
        def __init__(self, used):
            self.used = used

        def account(self):
            return {"request_limit": 1000, "request_count": self.used}
    ok, _ = bot.quota_ok(C(500), CFG, now_utc(), 4)       # ma: (500-10)/20 nap ≈ 24 kérés
    assert ok
    ok, _ = bot.quota_ok(C(530), CFG, now_utc(), 4)       # ma már 30 elment
    assert not ok


def test_steam_detection():
    import bot
    now = dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc)
    t = lambda m: (now - dt.timedelta(minutes=m)).isoformat()
    hist = {"f1": {"start": (now + dt.timedelta(hours=3)).isoformat(),
                   "m": {"101": [[t(90), [0.50, 0.28, 0.22]], [t(30), [0.51, 0.27, 0.22]], [t(0), [0.54, 0.25, 0.21]]]}}}
    mv = bot.steam_moves(hist, "f1", 101, now, CFG)
    assert list(mv) == [0] and mv[0] == pytest.approx(0.08)          # 0.50 -> 0.54 = +8%
    hist["f1"]["m"]["101"] = [[t(10), [0.50, 0.28, 0.22]], [t(0), [0.56, 0.24, 0.20]]]
    assert bot.steam_moves(hist, "f1", 101, now, CFG) == {}           # túl friss viszonyítási pont


class _Client:
    """Minimális kliens a végrehajthatóság teszteléséhez."""
    calls = 0

    def __init__(self, soft):
        self.soft = soft

    def markets(self):
        return list(META_ROWS)

    def odds(self, fid, books):
        return {"fixtureId": fid, "startTime": "2030-01-01T18:00:00+00:00",
                "bookmakerOdds": {"tippmixpro": _book(self.soft), "pinnacle": _book([1.80, 3.80, 4.80])}}


META_ROWS = [{"marketId": 101, "marketName": "Full Time Result", "playerProp": False,
              "outcomes": [{"outcomeId": 101, "outcomeName": "1"}, {"outcomeId": 102, "outcomeName": "X"}, {"outcomeId": 103, "outcomeName": "2"}]}]


@pytest.mark.parametrize("soft,status,odds", [([2.05, 3.4, 4.2], "ok", 2.00), ([1.92, 3.4, 4.2], "moved", 1.92),
                                              ([1.75, 3.4, 4.2], "gone", 2.00)])
def test_execution_check(tmp_path, monkeypatch, soft, status, odds):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import bot
    now = dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc)
    bet = {"fixture_id": "f1", "market_id": 101, "outcome_id": 101, "status": "open", "exec_status": "pending",
           "placed_at": (now - dt.timedelta(minutes=5)).isoformat(), "signal_odds": 2.00, "odds": 2.00,
           "fair_prob": 0.54, "ev": 0.08, "stake": 1000, "start": "2030-01-01T18:00:00+00:00", "home": "A", "away": "B", "label": "x"}
    bets = [bet]
    import copy
    paid = copy.deepcopy(CFG)
    paid["api"]["mode"] = "paid"
    _, gone = bot.recheck(_Client(soft), bets, paid, now, [], {}, {})
    assert bet["exec_status"] == status and bet["odds"] == odds
    assert (bet["status"] == "skipped") == (status == "gone") and bool(gone) == (status == "gone")
    s = ledger.summary(bets, CFG)
    assert s["execution"]["checked"] == 1 and s["bets"] == (0 if status == "gone" else 1)


def _hist(rows_by_outcome):
    return {"markets": {"101": {"outcomes": {str(oid): {"players": {"0": [{"createdAt": t, "price": p, "active": True} for t, p in rows]}}
                                             for oid, rows in rows_by_outcome.items()}}}}


def test_freefeed_snapshot_and_steam():
    import freefeed
    now = dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc)
    t = lambda m: (now - dt.timedelta(minutes=m)).isoformat()
    sharp = _hist({101: [(t(0), 1.85), (t(200), 2.00)],      # newest-first, mint az API példájában
                   102: [(t(200), 3.50)], 103: [(t(0), 4.60), (t(200), 4.00)]})
    snap = freefeed.snapshot(sharp)
    assert vf.market_prices(snap, 101, [101, 102, 103]) == [1.85, 3.50, 4.60]
    old = vf.market_prices(freefeed.snapshot(sharp, now - dt.timedelta(minutes=100)), 101, [101, 102, 103])
    assert old == [2.00, 3.50, 4.00]
    mv = freefeed.steam_from_history(sharp, 101, [101, 102, 103], now, CFG)
    assert 0 in mv and mv[0] > 0.04 and 2 not in mv


def test_free_mode_daily_cap_and_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import json
    import demo
    demo.main(5)
    bets = ledger.load()
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(CFG["timezone"])
    per_day = {}
    for b in bets:
        if b["status"] != "skipped":
            per_day[ledger.placed_day(b, tz)] = per_day.get(ledger.placed_day(b, tz), 0) + 1
    assert per_day and max(per_day.values()) <= CFG["free"]["max_bets_per_day"]
    sig = json.load(open(tmp_path / "signals.json"))
    assert len(sig) > len(bets) and any(x.get("clv") is not None for x in sig)
    runs = json.load(open(tmp_path / "runs.json"))
    paid = sum(r["api_calls"] for r in runs)
    assert paid / 5 < 250 / 30 + 3, f"túl sok fizetős kérés: {paid} 5 nap alatt"
