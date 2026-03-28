"""Test: compare popular vs niche queries to verify scraper works"""
import asyncio
import json
import time
from playwright.async_api import async_playwright

COOKIES_FILE = "twikit_cookies.json"


async def search(context, query):
    """Search and return tweet count."""
    from urllib.parse import quote

    page = await context.new_page()
    captured = {}
    event = asyncio.Event()

    async def on_response(response):
        if "SearchTimeline" in response.url and response.status == 200:
            try:
                captured["data"] = await response.json()
                event.set()
            except Exception:
                event.set()

    page.on("response", on_response)

    url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

    try:
        await asyncio.wait_for(event.wait(), timeout=15)
    except asyncio.TimeoutError:
        pass

    tweet_count = 0
    data = captured.get("data", {})
    instructions = (
        data.get("data", {})
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )
    for inst in instructions:
        for entry in inst.get("entries", []):
            if "tweet-" in entry.get("entryId", ""):
                tweet_count += 1

    await page.close()
    return tweet_count


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

    # Queries to test - mix of popular and actual on-demand queries from logs
    queries = [
        "bitcoin",
        "#TAO",
        "since:2026-03-27 until:2026-03-28 (from:Cupseyy)",
        'since:2026-03-21 until:2026-03-28 ("#SN63" OR "#TAO" OR "Quantum Innovate")',
        'since:2026-03-27 until:2026-03-28 ("$SOL" OR "solana memecoin")',
        "since:2026-03-27 until:2026-03-28 (from:ApesPro_)",
    ]

    for query in queries:
        await asyncio.sleep(3)  # rate limit
        count = await search(context, query)
        status = "OK" if count > 0 else "EMPTY"
        print(f"  [{status}] {count:3d} tweets | {query}")

    await browser.close()
    await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
