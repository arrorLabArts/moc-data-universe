"""
Login to X/Twitter locally and generate cookies file.
Then copy the cookies file to your production server.

Usage:
    python patches/twikit_login_local.py

Requires X_USERNAME, X_EMAIL, X_PASSWORD env vars or .env file.
Generates twikit_cookies.json in the project root.
"""

import asyncio
import os
import sys

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


async def main():
    try:
        from twikit import Client
    except ImportError:
        print("ERROR: twikit not installed. Run: pip install twikit==2.3.3")
        sys.exit(1)

    username = os.getenv("X_USERNAME")
    email = os.getenv("X_EMAIL")
    password = os.getenv("X_PASSWORD")

    if not all([username, email, password]):
        print("Set X_USERNAME, X_EMAIL, X_PASSWORD in .env or environment")
        sys.exit(1)

    cookies_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "twikit_cookies.json")

    print(f"Logging into X as @{username}...")
    client = Client("en-US")

    try:
        await client.login(
            auth_info_1=username,
            auth_info_2=email,
            password=password,
        )
    except Exception as e:
        print(f"Login failed: {e}")
        print("\nIf you get a Cloudflare error here too, try:")
        print("  1. Use a VPN or different network")
        print("  2. Export cookies from your browser instead (see below)")
        print("\nBrowser cookie export method:")
        print("  1. Login to x.com in your browser")
        print("  2. Install a cookie export extension (e.g., 'Get cookies.txt LOCALLY')")
        print("  3. Export cookies for x.com")
        print("  4. Use the twikit_import_browser_cookies.py script")
        sys.exit(1)

    client.save_cookies(cookies_file)
    print(f"\nCookies saved to: {cookies_file}")
    print(f"\nNow copy this file to your server:")
    print(f"  scp {cookies_file} moc@<server>:/home/moc/ciphervybe/bittensor/moc-data-universe/twikit_cookies.json")
    print(f"\nThen restart the miner:")
    print(f"  sudo systemctl restart sn13-miner")


if __name__ == "__main__":
    asyncio.run(main())
