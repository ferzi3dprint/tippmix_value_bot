"""Folyamatos futtatás egy GitHub Actions jobon belül: INTERVAL_MINUTES percenként bot.run() + napló-commit + dashboard,
LOOP_MINUTES percig. Egy hibás kör nem állítja le a hurkot."""
import datetime as dt
import os
import subprocess
import sys
import time
import traceback

import bot
from common import load_config


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[loop] '{cmd[:60]}…' hiba: {(r.stderr or r.stdout)[-300:]}")
    return r.returncode == 0


def commit_data():
    sh("git add data")
    if subprocess.run("git diff --cached --quiet", shell=True).returncode == 0:
        return
    sh('git -c user.name=tippmix-bot -c user.email=41898282+github-actions[bot]@users.noreply.github.com '
       f'commit -q -m "Napló: {dt.datetime.utcnow():%Y-%m-%dT%H:%M}"')
    for i in range(5):
        if sh("git pull -q --rebase -X theirs origin main") and sh("git push -q origin HEAD:main"):
            return
        sh("git rebase --abort")
        time.sleep(5 * (i + 1))


def main():
    total = float(os.getenv("LOOP_MINUTES", "325")) * 60
    every = float(os.getenv("INTERVAL_MINUTES", "20")) * 60
    t_end = time.monotonic() + total
    n = 0
    while True:
        start = time.monotonic()
        n += 1
        print(f"\n===== {n}. kör {dt.datetime.utcnow():%H:%M} UTC =====", flush=True)
        st = {}
        try:
            st = bot.run(load_config()) or {}
        except Exception:
            traceback.print_exc()
        changed = not st.get("fast") or st.get("placed") or st.get("settled") or st.get("not_executable") or st.get("candidates")
        if changed:             # gyors körben csak akkor commitol/frissít dashboardot, ha történt valami
            commit_data()
            sh(f"{sys.executable} -m dashboard.build")
            commit_data()       # első titkosított buildnél a salt-fájl
        nxt = start + every
        if nxt + 60 > t_end:
            break
        time.sleep(max(0, nxt - time.monotonic()))
    commit_data()
    print(f"[loop] vége, {n} kör.")


if __name__ == "__main__":
    main()
