"""Odds-emelések (boost) a Tippmixpro-n: a JS-ből a kapcsolódó lekérdezések, a WAMP-ajánlatok mezői, és a 'boost'-os rekordok."""
import asyncio, collections, datetime as dt, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed
from playwright.async_api import async_playwright

async def js_scan():
    out = set()
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        pg = await (await b.new_context(locale="hu-HU")).new_page()
        scripts = []
        pg.on("response", lambda r: scripts.append(r) if r.request.resource_type == "script" and "tippmixpro" in r.url else None)
        await pg.goto("https://www.tippmixpro.hu/hu/fogadas/i/labdarugas/1", wait_until="domcontentloaded", timeout=45000)
        await pg.wait_for_timeout(9000)
        for r in scripts:
            try:
                js = await r.text()
            except Exception:
                continue
            for m in re.finditer(r"[^\"'`\s]{0,60}(boost|Boost|BOOST|szorzonovel|oddsnovel|super-?odds|enhanced|promo-odds|special-odds)[^\"'`\s]{0,60}", js):
                out.add(m.group(0)[:120])
        await b.close()
    return sorted(out)

pats = asyncio.run(js_scan())
print("JS-minták (boost):", pats[:80])
now = dt.datetime.now(dt.timezone.utc)
with tippmixfeed.Session() as s:
    ms = s.today_matches(now, sports=("Labdarúgás", "Kosárlabda", "Tenisz", "Jégkorong"), days_ahead=1)
    keys, boosted, types = collections.Counter(), [], collections.Counter()
    for m in ms[:60]:
        recs = s.dump(f"/sports/2901/hu/{m['id']}/match-odds/market-group/Main_BannerMatrix") or []
        for r in recs:
            types[r.get("_type")] += 1
            if r.get("_type") == "BETTING_OFFER":
                keys.update(k for k, v in r.items() if v not in (None, False, "", 0))
            if re.search(r"boost|enhanc|super", json.dumps(r), re.I) and len(boosted) < 5:
                boosted.append(json.dumps(r, ensure_ascii=False)[:500])
    print("Rekordtípusok:", dict(types))
    print("BETTING_OFFER nem üres mezői:", dict(keys))
    print("Boost-gyanús rekordok:", boosted)
    for t in [p for p in pats if p.startswith("/sports") or "aggregator" in p][:10]:
        print("próba-téma:", t)
    for topic in ("/sports/2901/hu/boosted-odds", "/sports/2901/hu/odds-boost", "/sports/2901/hu/boosted-matches-aggregator-main/0/0/50",
                  "/sports/2901/hu/super-odds", "/sports/2901/hu/promotions"):
        r = s.dump(topic)
        print(topic, "->", None if r is None else dict(collections.Counter(x.get("_type") for x in r)))
