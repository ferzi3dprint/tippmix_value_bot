"""Telegram-próba: egy ajánlás-kártya formájú üzenet egy valódi mai meccs Tippmixpro-linkjével (a gomb tesztelésére)."""
import datetime as dt

import notify
import tippmixfeed

now = dt.datetime.now(dt.timezone.utc)
with tippmixfeed.Session() as s:
    ms = sorted(s.today_matches(now, sports=("Labdarúgás",), days_ahead=1), key=lambda m: m["start"])
m = next((x for x in ms if x["start"] > now + dt.timedelta(hours=1)), ms[0] if ms else None)
if m:
    url = tippmixfeed.match_url(m)
    txt = (f"🧪 PRÓBA – a meccsgomb tesztje (nem ajánlás)\n\n⚽ {m['home']} – {m['away']}\n   {m['tournament']}\n\n"
           f"Nyomd meg a gombot: ha a Tippmixpro-n ennek a meccsnek az oldala nyílik meg, a link jó.")
    ok = notify.send(txt, buttons=[("🟢 Meccs megnyitása a Tippmixpro-n", url)])
    print("Elküldve:" if ok else "NEM ment el:", url)
    raise SystemExit(0 if ok else 1)
print("Nincs mai meccs a listán")
