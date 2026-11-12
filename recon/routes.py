"""A Tippmixpro meccsoldal útvonala: a JS-csomagokból kigyűjti az útvonalmintákat, majd egy meccsre kattint és kiírja az URL-t."""
import asyncio, re
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        ctx = await b.new_context(locale="hu-HU", viewport={"width": 1366, "height": 900})
        pg = await ctx.new_page()
        scripts = []
        pg.on("response", lambda r: scripts.append(r) if r.request.resource_type == "script" and "tippmixpro" in r.url else None)
        await pg.goto("https://www.tippmixpro.hu/hu/fogadas/i/labdarugas/1", wait_until="domcontentloaded", timeout=45000)
        await pg.wait_for_timeout(10000)
        pats = set()
        for r in scripts:
            try:
                js = await r.text()
            except Exception:
                continue
            for m in re.findall(r"[\"'`](/?(?:hu/)?fogadas/[^\"'`]{0,80})[\"'`]", js):
                pats.add(m)
            for m in re.findall(r"[\"'`]([a-z\-]*(?:esemeny|merkozes|event|match)[a-z\-/:]*)[\"'`]", js):
                if len(m) < 60:
                    pats.add(m)
        print("ÚTVONAL-MINTÁK:", sorted(pats)[:120])
        for sel in ["#onetrust-accept-btn-handler", "button:has-text('Elfogad')", "button:has-text('Összes elfogadása')"]:
            try:
                await pg.click(sel, timeout=1500); break
            except Exception:
                pass
        # kattintás: az első olyan elem, amelyben " - " van és rövid (meccsnév)
        cands = await pg.eval_on_selector_all("*", """els => els.filter(e => e.children.length === 0 && / - /.test(e.innerText||'') && (e.innerText||'').length < 70)
             .slice(0, 40).map(e => { const r = e.getBoundingClientRect(); return {t: e.innerText, x: r.x + r.width/2, y: r.y + r.height/2, tag: e.tagName, cls: e.className} })""")
        print("JELÖLTEK:", [(c["t"], c["tag"], str(c["cls"])[:40]) for c in cands[:12]])
        for c in cands:
            if c["y"] <= 0 or c["y"] > 880:
                continue
            before = pg.url
            await pg.mouse.click(c["x"], c["y"])
            await pg.wait_for_timeout(4000)
            if pg.url != before:
                print("KATTINTÁS:", c["t"], "->", pg.url)
                body = (await pg.inner_text("body"))[:600].replace("\n", " | ")
                print("TARTALOM:", body)
                break
        await b.close()

asyncio.run(main())
