"""Meccsoldal URL: shadow DOM-on át megkeresi egy ismert (WAMP-ból vett) mai meccs csapatnevét, rákattint, kiírja az URL-t."""
import asyncio, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tippmixfeed
from playwright.async_api import async_playwright

now = dt.datetime.now(dt.timezone.utc)
with tippmixfeed.Session() as s:
    names = [m["home"] for m in s.today_matches(now, sports=("Labdarúgás",), days_ahead=1)][:80]

FIND = """(names) => {
  const out = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) walk(el.shadowRoot);
      const t = (el.textContent || '').trim();
      if (el.children.length === 0 && t && names.includes(t)) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) out.push({t, x: r.x + r.width / 2, y: r.y + r.height / 2});
      }
    }
  };
  walk(document);
  return out.slice(0, 20);
}"""

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        for vp, ua in (({"width": 390, "height": 844}, "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
                       ({"width": 1366, "height": 900}, None)):
            ctx = await b.new_context(locale="hu-HU", viewport=vp, **({"user_agent": ua} if ua else {}))
            pg = await ctx.new_page()
            await pg.goto("https://www.tippmixpro.hu/hu/fogadas/i/labdarugas/1", wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(9000)
            for txt in ("ÖSSZES ENGEDÉLYEZÉSE", "Összes engedélyezése", "Elfogadom"):
                try:
                    await pg.get_by_text(txt).first.click(timeout=2000); break
                except Exception:
                    pass
            await pg.wait_for_timeout(3000)
            hits = []
            for _ in range(6):
                hits = await pg.evaluate(FIND, names)
                if hits:
                    break
                await pg.mouse.wheel(0, 1200); await pg.wait_for_timeout(2000)
            print(f"[{vp['width']}px] talált csapatnevek:", [h["t"] for h in hits][:10])
            for h in hits:
                before = pg.url
                await pg.mouse.click(h["x"], h["y"])
                await pg.wait_for_timeout(4500)
                if pg.url != before:
                    print("KATTINTÁS:", h["t"], "->", pg.url)
                    break
            else:
                print("nem navigált")
            await ctx.close()
        await b.close()

asyncio.run(main())
