"""Quick test: Playwright with domcontentloaded + event wait"""
import asyncio
import json
import time
from playwright.async_api import async_playwright

COOKIES_FILE = "twikit_cookies.json"


async def test_search():
    raw_cookies = json.load(open(COOKIES_FILE))

    pw_cookies = []
    for name, value in raw_cookies.items():
        pw_cookies.append({
            "name": name,
            "value": str(value),
            "domain": ".x.com",
            "path": "/",
            "httpOnly": name in ("auth_token",),
            "secure": True,
            "sameSite": "None",
        })

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )

    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )

    await context.add_cookies(pw_cookies)
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)

    page = await context.new_page()

    captured = {}
    capture_event = asyncio.Event()

    async def on_response(response):
        url = response.url
        if "SearchTimeline" in url:
            try:
                body = await response.json()
                captured["search"] = body
                capture_event.set()
                print(f"  [CAPTURED] SearchTimeline: status={response.status}")
            except Exception as e:
                print(f"  [ERROR] SearchTimeline response: {e}")
                capture_event.set()
        if "/i/api/" in url and response.status != 200:
            print(f"  [API] {response.status} {url[:120]}")

    page.on("response", on_response)

    print("=== Search for 'bitcoin' ===")
    t0 = time.time()

    await page.goto(
        "https://x.com/search?q=bitcoin&src=typed_query&f=live",
        wait_until="domcontentloaded",
        timeout=45000,
    )
    print(f"  DOM loaded in {time.time()-t0:.1f}s")

    # Wait for API response
    try:
        await asyncio.wait_for(capture_event.wait(), timeout=20)
        print(f"  API captured in {time.time()-t0:.1f}s")
    except asyncio.TimeoutError:
        print(f"  TIMEOUT after {time.time()-t0:.1f}s")
        title = await page.title()
        print(f"  Page title: {title}")
        print(f"  Page URL: {page.url}")

    tweet_count = await page.locator('[data-testid="tweet"]').count()
    print(f"  Tweets in DOM: {tweet_count}")

    if "search" in captured:
        data = captured["search"]
        instructions = (
            data.get("data", {})
            .get("search_by_raw_query", {})
            .get("search_timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )
        tweet_entries = sum(
            1 for inst in instructions
            for entry in inst.get("entries", [])
            if "tweet-" in entry.get("entryId", "")
        )
        print(f"  Tweets in API: {tweet_entries}")
    else:
        print("  No SearchTimeline captured!")

    await page.screenshot(path="/tmp/pw_test2.png")
    print("  Screenshot: /tmp/pw_test2.png")

    await page.close()
    await browser.close()
    await p.stop()
    return bool(captured.get("search"))


if __name__ == "__main__":
    ok = asyncio.run(test_search())
    print(f"\nResult: {'SUCCESS' if ok else 'FAILED'}")
