"""
Export a full twikit session (cookies + homepage cache) from local machine.
This avoids Cloudflare blocks on the server.

Usage:
    python3 patches/twikit_export_session.py

Generates:
    twikit_cookies.json   - auth cookies
    twikit_homepage.html  - cached x.com homepage (for ClientTransaction)

Copy both files to your server's project root.
"""

import asyncio
import json
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


async def main():
    try:
        from twikit import Client
    except ImportError:
        print("ERROR: pip install twikit==2.3.3")
        sys.exit(1)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cookies_file = os.path.join(project_root, "twikit_cookies.json")
    homepage_file = os.path.join(project_root, "twikit_homepage.html")

    client = Client("en-US")

    # Try loading existing cookies first
    if os.path.exists(cookies_file):
        print("Loading existing cookies...")
        client.load_cookies(cookies_file)
    else:
        username = os.getenv("X_USERNAME")
        email = os.getenv("X_EMAIL")
        password = os.getenv("X_PASSWORD")

        if not all([username, email, password]):
            print("No cookies file found and no credentials in env.")
            print("Set X_USERNAME, X_EMAIL, X_PASSWORD or create twikit_cookies.json")
            sys.exit(1)

        print(f"Logging in as @{username}...")
        await client.login(
            auth_info_1=username,
            auth_info_2=email,
            password=password,
        )
        client.save_cookies(cookies_file)
        print(f"Cookies saved: {cookies_file}")

    # Now fetch x.com homepage (for ClientTransaction init)
    print("Fetching x.com homepage...")
    from httpx import AsyncClient as HttpxClient
    headers = {
        'Accept-Language': 'en-US,en;q=0.9',
        'Cache-Control': 'no-cache',
        'Referer': 'https://x.com',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
    }

    response = await client.http.request("GET", "https://x.com", headers=headers)
    html = response.text

    if "Cloudflare" in html and "blocked" in html.lower():
        print("ERROR: Cloudflare blocked even locally!")
        sys.exit(1)

    with open(homepage_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Homepage saved: {homepage_file}")
    print(f"\nCopy both files to your server:")
    print(f"  scp {cookies_file} {homepage_file} moc@<server>:/home/moc/ciphervybe/bittensor/moc-data-universe/")
    print(f"\nThen restart: sudo systemctl restart sn13-miner")


if __name__ == "__main__":
    asyncio.run(main())
