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
        assert CFG["filters"]["min_odds"] <= b["odds"] <= CFG["filters"]["max_odds"]
        assert b["ev"] >= vf.min_ev_for(b["odds"], CFG)
        assert b["stake"] <= CFG["staking"]["start_bankroll"] * 2 * CFG["staking"]["max_stake_pct"]
        assert sum(1 for x in bets if x["fixture_id"] == b["fixture_id"]) <= CFG["filters"]["max_bets_per_fixture"]
    # napi kimutatás és dashboard-adat elkészül
    import report
    from zoneinfo import ZoneInfo
    day = ledger.settle_day(next(b for b in bets if b["status"] == "settled"), ZoneInfo(CFG["timezone"]))
    txt = report.daily_message(bets, CFG, day, "https://x")
    assert "Ma lezárult" in txt and "Tőke" in txt
    from dashboard import build
    d = build.collect(CFG)
    assert d["summary"]["bets"] == len(bets) and d["series"]


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
    ok, _ = bot.quota_ok(C(500), CFG, now_utc(), 4)       # ma: (500-50)/20 nap ≈ 22 kérés
    assert ok
    ok, _ = bot.quota_ok(C(520), CFG, now_utc(), 4)       # ma már 20 elment, +4 > 22
    assert not ok
