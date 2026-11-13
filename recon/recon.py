"""Felderítés: honnan tölti a tippmixpro.hu és a vegas.hu az oddsokat?

Headless böngészővel megnyitja az oldalakat (csak olvasás, bejelentkezés nélkül), és rögzíti a hálózati forgalmat:
XHR/fetch válaszok (URL, típus, méret, eleje) és websocket-üzenetek. Képernyőképet is ment. Kimenet: recon/out/.
"""
import asyncio
import json
import os
import re
import sys

from playwright.async_api import async_playwright

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
SITES = {
    "tippmixpro": ["https://www.tippmixpro.hu/hu/fogadas/i/labdarugas/1", "https://www.tippmixpro.hu/hu/fogadas/i/elo-naptar",
                   "https://www.tippmixpro.hu/hu/fogadas/i/tenisz/3"],
    "tippmix": ["https://www.tippmix.hu/", "https://www.tippmix.hu/fogadas", "https://www.tippmix.hu/sportfogadas"],
    "vegas": ["https://www.vegas.hu/", "https://www.vegas.hu/sportfogadas", "https://www.vegas.hu/sport"],
}
INTERESTING = re.compile(r"odd|price|market|event|match|sport|fixture|bet|coupon|offer|outcome|selection", re.I)
SKIP = re.compile(r"\.(png|jpe?g|gif|svg|webp|woff2?|ttf|css|ico|mp4)(\?|$)|google|facebook|doubleclick|hotjar|analytics|gtm|onetrust|cookie", re.I)


async def visit(p, name, urls):
    os.makedirs(OUT, exist_ok=True)
    log = {"requests": [], "ws": [], "pages": []}
    browser = await p.chromium.launch(channel="chrome")   # a GitHub-gépen előtelepített Chrome
    ctx = await browser.new_context(locale="hu-HU", timezone_id="Europe/Budapest", viewport={"width": 1366, "height": 900},
                                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                               "Chrome/130.0 Safari/537.36")
    page = await ctx.new_page()

    async def on_response(resp):
        url = resp.url
        if SKIP.search(url):
            return
        rt = resp.request.resource_type
        if rt not in ("xhr", "fetch", "document", "script", "other"):
            return
        entry = {"url": url[:400], "type": rt, "status": resp.status, "ct": resp.headers.get("content-type", ""),
                 "method": resp.request.method}
        if rt == "script" and "tippmixpro" in url:
            try:
                js = await resp.text()
                pats = set(re.findall(r"/sports/[^\"'`\s]{3,160}", js)) | set(re.findall(r"[\"'`][a-z\-]*aggregator[a-z\-]*[\"'`]", js))
                if pats:
                    log.setdefault("js_topics", []).extend(sorted(pats)[:400])
            except Exception:
                pass
        if rt in ("xhr", "fetch", "other") and ("json" in entry["ct"] or "text" in entry["ct"] or "proto" in entry["ct"]):
            try:
                body = await resp.text()
                entry["size"] = len(body)
                entry["head"] = body[:600]
                entry["interesting"] = bool(INTERESTING.search(url)) or bool(re.search(r'"(odds|price|outcome)', body[:5000], re.I))
                if entry["interesting"] and len(body) > 500:
                    fn = f"{name}_{len(log['requests'])}.txt"
                    open(os.path.join(OUT, fn), "w", encoding="utf-8").write(url + "\n\n" + body[:200000])
                    entry["saved"] = fn
            except Exception as e:
                entry["err"] = str(e)[:100]
        if rt == "xhr" or rt == "fetch":
            entry["req_headers"] = {k: v for k, v in resp.request.headers.items() if k.lower() not in ("cookie",)}
            entry["post"] = (resp.request.post_data or "")[:500]
        log["requests"].append(entry)

    def on_ws(ws):
        rec = {"url": ws.url, "sent": [], "recv": []}
        log["ws"].append(rec)
        ws.on("framesent", lambda f: len(rec["sent"]) < 400 and rec["sent"].append(str(f)[:800]))
        ws.on("framereceived", lambda f: len(rec["recv"]) < 60 and rec["recv"].append(str(f)[:1500]))

    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
    page.on("websocket", on_ws)
    for i, url in enumerate(urls):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(9000)
            for sel in ["#onetrust-accept-btn-handler", "button:has-text('Elfogad')", "button:has-text('Rendben')",
                        "button:has-text('Összes elfogadása')"]:
                try:
                    await page.click(sel, timeout=1500)
                    break
                except Exception:
                    pass
            for _ in range(4):
                await page.mouse.wheel(0, 2500)
                await page.wait_for_timeout(2500)
            # kattintás az első meccs/bajnokság linkre, hogy a részletes lekérdezések is látszódjanak
            try:
                await page.click("text=/Ma|Mai|Holnap/", timeout=2000)
                await page.wait_for_timeout(4000)
            except Exception:
                pass
            await page.screenshot(path=os.path.join(OUT, f"{name}_{i}.png"))
            text = await page.inner_text("body")
            links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href).slice(0, 400)")
            log["pages"].append({"url": url, "final_url": page.url, "title": await page.title(), "text_head": text[:3000],
                                 "links": [l for l in links if re.search(r"sport|fogad|labda|foci|futball|tenisz", l, re.I)][:120],
                                 "match_links": [l for l in links if re.search(r"\d{15,}", l)][:40]})
        except Exception as e:
            log["pages"].append({"url": url, "error": str(e)[:300]})
    await browser.close()
    json.dump(log, open(os.path.join(OUT, f"{name}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"== {name}: {len(log['requests'])} válasz, {len(log['ws'])} websocket")
    for r in log["requests"]:
        if r.get("interesting"):
            print("  *", r["status"], r["type"], r.get("size"), r["url"][:200])
    for pg in log["pages"]:
        print("  OLDAL", pg.get("final_url"), "| meccs-linkek:", pg.get("match_links", [])[:8])


async def main():
    names = sys.argv[1:] or list(SITES)
    async with async_playwright() as p:
        for n in names:
            await visit(p, n, SITES[n])


if __name__ == "__main__":
    asyncio.run(main())
