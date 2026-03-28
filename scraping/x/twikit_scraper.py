"""
Custom X/Twitter scraper using Playwright headless browser.
Navigates to x.com search/tweet pages and intercepts GraphQL API responses.
Bypasses Cloudflare because it runs a real Chromium browser.
Requires browser cookies in twikit_cookies.json (auth_token, ct0, etc).

All requests go through a single queue processed one at a time with a
10-second cooldown between requests to avoid rate limiting.
"""

import asyncio
import json
import os
import time
import traceback
import threading
import datetime as dt
import bittensor as bt
from pathlib import Path
from typing import List, Optional, Any

from common.data import DataEntity, DataLabel, DataSource
from common.protocol import KeywordMode
from scraping.scraper import ScrapeConfig, Scraper, ValidationResult
from scraping.x.model import XContent
from scraping.x import utils


PROJECT_ROOT = Path(__file__).parent.parent.parent
COOKIES_FILE = os.path.join(PROJECT_ROOT, "twikit_cookies.json")

COOLDOWN_SECONDS = 10.0


class _QueueRequest:
    """A request to be processed by the queue worker."""

    def __init__(self, kind: str, **kwargs):
        self.kind = kind  # "search" or "detail"
        self.kwargs = kwargs
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()


class TwikitTwitterScraper(Scraper):
    """
    Scrapes tweets using Playwright headless Chromium.
    All Twitter requests go through a single queue processed sequentially
    with a 10-second cooldown between requests.
    """

    SCRAPE_TIMEOUT_SECS = 120
    concurrent_validates_semaphore = threading.BoundedSemaphore(5)
    MAX_RETRIES = 3

    # Shared state across all instances
    _browser = None
    _context = None
    _playwright = None
    _browser_lock = None
    _queue: Optional[asyncio.Queue] = None
    _worker_started = False

    def __init__(self):
        if TwikitTwitterScraper._browser_lock is None:
            TwikitTwitterScraper._browser_lock = asyncio.Lock()

    @staticmethod
    def _load_cookies_raw() -> dict:
        """Load raw cookie dict from file."""
        if not os.path.exists(COOKIES_FILE):
            raise FileNotFoundError(
                f"Cookie file not found: {COOKIES_FILE}. "
                "Export cookies from your browser."
            )
        with open(COOKIES_FILE, "r") as f:
            return json.load(f)

    @staticmethod
    def _cookies_for_playwright(raw_cookies: dict) -> list:
        """Convert raw cookie dict to Playwright cookie format."""
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
        return pw_cookies

    async def _ensure_browser(self):
        """Ensure browser, context, and queue worker are running."""
        if TwikitTwitterScraper._context is not None:
            return TwikitTwitterScraper._context

        async with TwikitTwitterScraper._browser_lock:
            if TwikitTwitterScraper._context is not None:
                return TwikitTwitterScraper._context

            from playwright.async_api import async_playwright

            bt.logging.info("Launching Playwright browser for X scraping...")

            raw_cookies = self._load_cookies_raw()
            pw_cookies = self._cookies_for_playwright(raw_cookies)

            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
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
                timezone_id="America/New_York",
            )

            await context.add_cookies(pw_cookies)

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            TwikitTwitterScraper._browser = browser
            TwikitTwitterScraper._context = context
            TwikitTwitterScraper._playwright = p
            TwikitTwitterScraper._queue = asyncio.Queue()

            bt.logging.success("Playwright browser launched and cookies loaded")

            # Start the queue worker
            asyncio.create_task(self._queue_worker())
            TwikitTwitterScraper._worker_started = True

            # Startup self-test
            try:
                result = await self._do_search("bitcoin")
                bt.logging.success(
                    f"Startup self-test PASSED: found {len(result)} tweets "
                    f"for 'bitcoin'"
                )
            except Exception as e:
                bt.logging.warning(f"Startup self-test failed: {e}")

            return context

    async def _queue_worker(self):
        """Background worker: processes one request at a time with cooldown."""
        bt.logging.info("X scraper queue worker started")
        queue = TwikitTwitterScraper._queue

        while True:
            try:
                req = await queue.get()

                bt.logging.debug(
                    f"Queue worker processing {req.kind} request "
                    f"(queue size: {queue.qsize()})"
                )

                try:
                    if req.kind == "search":
                        result = await self._do_search(req.kwargs["query"])
                        req.future.set_result(result)
                    elif req.kind == "detail":
                        result = await self._do_tweet_detail(
                            req.kwargs["tweet_id"],
                            req.kwargs.get("tweet_url"),
                        )
                        req.future.set_result(result)
                    else:
                        req.future.set_result(None)
                except Exception as e:
                    req.future.set_exception(e)

                queue.task_done()

                # Cooldown AFTER the request completes
                bt.logging.debug(
                    f"Request done. Cooling down {COOLDOWN_SECONDS}s..."
                )
                await asyncio.sleep(COOLDOWN_SECONDS)

            except Exception:
                bt.logging.error(
                    f"Queue worker error: {traceback.format_exc()}"
                )
                await asyncio.sleep(5)

    async def _enqueue(self, kind: str, **kwargs) -> Any:
        """Submit a request to the queue and wait for the result."""
        await self._ensure_browser()

        req = _QueueRequest(kind, **kwargs)
        await TwikitTwitterScraper._queue.put(req)

        queue_size = TwikitTwitterScraper._queue.qsize()
        if queue_size > 1:
            bt.logging.debug(f"Queued {kind} request (position: {queue_size})")

        return await req.future

    async def _do_search(self, query: str) -> list:
        """Actually perform a search (called by queue worker only)."""
        from urllib.parse import quote

        search_url = (
            f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
        )

        bt.logging.info(f"Playwright search: {query}")
        data = await self._intercept_api_response(
            search_url, "SearchTimeline", timeout_ms=45000
        )

        if not data:
            bt.logging.warning("No SearchTimeline response captured")
            return []

        tweets = self._extract_tweets_from_timeline(data)
        if not tweets:
            try:
                bt.logging.warning(
                    f"SearchTimeline returned 0 tweets. "
                    f"Response preview: "
                    f"{json.dumps(data, default=str)[:500]}"
                )
            except Exception:
                pass
        else:
            bt.logging.success(
                f"Search returned {len(tweets)} tweets for: {query}"
            )
        return tweets

    async def _do_tweet_detail(
        self, tweet_id: str, tweet_url: str = None
    ) -> Optional[dict]:
        """Actually fetch a tweet detail (called by queue worker only)."""
        if not tweet_url:
            tweet_url = f"https://x.com/i/status/{tweet_id}"

        bt.logging.info(f"Playwright tweet detail: {tweet_id}")
        data = await self._intercept_api_response(
            tweet_url, "TweetDetail", timeout_ms=30000
        )

        if not data:
            bt.logging.warning(
                f"No TweetDetail response captured for {tweet_id}"
            )
            return None

        tweets = self._extract_tweets_from_detail(data)
        for t in tweets:
            if t.get("rest_id") == tweet_id:
                return t
        return tweets[0] if tweets else None

    async def _intercept_api_response(
        self, url: str, api_pattern: str, timeout_ms: int = 30000
    ) -> Optional[dict]:
        """
        Navigate to a URL and intercept the matching API response.
        Returns the parsed JSON from the intercepted response.
        """
        context = TwikitTwitterScraper._context
        page = await context.new_page()

        captured_response = None
        capture_event = asyncio.Event()

        async def handle_response(response):
            nonlocal captured_response
            if api_pattern in response.url and captured_response is None:
                try:
                    if response.status == 200:
                        body = await response.json()
                        captured_response = body
                        capture_event.set()
                        bt.logging.debug(
                            f"Captured {api_pattern} response (200)"
                        )
                    elif response.status == 429:
                        bt.logging.warning(
                            f"Rate limited (429) for {api_pattern}"
                        )
                        capture_event.set()
                    else:
                        bt.logging.warning(
                            f"API response {response.status} for {api_pattern}"
                        )
                        capture_event.set()
                except Exception as e:
                    bt.logging.warning(f"Error reading API response: {e}")

        page.on("response", handle_response)

        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=timeout_ms
            )

            bt.logging.debug(
                f"Page DOM loaded, waiting up to 20s for {api_pattern}..."
            )
            try:
                await asyncio.wait_for(capture_event.wait(), timeout=20)
            except asyncio.TimeoutError:
                page_title = await page.title()
                page_url = page.url
                bt.logging.warning(
                    f"Timeout waiting for {api_pattern}. "
                    f"title='{page_title}', url='{page_url}'"
                )
                try:
                    await page.screenshot(
                        path="/tmp/pw_debug.png", full_page=False
                    )
                    bt.logging.info(
                        "Debug screenshot saved to /tmp/pw_debug.png"
                    )
                except Exception:
                    pass

            return captured_response

        except Exception as e:
            bt.logging.error(f"Page navigation failed: {e}")
            try:
                page_title = await page.title()
                bt.logging.error(f"Page title on error: '{page_title}'")
                await page.screenshot(
                    path="/tmp/pw_debug.png", full_page=False
                )
            except Exception:
                pass
            return None
        finally:
            await page.close()

    # ── Search / detail public methods (enqueue + retry) ──

    async def _search_tweets(self, query: str, count: int = 20) -> list:
        """Search tweets via the queue."""
        return await self._enqueue("search", query=query)

    async def _get_tweet_detail(
        self, tweet_id: str, tweet_url: str = None
    ) -> Optional[dict]:
        """Get tweet detail via the queue."""
        return await self._enqueue(
            "detail", tweet_id=tweet_id, tweet_url=tweet_url
        )

    async def _search_with_retry(self, query: str, count: int = 20) -> list:
        """Search with retry on failure."""
        for attempt in range(self.MAX_RETRIES):
            try:
                results = await self._search_tweets(query, count)
                if results:
                    return results
                if attempt < self.MAX_RETRIES - 1:
                    bt.logging.warning(
                        f"Search returned 0 results on attempt "
                        f"{attempt + 1}/{self.MAX_RETRIES}, will retry"
                    )
                    # No extra sleep here — the queue worker already
                    # enforces 10s cooldown between requests
            except Exception:
                bt.logging.error(
                    f"Search failed: {traceback.format_exc()}"
                )
        return []

    async def _get_tweet_with_retry(self, tweet_id: str) -> Optional[dict]:
        """Get tweet by ID with retry."""
        for attempt in range(self.MAX_RETRIES):
            try:
                result = await self._get_tweet_detail(tweet_id)
                if result:
                    return result
            except Exception:
                bt.logging.error(
                    f"Get tweet failed: {traceback.format_exc()}"
                )
        return None

    # ── Response parsing ──

    def _extract_tweets_from_timeline(self, data: dict) -> list:
        """Extract tweet data from SearchTimeline response."""
        tweets = []
        try:
            instructions = (
                data.get("data", {})
                .get("search_by_raw_query", {})
                .get("search_timeline", {})
                .get("timeline", {})
                .get("instructions", [])
            )
            for instruction in instructions:
                entries = instruction.get("entries", [])
                for entry in entries:
                    content = entry.get("content", {})
                    item_content = content.get("itemContent", {})
                    if not item_content:
                        items = content.get("items", [])
                        for item in items:
                            ic = item.get("item", {}).get("itemContent", {})
                            tweet_results = ic.get("tweet_results", {})
                            result = tweet_results.get("result", {})
                            if result:
                                tweets.append(
                                    self._normalize_tweet_result(result)
                                )
                        continue

                    tweet_results = item_content.get("tweet_results", {})
                    result = tweet_results.get("result", {})
                    if result:
                        tweets.append(self._normalize_tweet_result(result))
        except Exception:
            bt.logging.warning(
                f"Failed to extract tweets: {traceback.format_exc()}"
            )
        return [t for t in tweets if t is not None]

    def _extract_tweets_from_detail(self, data: dict) -> list:
        """Extract tweet data from TweetDetail response."""
        tweets = []
        try:
            instructions = (
                data.get("data", {})
                .get("threaded_conversation_with_injections_v2", {})
                .get("instructions", [])
            )
            for instruction in instructions:
                entries = instruction.get("entries", [])
                for entry in entries:
                    content = entry.get("content", {})
                    item_content = content.get("itemContent", {})
                    if item_content:
                        tweet_results = item_content.get("tweet_results", {})
                        result = tweet_results.get("result", {})
                        if result:
                            tweets.append(
                                self._normalize_tweet_result(result)
                            )
                    items = content.get("items", [])
                    for item in items:
                        ic = item.get("item", {}).get("itemContent", {})
                        tweet_results = ic.get("tweet_results", {})
                        result = tweet_results.get("result", {})
                        if result:
                            tweets.append(
                                self._normalize_tweet_result(result)
                            )
        except Exception:
            bt.logging.warning(
                f"Failed to extract tweet detail: {traceback.format_exc()}"
            )
        return [t for t in tweets if t is not None]

    def _normalize_tweet_result(self, result: dict) -> Optional[dict]:
        """Normalize a tweet result object."""
        if result.get("__typename") == "TweetWithVisibilityResults":
            result = result.get("tweet", {})
        if result.get("__typename") == "TweetTombstone":
            return None
        if not result.get("legacy"):
            return result if result.get("rest_id") else None
        return result

    @staticmethod
    def _safe_int(val) -> Optional[int]:
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _parse_raw_tweet_to_xcontent(
        self, tweet_data: dict
    ) -> Optional[XContent]:
        """Convert raw GraphQL tweet data to XContent."""
        try:
            legacy = tweet_data.get("legacy", {})
            core = tweet_data.get("core", {})
            user_results = core.get("user_results", {}).get("result", {})

            # Twitter moved user fields from result.legacy to result.core
            user_core = user_results.get("core", {})
            user_legacy = user_results.get("legacy", {})

            tweet_id = tweet_data.get("rest_id", "")
            # Try new path (result.core) first, fall back to old (result.legacy)
            screen_name = (
                user_core.get("screen_name", "")
                or user_legacy.get("screen_name", "")
            )

            if not tweet_id or not screen_name:
                return None

            url = f"https://x.com/{screen_name}/status/{tweet_id}"
            text = legacy.get("full_text", "")

            hashtags = []
            entities = legacy.get("entities", {})
            for ht in entities.get("hashtags", []):
                hashtags.append(f"#{ht.get('text', '')}")

            media_urls = None
            extended = legacy.get("extended_entities", {})
            media_list = extended.get("media", entities.get("media", []))
            if media_list:
                media_urls = [
                    m.get("media_url_https") or m.get("media_url")
                    for m in media_list
                    if m.get("media_url_https") or m.get("media_url")
                ]
                if not media_urls:
                    media_urls = None

            created_at = legacy.get("created_at", "")
            timestamp = dt.datetime.strptime(
                created_at, "%a %b %d %H:%M:%S %z %Y"
            )

            in_reply_to_status = legacy.get("in_reply_to_status_id_str")
            is_reply = in_reply_to_status is not None
            is_quote = legacy.get("is_quote_status", False)

            quoted_tweet_id = None
            quoted = (
                tweet_data.get("quoted_status_result", {}).get("result", {})
            )
            if quoted:
                quoted_tweet_id = quoted.get("rest_id")

            views = tweet_data.get("views", {})
            view_count = self._safe_int(views.get("count"))

            # User display name: try new path, fall back to old
            display_name = (
                user_core.get("name", "")
                or user_legacy.get("name", "")
            )

            # Avatar: try new avatar field, fall back to legacy
            avatar = user_results.get("avatar", {})
            profile_image = (
                avatar.get("image_url")
                or user_legacy.get("profile_image_url_https")
            ) or None

            return XContent(
                username=screen_name,
                text=utils.sanitize_scraped_tweet(text),
                url=url,
                timestamp=timestamp,
                tweet_hashtags=hashtags,
                media=media_urls,
                user_id=user_results.get("rest_id"),
                user_display_name=display_name or None,
                user_verified=user_legacy.get("verified"),
                tweet_id=tweet_id,
                is_reply=is_reply,
                is_quote=is_quote,
                conversation_id=legacy.get("conversation_id_str"),
                in_reply_to_user_id=legacy.get("in_reply_to_user_id_str"),
                language=legacy.get("lang"),
                in_reply_to_username=legacy.get(
                    "in_reply_to_screen_name"
                ),
                quoted_tweet_id=quoted_tweet_id,
                like_count=legacy.get("favorite_count"),
                retweet_count=legacy.get("retweet_count"),
                reply_count=legacy.get("reply_count"),
                quote_count=legacy.get("quote_count"),
                view_count=view_count,
                bookmark_count=legacy.get("bookmark_count"),
                user_blue_verified=user_results.get("is_blue_verified"),
                user_description=user_legacy.get("description") or None,
                user_location=user_legacy.get("location") or None,
                profile_image_url=profile_image,
                cover_picture_url=user_legacy.get("profile_banner_url")
                or None,
                user_followers_count=user_legacy.get("followers_count"),
                user_following_count=user_legacy.get("friends_count"),
                scraped_at=dt.datetime.now(dt.timezone.utc),
            )
        except Exception:
            bt.logging.warning(
                f"Failed to parse tweet: {traceback.format_exc()}"
            )
            return None

    # ── Public interface: validate, scrape, on_demand_scrape ──

    async def validate(
        self,
        entities: List[DataEntity],
        allow_low_engagement: bool = False,
    ) -> List[ValidationResult]:
        """Validate DataEntities by fetching the actual tweet."""

        async def validate_entity(entity: DataEntity) -> ValidationResult:
            if not utils.is_valid_twitter_url(entity.uri):
                return ValidationResult(
                    is_valid=False,
                    reason="Invalid URI.",
                    content_size_bytes_validated=entity.content_size_bytes,
                )

            try:
                tweet_id = (
                    entity.uri.rstrip("/").split("/")[-1].split("?")[0]
                )
                tweet_data = await self._get_tweet_with_retry(tweet_id)

                if not tweet_data:
                    return ValidationResult(
                        is_valid=False,
                        reason="Tweet not found.",
                        content_size_bytes_validated=(
                            entity.content_size_bytes
                        ),
                    )

                actual_content = self._parse_raw_tweet_to_xcontent(
                    tweet_data
                )
                if not actual_content:
                    return ValidationResult(
                        is_valid=False,
                        reason="Failed to parse tweet.",
                        content_size_bytes_validated=(
                            entity.content_size_bytes
                        ),
                    )

                legacy = tweet_data.get("legacy", {})
                user_result = (
                    tweet_data.get("core", {})
                    .get("user_results", {})
                    .get("result", {})
                )
                user_legacy = user_result.get("legacy", {})
                user_core = user_result.get("core", {})

                author_data = {
                    "followers": user_legacy.get("followers_count", 0),
                    "createdAt": (
                        user_core.get("created_at")
                        or user_legacy.get("created_at")
                    ),
                }
                views = tweet_data.get("views", {})
                view_count = self._safe_int(views.get("count")) or 0
                is_retweet = legacy.get("retweeted", False)

                if not allow_low_engagement:
                    if utils.is_spam_account(author_data):
                        return ValidationResult(
                            is_valid=False,
                            reason="Tweet from spam account.",
                            content_size_bytes_validated=(
                                entity.content_size_bytes
                            ),
                        )
                    if utils.is_low_engagement_tweet(
                        {"viewCount": view_count}
                    ):
                        return ValidationResult(
                            is_valid=False,
                            reason="Tweet has low engagement.",
                            content_size_bytes_validated=(
                                entity.content_size_bytes
                            ),
                        )

                return utils.validate_tweet_content(
                    actual_tweet=actual_content,
                    entity=entity,
                    is_retweet=is_retweet,
                    author_data=author_data,
                    view_count=view_count,
                )

            except Exception:
                bt.logging.error(
                    f"Validation failed for {entity.uri}: "
                    f"{traceback.format_exc()}"
                )
                return ValidationResult(
                    is_valid=False,
                    reason="Failed to fetch tweet for validation.",
                    content_size_bytes_validated=entity.content_size_bytes,
                )

        if not entities:
            return []

        with TwikitTwitterScraper.concurrent_validates_semaphore:
            results = await asyncio.gather(
                *[validate_entity(entity) for entity in entities]
            )

        return results

    async def scrape(
        self,
        scrape_config: ScrapeConfig,
        allow_low_engagement: bool = False,
    ) -> List[DataEntity]:
        """Scrape tweets based on config."""
        query_parts = []

        if scrape_config.labels:
            username_labels = []
            keyword_labels = []

            for label in scrape_config.labels:
                if label.value.startswith("@"):
                    username_labels.append(f"from:{label.value[1:]}")
                else:
                    keyword_labels.append(label.value)

            if username_labels:
                query_parts.append(
                    f"({' OR '.join(username_labels)})"
                )
            if keyword_labels:
                query_parts.append(
                    f"({' OR '.join(keyword_labels)})"
                )
        else:
            query_parts.append("e")

        date_format = "%Y-%m-%d"
        query_parts.append(
            f"since:{scrape_config.date_range.start.strftime(date_format)}"
        )
        query_parts.append(
            f"until:{scrape_config.date_range.end.strftime(date_format)}"
        )

        query = " ".join(query_parts)
        max_items = scrape_config.entity_limit or 150

        bt.logging.success(f"Performing X scrape for: {query}")

        raw_tweets = await self._search_with_retry(
            query, min(max_items, 20)
        )

        data_entities = []
        for tweet_data in raw_tweets[:max_items]:
            x_content = self._parse_raw_tweet_to_xcontent(tweet_data)
            if x_content is None:
                continue

            if not allow_low_engagement:
                user_legacy = (
                    tweet_data.get("core", {})
                    .get("user_results", {})
                    .get("result", {})
                    .get("legacy", {})
                )
                author_data = {
                    "followers": user_legacy.get("followers_count", 0)
                }
                if utils.is_spam_account(author_data):
                    continue
                views = tweet_data.get("views", {})
                view_count = self._safe_int(views.get("count")) or 0
                if utils.is_low_engagement_tweet(
                    {"viewCount": view_count}
                ):
                    continue

            data_entities.append(
                XContent.to_data_entity(content=x_content)
            )

        bt.logging.success(
            f"Completed X scrape for {query}. "
            f"Scraped {len(data_entities)} items."
        )
        return data_entities

    async def on_demand_scrape(
        self,
        usernames: List[str] = None,
        keywords: List[str] = None,
        url: str = None,
        keyword_mode: KeywordMode = "all",
        start_datetime: dt.datetime = None,
        end_datetime: dt.datetime = None,
        limit: int = 100,
    ) -> List[DataEntity]:
        """On-demand scrape for validator requests."""

        # Handle URL-based lookup
        if url:
            if not utils.is_valid_twitter_url(url):
                bt.logging.error(f"Invalid Twitter URL: {url}")
                return []

            tweet_id = url.rstrip("/").split("/")[-1].split("?")[0]
            bt.logging.info(f"On-demand X scrape for URL: {url}")

            try:
                tweet_data = await self._get_tweet_with_retry(tweet_id)
                if not tweet_data:
                    return []

                x_content = self._parse_raw_tweet_to_xcontent(tweet_data)
                if x_content is None:
                    return []

                return [XContent.to_data_entity(content=x_content)]
            except Exception:
                bt.logging.error(
                    f"Failed to fetch tweet {url}: "
                    f"{traceback.format_exc()}"
                )
                return []

        if all(
            param is None
            for param in [usernames, keywords, start_datetime, end_datetime]
        ):
            return []

        bt.logging.info(
            f"On-demand X scrape: usernames={usernames}, "
            f"keywords={keywords}, mode={keyword_mode}"
        )

        query_parts = []

        if start_datetime:
            query_parts.append(
                f"since:{start_datetime.strftime('%Y-%m-%d')}"
            )
        if end_datetime:
            query_parts.append(
                f"until:{end_datetime.strftime('%Y-%m-%d')}"
            )

        if usernames:
            username_queries = [
                f"from:{u.removeprefix('@')}" for u in usernames
            ]
            query_parts.append(f"({' OR '.join(username_queries)})")

        if keywords:
            quoted = [f'"{kw}"' for kw in keywords]
            if keyword_mode == "all":
                query_parts.append(f"({' AND '.join(quoted)})")
            else:
                query_parts.append(f"({' OR '.join(quoted)})")

        if not usernames and not keywords:
            query_parts.append("e")

        query = " ".join(query_parts)

        bt.logging.success(f"On-demand X scrape for: {query}")

        raw_tweets = await self._search_with_retry(
            query, min(limit, 20)
        )

        data_entities = []
        for tweet_data in raw_tweets[:limit]:
            x_content = self._parse_raw_tweet_to_xcontent(tweet_data)
            if x_content is None:
                continue
            data_entities.append(
                XContent.to_data_entity(content=x_content)
            )

        bt.logging.success(
            f"On-demand X scrape completed. "
            f"Found {len(data_entities)} items."
        )
        return data_entities
