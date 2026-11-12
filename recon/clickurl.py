"""Meccsoldal URL-formátuma: a focioldalon rákattint néhány meccsre, és kiírja, milyen URL-re navigál."""
import asyncio, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed
from playwright.async_api import async_playwright

now = dt.datetime.now(dt.timezone.utc)
with tippmixfeed.Session() as s:
    ms = [m for m in s.today_matches(now, sports=("Labdarúgás",), days_ahead=1)][:12]
print("Minta meccsek:", [(m["id"], m["home"]) for m in ms[:5]])

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        pg = await (await b.new_context(locale="hu-HU", viewport={"width": 1366, "height": 900})).new_page()
        await pg.goto("https://www.tippmixpro.hu/hu/fogadas/i/elo-naptar", wait_until="domcontentloaded", timeout=45000)
        await pg.wait_for_timeout(9000)
        for sel in ["#onetrust-accept-btn-handler", "button:has-text('Elfogad')", "button:has-text('Összes elfogadása')"]:
            try:
                await pg.click(sel, timeout=1500); break
            except Exception:
                pass
        for m in ms:
            try:
                loc = pg.get_by_text(m["home"], exact=False).first
                await loc.click(timeout=3000)
                await pg.wait_for_timeout(4000)
                print("KATTINTÁS:", m["home"], "->", pg.url, "| id:", m["id"])
                if str(m["id"]) in pg.url or "esemeny" in pg.url or "event" in pg.url:
                    break
                await pg.go_back(); await pg.wait_for_timeout(3000)
            except Exception as e:
                print("nem kattintható:", m["home"], str(e)[:80])
        # direkt próbák
        mid = ms[0]["id"] if ms else ""
        for u in (f"https://www.tippmixpro.hu/hu/fogadas/i/esemeny/{mid}", f"https://www.tippmixpro.hu/hu/fogadas/e/{mid}",
                  f"https://www.tippmixpro.hu/hu/fogadas/i/merkozes/{mid}"):
            await pg.goto(u, wait_until="domcontentloaded"); await pg.wait_for_timeout(5000)
            txt = (await pg.inner_text("body"))[:300].replace("\n", " ")
            print("PRÓBA", u, "->", pg.url, "|", txt[:200])
        await b.close()

asyncio.run(main())
