"""Quick test: can Playwright actually fetch tweets from Twitter?"""
import asyncio
import json
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

    # Capture API responses
    captured = {}

    async def on_response(response):
        url = response.url
        if "SearchTimeline" in url:
            try:
                body = await response.json()
                captured["search"] = body
                print(f"  [CAPTURED] SearchTimeline: status={response.status}, keys={list(body.keys()) if body else 'none'}")
            except Exception as e:
                captured["search_error"] = str(e)
                print(f"  [ERROR] SearchTimeline: {e}")
        elif "TweetDetail" in url:
            try:
                body = await response.json()
                captured["detail"] = body
                print(f"  [CAPTURED] TweetDetail: status={response.status}")
            except Exception as e:
                print(f"  [ERROR] TweetDetail: {e}")

    page.on("response", on_response)

    # Test 1: Search
    print("=== Test 1: Search for 'bitcoin' ===")
    await page.goto(
        "https://x.com/search?q=bitcoin&src=typed_query&f=live",
        wait_until="domcontentloaded",
        timeout=45000,
    )

    # Give time for API calls
    await asyncio.sleep(8)

    # Check page title and content
    title = await page.title()
    print(f"  Page title: {title}")

    # Check if we got tweets in the DOM
    tweet_count = await page.locator('[data-testid="tweet"]').count()
    print(f"  Tweets visible in DOM: {tweet_count}")

    if "search" in captured:
        data = captured["search"]
        try:
            instructions = (
                data.get("data", {})
                .get("search_by_raw_query", {})
                .get("search_timeline", {})
                .get("timeline", {})
                .get("instructions", [])
            )
            tweet_entries = 0
            for inst in instructions:
                for entry in inst.get("entries", []):
                    if "tweet-" in entry.get("entryId", ""):
                        tweet_entries += 1
            print(f"  Tweets in API response: {tweet_entries}")
            if tweet_entries > 0:
                # Print first tweet
                for inst in instructions:
                    for entry in inst.get("entries", []):
                        if "tweet-" in entry.get("entryId", ""):
                            result = (
                                entry.get("content", {})
                                .get("itemContent", {})
                                .get("tweet_results", {})
                                .get("result", {})
                            )
                            legacy = result.get("legacy", {})
                            user = (
                                result.get("core", {})
                                .get("user_results", {})
                                .get("result", {})
                                .get("legacy", {})
                            )
                            print(f"  First tweet: @{user.get('screen_name')}: {legacy.get('full_text', '')[:100]}")
                            break
                    break
        except Exception as e:
            print(f"  Parse error: {e}")
            print(f"  Raw keys: {json.dumps(list(data.keys()))}")
    else:
        print("  No SearchTimeline response captured!")
        # Check what URL the page ended up on
        print(f"  Current URL: {page.url}")

    # Take screenshot for debugging
    await page.screenshot(path="/tmp/pw_test.png")
    print("  Screenshot saved to /tmp/pw_test.png")

    await page.close()
    await browser.close()
    await p.stop()

    print("\n=== Done ===")
    return bool(captured.get("search"))


if __name__ == "__main__":
    success = asyncio.run(test_search())
    print(f"\nResult: {'SUCCESS' if success else 'FAILED'}")
