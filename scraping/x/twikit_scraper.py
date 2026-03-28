"""
Custom X/Twitter scraper using curl_cffi for Cloudflare bypass.
Calls Twitter's GraphQL API directly with browser-like TLS fingerprint.
Requires browser cookies in twikit_cookies.json (auth_token, ct0, etc).
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
from typing import List, Optional
from urllib.parse import urlencode, quote

from common.data import DataEntity, DataLabel, DataSource
from common.protocol import KeywordMode
from scraping.scraper import ScrapeConfig, Scraper, ValidationResult
from scraping.x.model import XContent
from scraping.x import utils


PROJECT_ROOT = Path(__file__).parent.parent.parent
COOKIES_FILE = os.path.join(PROJECT_ROOT, "twikit_cookies.json")

BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# Current GraphQL query IDs - update these when Twitter rotates them
SEARCH_TIMELINE_ID = "GcXk9vN_d1jUfHNqLacXQA"
TWEET_DETAIL_ID = "CysGzLIZa76UzZ3WTe-Bhg"

GRAPHQL_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}

FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withArticleSummaryText": True,
    "withArticleVoiceOver": True,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}


class TwikitTwitterScraper(Scraper):
    """
    Scrapes tweets using Twitter's GraphQL API directly via curl_cffi.
    Bypasses Cloudflare by impersonating browser TLS fingerprint.
    """

    SCRAPE_TIMEOUT_SECS = 120
    concurrent_validates_semaphore = threading.BoundedSemaphore(5)

    # Shared state across all instances
    _cookies = None
    _rate_lock = None
    _last_request_time = 0
    MIN_REQUEST_INTERVAL = 3.0
    MAX_RETRIES = 3

    def __init__(self):
        if TwikitTwitterScraper._rate_lock is None:
            TwikitTwitterScraper._rate_lock = asyncio.Lock()

    def _load_cookies(self):
        """Load cookies from file (cached across instances)."""
        if TwikitTwitterScraper._cookies is not None:
            return TwikitTwitterScraper._cookies

        if not os.path.exists(COOKIES_FILE):
            raise FileNotFoundError(
                f"Cookie file not found: {COOKIES_FILE}. "
                "Export cookies from your browser."
            )

        with open(COOKIES_FILE, "r") as f:
            TwikitTwitterScraper._cookies = json.load(f)

        bt.logging.success("Loaded X cookies from file")
        return TwikitTwitterScraper._cookies

    async def _rate_limit(self):
        """Simple rate limiter."""
        async with TwikitTwitterScraper._rate_lock:
            now = time.time()
            elapsed = now - TwikitTwitterScraper._last_request_time
            if elapsed < self.MIN_REQUEST_INTERVAL:
                await asyncio.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
            TwikitTwitterScraper._last_request_time = time.time()

    def _build_headers(self, cookies: dict) -> dict:
        """Build request headers matching browser format."""
        return {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "authorization": f"Bearer {BEARER_TOKEN}",
            "content-type": "application/json",
            "x-csrf-token": cookies.get("ct0", ""),
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "en",
        }

    async def _graphql_request(self, query_id: str, operation: str,
                                variables: dict, extra_params: dict = None) -> dict:
        """Make a GraphQL request to Twitter API using curl_cffi."""
        from curl_cffi.requests import AsyncSession

        cookies = self._load_cookies()
        headers = self._build_headers(cookies)

        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(GRAPHQL_FEATURES, separators=(",", ":")),
        }
        if extra_params:
            for k, v in extra_params.items():
                params[k] = json.dumps(v, separators=(",", ":")) if isinstance(v, dict) else v

        url = f"https://x.com/i/api/graphql/{query_id}/{operation}?{urlencode(params, quote_via=quote)}"

        async with AsyncSession(impersonate="chrome") as session:
            response = await session.get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                raise RateLimitError(f"Rate limited (429)")
            else:
                raise APIError(
                    f"Twitter API returned {response.status_code}: "
                    f"{response.text[:200]}"
                )

    async def _search_tweets(self, query: str, count: int = 20) -> list:
        """Search tweets and return raw tweet data list."""
        variables = {
            "rawQuery": query,
            "count": count,
            "querySource": "typed_query",
            "product": "Latest",
        }

        await self._rate_limit()
        data = await self._graphql_request(
            SEARCH_TIMELINE_ID, "SearchTimeline", variables
        )

        return self._extract_tweets_from_timeline(data)

    async def _get_tweet_detail(self, tweet_id: str) -> Optional[dict]:
        """Get a single tweet by ID."""
        variables = {
            "focalTweetId": tweet_id,
            "with_rux_injections": False,
            "rankingMode": "Relevance",
            "includePromotedContent": True,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
        }

        await self._rate_limit()
        data = await self._graphql_request(
            TWEET_DETAIL_ID, "TweetDetail", variables,
            extra_params={"fieldToggles": FIELD_TOGGLES}
        )

        tweets = self._extract_tweets_from_detail(data)
        # Find the focal tweet
        for t in tweets:
            if t.get("rest_id") == tweet_id:
                return t
        return tweets[0] if tweets else None

    def _extract_tweets_from_timeline(self, data: dict) -> list:
        """Extract tweet data from SearchTimeline response."""
        tweets = []
        try:
            instructions = data.get("data", {}).get("search_by_raw_query", {}).get("search_timeline", {}).get("timeline", {}).get("instructions", [])
            for instruction in instructions:
                entries = instruction.get("entries", [])
                for entry in entries:
                    content = entry.get("content", {})
                    item_content = content.get("itemContent", {})
                    if not item_content:
                        # Check for items in moduleItems
                        items = content.get("items", [])
                        for item in items:
                            ic = item.get("item", {}).get("itemContent", {})
                            tweet_results = ic.get("tweet_results", {})
                            result = tweet_results.get("result", {})
                            if result:
                                tweets.append(self._normalize_tweet_result(result))
                        continue

                    tweet_results = item_content.get("tweet_results", {})
                    result = tweet_results.get("result", {})
                    if result:
                        tweets.append(self._normalize_tweet_result(result))
        except Exception:
            bt.logging.warning(f"Failed to extract tweets: {traceback.format_exc()}")
        return [t for t in tweets if t is not None]

    def _extract_tweets_from_detail(self, data: dict) -> list:
        """Extract tweet data from TweetDetail response."""
        tweets = []
        try:
            instructions = data.get("data", {}).get("threaded_conversation_with_injections_v2", {}).get("instructions", [])
            for instruction in instructions:
                entries = instruction.get("entries", [])
                for entry in entries:
                    content = entry.get("content", {})
                    item_content = content.get("itemContent", {})
                    if item_content:
                        tweet_results = item_content.get("tweet_results", {})
                        result = tweet_results.get("result", {})
                        if result:
                            tweets.append(self._normalize_tweet_result(result))
                    # Also check items for threaded replies
                    items = content.get("items", [])
                    for item in items:
                        ic = item.get("item", {}).get("itemContent", {})
                        tweet_results = ic.get("tweet_results", {})
                        result = tweet_results.get("result", {})
                        if result:
                            tweets.append(self._normalize_tweet_result(result))
        except Exception:
            bt.logging.warning(f"Failed to extract tweet detail: {traceback.format_exc()}")
        return [t for t in tweets if t is not None]

    def _normalize_tweet_result(self, result: dict) -> Optional[dict]:
        """Normalize a tweet result object, handling various wrapper types."""
        # Handle TweetWithVisibilityResults wrapper
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

    def _parse_raw_tweet_to_xcontent(self, tweet_data: dict) -> Optional[XContent]:
        """Convert raw GraphQL tweet data to XContent."""
        try:
            legacy = tweet_data.get("legacy", {})
            core = tweet_data.get("core", {})
            user_results = core.get("user_results", {}).get("result", {})
            user_legacy = user_results.get("legacy", {})

            tweet_id = tweet_data.get("rest_id", "")
            screen_name = user_legacy.get("screen_name", "")

            if not tweet_id or not screen_name:
                return None

            url = f"https://x.com/{screen_name}/status/{tweet_id}"

            # Text
            text = legacy.get("full_text", "")

            # Hashtags
            hashtags = []
            entities = legacy.get("entities", {})
            for ht in entities.get("hashtags", []):
                hashtags.append(f"#{ht.get('text', '')}")

            # Media
            media_urls = None
            extended = legacy.get("extended_entities", {})
            media_list = extended.get("media", entities.get("media", []))
            if media_list:
                media_urls = [m.get("media_url_https") or m.get("media_url") for m in media_list if m.get("media_url_https") or m.get("media_url")]
                if not media_urls:
                    media_urls = None

            # Timestamp
            created_at = legacy.get("created_at", "")
            timestamp = dt.datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")

            # Reply/quote info
            in_reply_to_status = legacy.get("in_reply_to_status_id_str")
            is_reply = in_reply_to_status is not None
            is_quote = legacy.get("is_quote_status", False)

            # Quoted tweet
            quoted_tweet_id = None
            quoted = tweet_data.get("quoted_status_result", {}).get("result", {})
            if quoted:
                quoted_tweet_id = quoted.get("rest_id")

            # View count
            views = tweet_data.get("views", {})
            view_count = self._safe_int(views.get("count"))

            return XContent(
                username=screen_name,
                text=utils.sanitize_scraped_tweet(text),
                url=url,
                timestamp=timestamp,
                tweet_hashtags=hashtags,
                media=media_urls,
                # User fields
                user_id=user_results.get("rest_id"),
                user_display_name=user_legacy.get("name"),
                user_verified=user_legacy.get("verified"),
                # Tweet metadata
                tweet_id=tweet_id,
                is_reply=is_reply,
                is_quote=is_quote,
                conversation_id=legacy.get("conversation_id_str"),
                in_reply_to_user_id=legacy.get("in_reply_to_user_id_str"),
                language=legacy.get("lang"),
                in_reply_to_username=legacy.get("in_reply_to_screen_name"),
                quoted_tweet_id=quoted_tweet_id,
                # Engagement
                like_count=legacy.get("favorite_count"),
                retweet_count=legacy.get("retweet_count"),
                reply_count=legacy.get("reply_count"),
                quote_count=legacy.get("quote_count"),
                view_count=view_count,
                bookmark_count=legacy.get("bookmark_count"),
                # User profile
                user_blue_verified=user_results.get("is_blue_verified"),
                user_description=user_legacy.get("description") or None,
                user_location=user_legacy.get("location") or None,
                profile_image_url=user_legacy.get("profile_image_url_https") or None,
                cover_picture_url=user_legacy.get("profile_banner_url") or None,
                user_followers_count=user_legacy.get("followers_count"),
                user_following_count=user_legacy.get("friends_count"),
                scraped_at=dt.datetime.now(dt.timezone.utc),
            )
        except Exception:
            bt.logging.warning(f"Failed to parse tweet: {traceback.format_exc()}")
            return None

    async def _search_with_retry(self, query: str, count: int = 20) -> list:
        """Search with retry on rate limit."""
        for attempt in range(self.MAX_RETRIES):
            try:
                return await self._search_tweets(query, count)
            except RateLimitError:
                wait = 15 * (attempt + 1)
                bt.logging.warning(
                    f"Rate limited on attempt {attempt + 1}, waiting {wait}s"
                )
                await asyncio.sleep(wait)
            except Exception:
                bt.logging.error(
                    f"Search failed: {traceback.format_exc()}"
                )
                return []
        return []

    async def _get_tweet_with_retry(self, tweet_id: str) -> Optional[dict]:
        """Get tweet by ID with retry."""
        for attempt in range(self.MAX_RETRIES):
            try:
                return await self._get_tweet_detail(tweet_id)
            except RateLimitError:
                wait = 15 * (attempt + 1)
                bt.logging.warning(
                    f"Rate limited fetching tweet {tweet_id}, waiting {wait}s"
                )
                await asyncio.sleep(wait)
            except Exception:
                bt.logging.error(
                    f"Get tweet failed: {traceback.format_exc()}"
                )
                return None
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
                tweet_id = entity.uri.rstrip("/").split("/")[-1].split("?")[0]
                tweet_data = await self._get_tweet_with_retry(tweet_id)

                if not tweet_data:
                    return ValidationResult(
                        is_valid=False,
                        reason="Tweet not found.",
                        content_size_bytes_validated=entity.content_size_bytes,
                    )

                actual_content = self._parse_raw_tweet_to_xcontent(tweet_data)
                if not actual_content:
                    return ValidationResult(
                        is_valid=False,
                        reason="Failed to parse tweet.",
                        content_size_bytes_validated=entity.content_size_bytes,
                    )

                legacy = tweet_data.get("legacy", {})
                user_legacy = tweet_data.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})

                author_data = {
                    "followers": user_legacy.get("followers_count", 0),
                    "createdAt": user_legacy.get("created_at"),
                }
                views = tweet_data.get("views", {})
                view_count = self._safe_int(views.get("count")) or 0
                is_retweet = legacy.get("retweeted", False)

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

        raw_tweets = await self._search_with_retry(query, min(max_items, 20))

        data_entities = []
        for tweet_data in raw_tweets[:max_items]:
            x_content = self._parse_raw_tweet_to_xcontent(tweet_data)
            if x_content is None:
                continue

            if not allow_low_engagement:
                user_legacy = tweet_data.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
                author_data = {"followers": user_legacy.get("followers_count", 0)}
                if utils.is_spam_account(author_data):
                    continue
                views = tweet_data.get("views", {})
                view_count = self._safe_int(views.get("count")) or 0
                if utils.is_low_engagement_tweet({"viewCount": view_count}):
                    continue

            data_entities.append(XContent.to_data_entity(content=x_content))

        bt.logging.success(
            f"Completed X scrape for {query}. Scraped {len(data_entities)} items."
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
                    f"Failed to fetch tweet {url}: {traceback.format_exc()}"
                )
                return []

        # Return empty if no params
        if all(
            param is None
            for param in [usernames, keywords, start_datetime, end_datetime]
        ):
            return []

        bt.logging.info(
            f"On-demand X scrape: usernames={usernames}, "
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

        bt.logging.success(f"On-demand X scrape for: {query}")

        raw_tweets = await self._search_with_retry(query, min(limit, 20))

        data_entities = []
        for tweet_data in raw_tweets[:limit]:
            x_content = self._parse_raw_tweet_to_xcontent(tweet_data)
            if x_content is None:
                continue
            data_entities.append(XContent.to_data_entity(content=x_content))

        bt.logging.success(
            f"On-demand X scrape completed. Found {len(data_entities)} items."
        )
        return data_entities


class RateLimitError(Exception):
    pass


class APIError(Exception):
    pass
