"""Test: does reusing browser context cause empty results?
Simulates exactly what the miner does - one context, many sequential searches."""
import asyncio
import json
from urllib.parse import quote
from playwright.async_api import async_playwright

COOKIES_FILE = "twikit_cookies.json"


async def search(context, query):
    page = await context.new_page()
    captured = {}
    event = asyncio.Event()

    async def on_response(response):
        if "SearchTimeline" in response.url:
            try:
                if response.status == 200:
                    captured["data"] = await response.json()
                else:
                    captured["status"] = response.status
                event.set()
            except Exception:
                event.set()

    page.on("response", on_response)
    url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

    try:
        await asyncio.wait_for(event.wait(), timeout=15)
    except asyncio.TimeoutError:
        title = await page.title()
        print(f"    TIMEOUT! title='{title}' url='{page.url}'")

    await page.close()

    if "status" in captured:
        return -1  # non-200 status

    count = 0
    data = captured.get("data", {})
    insts = (
        data.get("data", {})
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )
    for inst in insts:
        for entry in inst.get("entries", []):
            if "tweet-" in entry.get("entryId", ""):
                count += 1
    return count


async def main():
    raw_cookies = json.load(open(COOKIES_FILE))
    pw_cookies = [
        {
            "name": k, "value": str(v), "domain": ".x.com",
            "path": "/", "httpOnly": k == "auth_token",
            "secure": True, "sameSite": "None",
        }
        for k, v in raw_cookies.items()
    ]

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    await context.add_cookies(pw_cookies)
    await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

    # Simulate miner: many searches on the same context with 5s gaps
    queries = [
        "bitcoin",                          # popular - should always work
        "#Bittensor",                       # should have results
        "$TAO",                             # cashtag
        'since:2026-03-02 until:2026-03-28 (from:gittensor_io)',  # you showed this works
        "bitcoin",                          # repeat - still work?
        "#Bittensor",                       # repeat
        "bitcoin",                          # 3rd time
        'since:2026-03-21 until:2026-03-28 ("#SN34" OR "$TAO" OR "BitMind")',
        "bitcoin",                          # 4th time - still working?
        "$TAO",                             # repeat
    ]

    print("=== Reusing ONE context (like the miner) ===")
    for i, query in enumerate(queries):
        await asyncio.sleep(5)  # same rate limit as miner
        count = await search(context, query)
        status = "OK" if count > 0 else ("ERR" if count < 0 else "EMPTY")
        print(f"  [{status}] #{i+1:2d} {count:3d} tweets | {query}")

    await browser.close()
    await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
