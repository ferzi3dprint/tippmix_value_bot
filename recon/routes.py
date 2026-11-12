"""Meccsoldal URL: a focioldal látható szövegéből meccsnevet keres, rákattint (shadow DOM-on át), és kiírja az URL-t."""
import asyncio, re
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        for vp, ua in (({"width": 1366, "height": 900}, None),
                       ({"width": 390, "height": 844}, "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")):
            ctx = await b.new_context(locale="hu-HU", viewport=vp, **({"user_agent": ua} if ua else {}))
            pg = await ctx.new_page()
            await pg.goto("https://www.tippmixpro.hu/hu/fogadas/i/labdarugas/1", wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(10000)
            for sel in ["#onetrust-accept-btn-handler", "button:has-text('Elfogad')", "button:has-text('Összes elfogadása')"]:
                try:
                    await pg.click(sel, timeout=1500); break
                except Exception:
                    pass
            text = await pg.locator("body").inner_text()
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            print(f"\n[{vp['width']}px] szöveg-minta:", lines[20:60])
            # csapatnév-jelöltek: rövid sorok, amelyek nem menüpontok
            done = False
            for l in lines[20:200]:
                if len(l) < 4 or len(l) > 40 or re.search(r"\d", l) or l.isupper():
                    continue
                try:
                    before = pg.url
                    await pg.get_by_text(l, exact=True).first.click(timeout=2000, force=True)
                    await pg.wait_for_timeout(4000)
                    if pg.url != before and "labdarugas/1" not in pg.url:
                        print("KATTINTÁS:", l, "->", pg.url)
                        print("TARTALOM:", (await pg.locator("body").inner_text())[:400].replace("\n", " | "))
                        done = True
                        break
                    if pg.url != before:
                        await pg.go_back(); await pg.wait_for_timeout(3000)
                except Exception:
                    continue
            if not done:
                print("nem sikerült meccsoldalra jutni")
            await ctx.close()
        await b.close()

asyncio.run(main())
