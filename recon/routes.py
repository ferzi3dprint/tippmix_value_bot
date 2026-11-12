"""Melyik link-változat tölti be a meccsoldalt (pontos nevekkel vs. helyettesítő nevekkel)."""
import asyncio, datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed
from playwright.async_api import async_playwright

MID = "318827872579035136"
with tippmixfeed.Session() as s:
    recs = s.dump(f"/sports/2901/hu/{MID}/match-odds/market-group/Main_BannerMatrix") or []
    m = next((r for r in recs if r.get("_type") == "MATCH"), {})
    print("MATCH név-mezők:", {k: v for k, v in m.items() if "ame" in k or "Id" in k[-2:]})
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
URLS = {
    "pontos": f"https://www.tippmixpro.hu/hu/fogadas/i/esemenyek/1/labdarugas/vilag/valogatott-merkozes/malta-eswatini/{MID}/nepszeru",
    "helyettesítő": f"https://www.tippmixpro.hu/hu/fogadas/i/esemenyek/1/x/x/x/x/{MID}/nepszeru",
    "rövid": f"https://www.tippmixpro.hu/hu/fogadas/i/esemenyek/1/labdarugas/x/x/x/{MID}",
}
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        ctx = await b.new_context(locale="hu-HU", viewport={"width": 390, "height": 844}, user_agent=UA)
        pg = await ctx.new_page()
        for k, u in URLS.items():
            await pg.goto(u, wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(9000)
            html = await pg.evaluate("""() => { let t=''; const w=(r)=>{for(const e of r.querySelectorAll('*')){if(e.shadowRoot)w(e.shadowRoot); if(e.children.length===0) t+=' '+(e.textContent||'').trim();}}; w(document); return t; }""")
            print(f"{k}: url={pg.url} | 404={'404' in html} | Málta={'Málta' in html or 'Malta' in html} | Eswatini={'Eswatini' in html}")
        await b.close()
asyncio.run(main())
