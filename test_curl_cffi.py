import subprocess, json

cookies = json.load(open('twikit_cookies.json'))
cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items() if k in ['auth_token', 'ct0', 'twid', 'guest_id', 'personalization_id', 'lang'])

# Test with actual curl command - exactly mimicking browser
cmd = [
    "curl", "-s", "-w", "\n%{http_code}",
    "-H", "accept: */*",
    "-H", "authorization: Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
    "-H", f"x-csrf-token: {cookies['ct0']}",
    "-H", "x-twitter-auth-type: OAuth2Session",
    "-H", "x-twitter-active-user: yes",
    "-H", "x-twitter-client-language: en",
    "-H", f"cookie: {cookie_str}",
    "https://x.com/i/api/1.1/search/tweets.json?q=bitcoin&count=5&tweet_mode=extended&result_type=recent"
]

print("=== curl v1.1 search ===")
result = subprocess.run(cmd, capture_output=True, text=True)
lines = result.stdout.strip().rsplit("\n", 1)
body = lines[0] if len(lines) > 1 else ""
status = lines[-1]
print(f"Status: {status}, Body: {len(body)} bytes")
if body:
    print(body[:500])
else:
    print("(empty body)")

# Test GraphQL with curl
print("\n=== curl GraphQL search ===")
import urllib.parse
variables = json.dumps({"rawQuery":"bitcoin","count":5,"querySource":"typed_query","product":"Latest"}, separators=(",",":"))
features = json.dumps({"rweb_video_screen_enabled":False,"profile_label_improvements_pcf_label_in_post_enabled":True,"responsive_web_graphql_timeline_navigation_enabled":True,"responsive_web_graphql_skip_user_profile_image_extensions_enabled":False,"creator_subscriptions_tweet_preview_api_enabled":True,"communities_web_enable_tweet_community_results_fetch":True,"c9s_tweet_anatomy_moderator_badge_enabled":True,"articles_preview_enabled":True,"responsive_web_edit_tweet_api_enabled":True,"graphql_is_translatable_rweb_tweet_is_translatable_enabled":True,"view_counts_everywhere_api_enabled":True,"longform_notetweets_consumption_enabled":True,"responsive_web_twitter_article_tweet_consumption_enabled":True,"freedom_of_speech_not_reach_fetch_enabled":True,"standardized_nudges_misinfo":True,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled":True,"longform_notetweets_rich_text_read_enabled":True,"responsive_web_enhance_cards_enabled":False}, separators=(",",":"))
params = urllib.parse.urlencode({"variables": variables, "features": features})
gql_url = f"https://x.com/i/api/graphql/GcXk9vN_d1jUfHNqLacXQA/SearchTimeline?{params}"

cmd2 = [
    "curl", "-s", "-w", "\n%{http_code}",
    "-H", "accept: */*",
    "-H", "authorization: Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
    "-H", f"x-csrf-token: {cookies['ct0']}",
    "-H", "x-twitter-auth-type: OAuth2Session",
    "-H", "x-twitter-active-user: yes",
    "-H", "x-twitter-client-language: en",
    "-H", f"cookie: {cookie_str}",
    gql_url
]

result2 = subprocess.run(cmd2, capture_output=True, text=True)
lines2 = result2.stdout.strip().rsplit("\n", 1)
body2 = lines2[0] if len(lines2) > 1 else ""
status2 = lines2[-1]
print(f"Status: {status2}, Body: {len(body2)} bytes")
if body2:
    print(body2[:500])
else:
    print("(empty body)")
