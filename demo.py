"""Demó: N nap gyorsított szimulációja szintetikus adatokkal (MOCK_API), a valódi bot-kóddal.

    python demo.py 21            -> demo_data/ (bets.json stb.), majd: DATA_DIR=demo_data python -m dashboard.build --plain demo_site

A szintetikus piac csak a működést mutatja be; a demó profitja semmit nem mond a valódi Tippmixről.
"""
import datetime as dt
import os
import shutil
import sys
from zoneinfo import ZoneInfo

os.environ.update({"MOCK_API": "1", "NOTIFY_SILENT": "1", "DATA_DIR": os.getenv("DATA_DIR", "demo_data")})

import bot  # noqa: E402
from common import load_config  # noqa: E402


def main(days=21):
    cfg = load_config()
    shutil.rmtree(os.environ["DATA_DIR"], ignore_errors=True)
    tz = ZoneInfo(cfg["timezone"])
    start = dt.datetime.now(tz).replace(hour=8, minute=0, second=0, microsecond=0) - dt.timedelta(days=days)
    t = start
    while t < start + dt.timedelta(days=days):
        if 8 <= t.hour <= 23:
            os.environ["FAKE_NOW"] = t.isoformat()
            bot.run(cfg)
        t += dt.timedelta(minutes=30)
    print("Demó kész:", os.environ["DATA_DIR"])


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 21)
