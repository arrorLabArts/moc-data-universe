"""
Custom X/Twitter scraper using twikit library.
Uses Twitter's internal GraphQL API with cookie-based auth.
Requires X_USERNAME, X_EMAIL, and X_PASSWORD in .env file.
"""

import asyncio
import os
import traceback
import threading
import datetime as dt
import bittensor as bt
from pathlib import Path
from typing import List, Optional

from common.data import DataEntity, DataLabel, DataSource
from common.protocol import KeywordMode
from scraping.scraper import ScrapeConfig, Scraper, ValidationResult
from scraping.x.model import XContent
from scraping.x import utils


# Cookie file location (persists login across restarts)
PROJECT_ROOT = Path(__file__).parent.parent.parent
COOKIES_FILE = os.path.join(PROJECT_ROOT, "twikit_cookies.json")
HOMEPAGE_CACHE_FILE = os.path.join(PROJECT_ROOT, "twikit_homepage.html")


class TwikitTwitterScraper(Scraper):
    """
    Scrapes tweets using twikit (Twitter's internal GraphQL API).
    Free alternative to Apify - only requires a regular X/Twitter account.
    """

    SCRAPE_TIMEOUT_SECS = 120
    concurrent_validates_semaphore = threading.BoundedSemaphore(5)

    def __init__(self):
        self._client = None
        self._login_lock = asyncio.Lock()
        self._logged_in = False

    async def _get_client(self):
        """Get or create an authenticated twikit client."""
        if self._client is not None and self._logged_in:
            return self._client

        async with self._login_lock:
            # Double-check after acquiring lock
            if self._client is not None and self._logged_in:
                return self._client

            from twikit import Client

            self._client = Client("en-US")

            # Try loading saved cookies first
            if os.path.exists(COOKIES_FILE):
                try:
                    self._client.load_cookies(COOKIES_FILE)
                    self._logged_in = True
                    bt.logging.success("Loaded twikit cookies from file")

                    # Pre-init ClientTransaction from cached homepage
                    # to avoid Cloudflare blocks on server
                    if os.path.exists(HOMEPAGE_CACHE_FILE):
                        try:
                            await self._init_transaction_from_cache()
                            bt.logging.success(
                                "Loaded cached homepage for ClientTransaction"
                            )
                        except Exception:
                            bt.logging.warning(
                                f"Failed to init from cached homepage: "
                                f"{traceback.format_exc()}"
                            )

                    return self._client
                except Exception:
                    bt.logging.warning(
                        "Failed to load cookies, will re-login"
                    )

            # Login with credentials
            username = os.getenv("X_USERNAME")
            email = os.getenv("X_EMAIL")
            password = os.getenv("X_PASSWORD")

            if not all([username, email, password]):
                raise ValueError(
                    "X_USERNAME, X_EMAIL, and X_PASSWORD must be set in .env "
                    "for the twikit scraper"
                )

            await self._client.login(
                auth_info_1=username,
                auth_info_2=email,
                password=password,
            )
            self._client.save_cookies(COOKIES_FILE)
            self._logged_in = True
            bt.logging.success(f"Logged into X as @{username}")

            return self._client

    async def _init_transaction_from_cache(self):
        """Initialize ClientTransaction using cached homepage HTML."""
        import bs4

        with open(HOMEPAGE_CACHE_FILE, "r", encoding="utf-8") as f:
            html = f.read()

        home_page = bs4.BeautifulSoup(html, "lxml")
        ct = self._client.client_transaction
        ct.home_page_response = home_page

        headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Referer": "https://x.com",
            "User-Agent": self._client._user_agent,
        }

        ct.DEFAULT_ROW_INDEX, ct.DEFAULT_KEY_BYTES_INDICES = (
            await ct.get_indices(home_page, self._client.http, headers)
        )
        ct.key = ct.get_key(response=home_page)
        ct.key_bytes = ct.get_key_bytes(key=ct.key)
        ct.animation_key = ct.get_animation_key(
            key_bytes=ct.key_bytes, response=home_page
        )

    @staticmethod
    def _safe_int(val) -> Optional[int]:
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _parse_tweet_to_xcontent(self, tweet) -> Optional[XContent]:
        """Convert a twikit Tweet object to XContent."""
        try:
            # Build URL
            url = f"https://x.com/{tweet.user.screen_name}/status/{tweet.id}"

            # Extract hashtags in order
            hashtags = []
            if tweet.hashtags:
                hashtags = [f"#{tag}" for tag in tweet.hashtags]

            # Extract media URLs
            media_urls = None
            if tweet.media:
                media_urls = []
                for m in tweet.media:
                    media_url = getattr(m, "media_url", None)
                    if media_url:
                        media_urls.append(media_url)
                if not media_urls:
                    media_urls = None

            # Parse timestamp
            timestamp = dt.datetime.strptime(
                tweet.created_at, "%a %b %d %H:%M:%S %z %Y"
            )

            # Determine tweet type
            reply_to_status = tweet.in_reply_to  # status ID string or None
            is_reply = reply_to_status is not None
            is_quote = getattr(tweet, "is_quote_status", None)

            # Extract user data
            user = tweet.user

            # Extract in_reply_to user ID from legacy data
            in_reply_to_user_id = tweet._legacy.get("in_reply_to_user_id_str")
            in_reply_to_screen_name = tweet._legacy.get("in_reply_to_screen_name")

            return XContent(
                username=user.screen_name,
                text=utils.sanitize_scraped_tweet(tweet.full_text or tweet.text or ""),
                url=url,
                timestamp=timestamp,
                tweet_hashtags=hashtags,
                media=media_urls,
                # User fields
                user_id=str(user.id) if user.id else None,
                user_display_name=user.name,
                user_verified=getattr(user, "verified", None),
                # Tweet metadata
                tweet_id=str(tweet.id),
                is_reply=is_reply,
                is_quote=is_quote,
                conversation_id=tweet._data.get("conversation_id_str"),
                in_reply_to_user_id=in_reply_to_user_id,
                # Static metadata
                language=tweet._legacy.get("lang"),
                in_reply_to_username=in_reply_to_screen_name,
                quoted_tweet_id=(
                    str(tweet.quote.id)
                    if tweet.quote
                    else None
                ),
                # Engagement metrics
                like_count=getattr(tweet, "favorite_count", None),
                retweet_count=getattr(tweet, "retweet_count", None),
                reply_count=getattr(tweet, "reply_count", None),
                quote_count=getattr(tweet, "quote_count", None),
                view_count=self._safe_int(getattr(tweet, "view_count", None)),
                bookmark_count=getattr(tweet, "bookmark_count", None),
                # User profile data
                user_blue_verified=getattr(user, "is_blue_verified", None),
                user_description=getattr(user, "description", None) or None,
                user_location=getattr(user, "location", None) or None,
                profile_image_url=getattr(user, "profile_image_url", None) or None,
                cover_picture_url=getattr(user, "profile_banner_url", None) or None,
                user_followers_count=getattr(user, "followers_count", None),
                user_following_count=getattr(user, "following_count", None),
                # Scrape tracking
                scraped_at=dt.datetime.now(dt.timezone.utc),
            )
        except Exception:
            bt.logging.warning(
                f"Failed to parse tweet: {traceback.format_exc()}"
            )
            return None

    async def validate(
        self, entities: List[DataEntity], allow_low_engagement: bool = False
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
                client = await self._get_client()

                # Extract tweet ID from URL
                tweet_id = entity.uri.rstrip("/").split("/")[-1].split("?")[0]

                tweet = await client.get_tweet_by_id(tweet_id)
                if not tweet:
                    return ValidationResult(
                        is_valid=False,
                        reason="Tweet not found.",
                        content_size_bytes_validated=entity.content_size_bytes,
                    )

                actual_content = self._parse_tweet_to_xcontent(tweet)
                if not actual_content:
                    return ValidationResult(
                        is_valid=False,
                        reason="Failed to parse tweet.",
                        content_size_bytes_validated=entity.content_size_bytes,
                    )

                # Build author_data dict for spam check
                author_data = {
                    "followers": getattr(tweet.user, "followers_count", 0),
                    "createdAt": getattr(tweet.user, "created_at", None),
                }
                view_count = getattr(tweet, "view_count", 0) or 0
                is_retweet = getattr(tweet, "is_retweet", False) or False

                # Check spam/engagement if filtering enabled
                if not allow_low_engagement:
                    if utils.is_spam_account(author_data):
                        return ValidationResult(
                            is_valid=False,
                            reason="Tweet from spam account.",
                            content_size_bytes_validated=entity.content_size_bytes,
                        )
                    if utils.is_low_engagement_tweet({"viewCount": view_count}):
                        return ValidationResult(
                            is_valid=False,
                            reason="Tweet has low engagement.",
                            content_size_bytes_validated=entity.content_size_bytes,
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
                    f"Validation failed for {entity.uri}: {traceback.format_exc()}"
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
        self, scrape_config: ScrapeConfig, allow_low_engagement: bool = False
    ) -> List[DataEntity]:
        """Scrape tweets based on config."""
        try:
            client = await self._get_client()
        except Exception:
            bt.logging.error(
                f"Failed to get twikit client: {traceback.format_exc()}"
            )
            return []

        # Build search query
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
                query_parts.append(f"({' OR '.join(username_labels)})")
            if keyword_labels:
                query_parts.append(f"({' OR '.join(keyword_labels)})")
        else:
            query_parts.append("e")

        # Add date range
        date_format = "%Y-%m-%d"
        query_parts.append(
            f"since:{scrape_config.date_range.start.strftime(date_format)}"
        )
        query_parts.append(
            f"until:{scrape_config.date_range.end.strftime(date_format)}"
        )

        query = " ".join(query_parts)
        max_items = scrape_config.entity_limit or 150

        bt.logging.success(f"Performing twikit scrape for: {query}")

        try:
            search_result = await client.search_tweet(
                query, product="Latest", count=min(max_items, 20)
            )
        except Exception:
            bt.logging.error(
                f"Failed to search tweets for {query}: {traceback.format_exc()}"
            )
            # Re-login on auth failure
            self._logged_in = False
            self._client = None
            return []

        data_entities = []
        tweets_processed = 0

        # Process first page
        for tweet in search_result:
            if tweets_processed >= max_items:
                break

            x_content = self._parse_tweet_to_xcontent(tweet)
            if x_content is None:
                continue

            if not allow_low_engagement:
                author_data = {
                    "followers": getattr(tweet.user, "followers_count", 0),
                }
                if utils.is_spam_account(author_data):
                    continue
                if utils.is_low_engagement_tweet(
                    {"viewCount": getattr(tweet, "view_count", 0) or 0}
                ):
                    continue

            data_entities.append(XContent.to_data_entity(content=x_content))
            tweets_processed += 1

        # Fetch more pages if needed
        while tweets_processed < max_items:
            try:
                more_tweets = await search_result.next()
                if not more_tweets:
                    break
            except Exception:
                break

            for tweet in more_tweets:
                if tweets_processed >= max_items:
                    break

                x_content = self._parse_tweet_to_xcontent(tweet)
                if x_content is None:
                    continue

                if not allow_low_engagement:
                    author_data = {
                        "followers": getattr(tweet.user, "followers_count", 0),
                    }
                    if utils.is_spam_account(author_data):
                        continue
                    if utils.is_low_engagement_tweet(
                        {"viewCount": getattr(tweet, "view_count", 0) or 0}
                    ):
                        continue

                data_entities.append(XContent.to_data_entity(content=x_content))
                tweets_processed += 1

            # Small delay to avoid rate limiting
            await asyncio.sleep(1)

        bt.logging.success(
            f"Completed twikit scrape for {query}. Scraped {len(data_entities)} items."
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
        try:
            client = await self._get_client()
        except Exception:
            bt.logging.error(
                f"Failed to get twikit client: {traceback.format_exc()}"
            )
            return []

        # Handle URL-based lookup
        if url:
            if not utils.is_valid_twitter_url(url):
                bt.logging.error(f"Invalid Twitter URL: {url}")
                return []

            tweet_id = url.rstrip("/").split("/")[-1].split("?")[0]
            bt.logging.info(f"On-demand twikit scrape for URL: {url}")

            try:
                tweet = await client.get_tweet_by_id(tweet_id)
                if not tweet:
                    return []

                x_content = self._parse_tweet_to_xcontent(tweet)
                if x_content is None:
                    return []

                return [XContent.to_data_entity(content=x_content)]
            except Exception:
                bt.logging.error(
                    f"Failed to fetch tweet {url}: {traceback.format_exc()}"
                )
                self._logged_in = False
                self._client = None
                return []

        # Return empty if no params
        if all(
            param is None
            for param in [usernames, keywords, start_datetime, end_datetime]
        ):
            return []

        bt.logging.info(
            f"On-demand twikit scrape: usernames={usernames}, "
            f"keywords={keywords}, mode={keyword_mode}"
        )

        # Build search query
        query_parts = []

        if start_datetime:
            query_parts.append(f"since:{start_datetime.strftime('%Y-%m-%d')}")
        if end_datetime:
            query_parts.append(f"until:{end_datetime.strftime('%Y-%m-%d')}")

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

        bt.logging.success(f"On-demand twikit scrape for: {query}")

        try:
            search_result = await client.search_tweet(
                query, product="Latest", count=min(limit, 20)
            )
        except Exception:
            bt.logging.error(
                f"Failed on-demand search {query}: {traceback.format_exc()}"
            )
            self._logged_in = False
            self._client = None
            return []

        data_entities = []
        tweets_processed = 0

        for tweet in search_result:
            if tweets_processed >= limit:
                break

            x_content = self._parse_tweet_to_xcontent(tweet)
            if x_content is None:
                continue

            data_entities.append(XContent.to_data_entity(content=x_content))
            tweets_processed += 1

        # Fetch more pages if needed
        while tweets_processed < limit:
            try:
                more_tweets = await search_result.next()
                if not more_tweets:
                    break
            except Exception:
                break

            for tweet in more_tweets:
                if tweets_processed >= limit:
                    break

                x_content = self._parse_tweet_to_xcontent(tweet)
                if x_content is None:
                    continue

                data_entities.append(XContent.to_data_entity(content=x_content))
                tweets_processed += 1

            await asyncio.sleep(1)

        bt.logging.success(
            f"On-demand twikit scrape completed. Found {len(data_entities)} items."
        )

        return data_entities
