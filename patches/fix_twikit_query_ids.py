"""
Patches twikit 2.3.3 with current Twitter GraphQL query IDs and features.
Twitter rotates these periodically, causing 404 errors.

Run on the server after pip install twikit:
    python patches/fix_twikit_query_ids.py

Last updated: 2026-03-28
"""

import os
import sys


def find_twikit_dir():
    try:
        import twikit
        return os.path.dirname(twikit.__file__)
    except ImportError:
        pass
    for path in sys.path:
        candidate = os.path.join(path, "twikit")
        if os.path.isdir(candidate):
            return candidate
    return None


# Current query IDs as of 2026-03-28
QUERY_ID_UPDATES = {
    "flaR-PUMshxFWZWPNpq4zA": "GcXk9vN_d1jUfHNqLacXQA",  # SearchTimeline
    "U0HTv-bAWTBYylwEMT7x5A": "CysGzLIZa76UzZ3WTe-Bhg",  # TweetDetail
}

# Current features as of 2026-03-28
NEW_FEATURES = """\
FEATURES = {
    'rweb_video_screen_enabled': False,
    'profile_label_improvements_pcf_label_in_post_enabled': True,
    'responsive_web_profile_redirect_enabled': False,
    'rweb_tipjar_consumption_enabled': False,
    'verified_phone_label_enabled': True,
    'creator_subscriptions_tweet_preview_api_enabled': True,
    'responsive_web_graphql_timeline_navigation_enabled': True,
    'responsive_web_graphql_skip_user_profile_image_extensions_enabled': False,
    'premium_content_api_read_enabled': False,
    'communities_web_enable_tweet_community_results_fetch': True,
    'c9s_tweet_anatomy_moderator_badge_enabled': True,
    'responsive_web_grok_analyze_button_fetch_trends_enabled': False,
    'responsive_web_grok_analyze_post_followups_enabled': True,
    'responsive_web_jetfuel_frame': True,
    'responsive_web_grok_share_attachment_enabled': True,
    'responsive_web_grok_annotations_enabled': True,
    'articles_preview_enabled': True,
    'responsive_web_edit_tweet_api_enabled': True,
    'graphql_is_translatable_rweb_tweet_is_translatable_enabled': True,
    'view_counts_everywhere_api_enabled': True,
    'longform_notetweets_consumption_enabled': True,
    'responsive_web_twitter_article_tweet_consumption_enabled': True,
    'content_disclosure_indicator_enabled': True,
    'content_disclosure_ai_generated_indicator_enabled': True,
    'responsive_web_grok_show_grok_translated_post': False,
    'responsive_web_grok_analysis_button_from_backend': True,
    'post_ctas_fetch_enabled': True,
    'freedom_of_speech_not_reach_fetch_enabled': True,
    'standardized_nudges_misinfo': True,
    'tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled': True,
    'longform_notetweets_rich_text_read_enabled': True,
    'longform_notetweets_inline_media_enabled': False,
    'responsive_web_grok_image_annotation_enabled': True,
    'responsive_web_grok_imagine_annotation_enabled': True,
    'responsive_web_grok_community_note_auto_translation_is_enabled': False,
    'responsive_web_enhance_cards_enabled': False
}
"""


def main():
    twikit_dir = find_twikit_dir()
    if not twikit_dir:
        print("ERROR: Could not find twikit installation")
        sys.exit(1)

    print(f"Found twikit at: {twikit_dir}")

    # Patch gql.py - update query IDs
    gql_path = os.path.join(twikit_dir, "client", "gql.py")
    if os.path.exists(gql_path):
        with open(gql_path, "r") as f:
            content = f.read()

        patched = content
        for old_id, new_id in QUERY_ID_UPDATES.items():
            if old_id in patched:
                patched = patched.replace(old_id, new_id)
                print(f"  Updated query ID: {old_id} -> {new_id}")
            else:
                print(f"  Query ID {old_id} not found (may already be updated)")

        if patched != content:
            backup = gql_path + ".bak"
            with open(backup, "w") as f:
                f.write(content)
            with open(gql_path, "w") as f:
                f.write(patched)
            print(f"  Backed up to: {backup}")
            print(f"  Patched: {gql_path}")
        else:
            print("  No changes needed for gql.py")

    # Patch constants.py - update FEATURES dict
    constants_path = os.path.join(twikit_dir, "constants.py")
    if os.path.exists(constants_path):
        with open(constants_path, "r") as f:
            content = f.read()

        # Find and replace the FEATURES dict
        import re
        pattern = r"^FEATURES = \{[^}]+\}"
        match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
        if match:
            backup = constants_path + ".bak"
            with open(backup, "w") as f:
                f.write(content)

            patched = content[:match.start()] + NEW_FEATURES.strip() + content[match.end():]
            with open(constants_path, "w") as f:
                f.write(patched)
            print(f"  Updated FEATURES dict in constants.py")
            print(f"  Backed up to: {backup}")
        else:
            print("  Could not find FEATURES dict in constants.py")

    print("\nDone! Restart the miner to apply changes.")


if __name__ == "__main__":
    main()
