"""Közös segédfüggvények: config, idő, JSON-tárolás."""
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

import yaml

UTC = dt.timezone.utc


def load_config(path="config.yaml"):
    return yaml.safe_load(open(path, encoding="utf-8"))


def data_dir():
    return os.getenv("DATA_DIR", "data")


def now_utc():
    """A futás ideje. FAKE_NOW (ISO) csak a demóhoz/tesztekhez."""
    fake = os.getenv("FAKE_NOW")
    return dt.datetime.fromisoformat(fake).astimezone(UTC) if fake else dt.datetime.now(UTC)


def sleep_minutes(m):
    """Várakozás a futáson belül. A demóban/tesztben (FAKE_NOW) csak az órát tekeri előre."""
    fake = os.getenv("FAKE_NOW")
    if fake:
        os.environ["FAKE_NOW"] = (dt.datetime.fromisoformat(fake) + dt.timedelta(minutes=m)).isoformat()
        return
    import time
    time.sleep(m * 60)


def to_local(t, cfg):
    return t.astimezone(ZoneInfo(cfg["timezone"]))


def parse_time(s):
    if s is None:
        return None
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.replace(tzinfo=UTC)
    s = str(s).replace("Z", "+00:00")
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def load_json(name, default):
    path = os.path.join(data_dir(), name)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name, obj):
    path = os.path.join(data_dir(), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def huf(x):
    """1234.5 -> '1 235 Ft'"""
    return f"{round(x):,}".replace(",", " ") + " Ft"


def signed_huf(x):
    return ("+" if x > 0 else "") + huf(x).replace("-", "−")


def pct(x, d=1):
    return ("+" if x > 0 else "") + f"{x * 100:.{d}f}%".replace("-", "−").replace(".", ",")


def odds_fmt(x):
    return f"{x:.2f}".replace(".", ",")
