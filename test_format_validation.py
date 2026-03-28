"""Test: does our scraper output pass the validator's format and metadata checks?"""
import asyncio
import json
import sys
sys.path.insert(0, ".")

from scraping.x.twikit_scraper import TwikitTwitterScraper
from scraping.x.model import XContent
from common.data import DataEntity
from vali_utils.on_demand.output_models import validate_metadata_completeness


async def main():
    scraper = TwikitTwitterScraper()

    # Search for something that should return results
    query = "bitcoin"
    print(f"Searching for: {query}")
    raw_tweets = await scraper._search_with_retry(query, 5)
    print(f"Got {len(raw_tweets)} raw tweets\n")

    for i, tweet_data in enumerate(raw_tweets[:3]):
        print(f"--- Tweet {i+1} ---")
        x_content = scraper._parse_raw_tweet_to_xcontent(tweet_data)
        if x_content is None:
            print("  PARSE FAILED (returned None)")
            # Dump what we got
            rest_id = tweet_data.get("rest_id", "?")
            core = tweet_data.get("core", {})
            user_results = core.get("user_results", {}).get("result", {})
            user_core = user_results.get("core", {})
            user_legacy = user_results.get("legacy", {})
            print(f"  rest_id={rest_id}")
            print(f"  user_core keys: {list(user_core.keys())}")
            print(f"  user_legacy keys: {list(user_legacy.keys())}")
            print(f"  screen_name from core: {user_core.get('screen_name')}")
            print(f"  screen_name from legacy: {user_legacy.get('screen_name')}")
            continue

        entity = XContent.to_data_entity(content=x_content)
        print(f"  URL: {entity.uri}")
        print(f"  Source: {entity.source}")
        print(f"  Content size: {entity.content_size_bytes}")

        # Check metadata completeness (what validator checks)
        is_valid, missing = validate_metadata_completeness(entity)
        if is_valid:
            print(f"  Metadata: PASS")
        else:
            print(f"  Metadata: FAIL - missing: {missing}")

        # Check content can be parsed back
        try:
            parsed = XContent.from_data_entity(entity)
            print(f"  Round-trip parse: PASS")
            print(f"    username={parsed.username}")
            print(f"    user_id={parsed.user_id}")
            print(f"    tweet_id={parsed.tweet_id}")
            print(f"    user_verified={parsed.user_verified}")
            print(f"    user_blue_verified={parsed.user_blue_verified}")
            print(f"    view_count={parsed.view_count}")
            print(f"    bookmark_count={parsed.bookmark_count}")
            print(f"    conversation_id={parsed.conversation_id}")
            print(f"    language={parsed.language}")
            print(f"    followers={parsed.user_followers_count}")
            print(f"    following={parsed.user_following_count}")
        except Exception as e:
            print(f"  Round-trip parse: FAIL - {e}")

        # Show raw content JSON (first 500 chars)
        content_str = entity.content.decode("utf-8")
        print(f"  Content JSON preview: {content_str[:300]}...")
        print()

    await scraper._cleanup()


if __name__ == "__main__":
    asyncio.run(main())
