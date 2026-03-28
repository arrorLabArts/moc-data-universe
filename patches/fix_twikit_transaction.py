"""
Patches twikit 2.3.3 transaction.py to fix the handle_x_migration error.

Twitter/X changed their webpack chunk format around March 18, 2026.
The old regex patterns in twikit no longer match the new format.

Run this script once after installing twikit:
    python patches/fix_twikit_transaction.py

See: https://github.com/d60/twikit/issues/408
"""

import importlib
import os
import sys


def find_transaction_py():
    """Find the twikit transaction.py file in site-packages."""
    try:
        import twikit
        pkg_dir = os.path.dirname(twikit.__file__)
        target = os.path.join(pkg_dir, "x_client_transaction", "transaction.py")
        if os.path.exists(target):
            return target
    except ImportError:
        pass

    # Fallback: search common locations
    for path in sys.path:
        candidate = os.path.join(path, "twikit", "x_client_transaction", "transaction.py")
        if os.path.exists(candidate):
            return candidate

    return None


OLD_ON_DEMAND = """ON_DEMAND_FILE_REGEX = re.compile(
    r\"\"\"['|\\\"]{1}ondemand\\.s['|\\\"]{1}:\\s*['|\\\"]{1}([\\w]*)['|\\\"]{1}\"\"\", flags=(re.VERBOSE | re.MULTILINE))"""

NEW_ON_DEMAND = """ON_DEMAND_FILE_REGEX = re.compile(
    r\"\"\",(\d+):["']ondemand\\.s["']\"\"\", flags=(re.VERBOSE | re.MULTILINE))
ON_DEMAND_HASH_PATTERN = r',{}:\"([0-9a-f]+)\"'"""

OLD_INDICES = """INDICES_REGEX = re.compile(
    r\"\"\"(\\(\\w{1}\\[(\\d{1,2})\\],\\s*16\\))+\"\"\", flags=(re.VERBOSE | re.MULTILINE))"""

NEW_INDICES = """INDICES_REGEX = re.compile(
    r\"\"\"\\[(\\d+)\\],\\s*16\"\"\", flags=(re.VERBOSE | re.MULTILINE))"""

OLD_GET_INDICES = '''    async def get_indices(self, home_page_response, session, headers):
        key_byte_indices = []
        response = self.validate_response(
            home_page_response) or self.home_page_response
        on_demand_file = ON_DEMAND_FILE_REGEX.search(str(response))
        if on_demand_file:
            on_demand_file_url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{on_demand_file.group(1)}a.js"
            on_demand_file_response = await session.request(method="GET", url=on_demand_file_url, headers=headers)
            key_byte_indices_match = INDICES_REGEX.finditer(
                str(on_demand_file_response.text))
            for item in key_byte_indices_match:
                key_byte_indices.append(item.group(2))
        if not key_byte_indices:
            raise Exception("Couldn't get KEY_BYTE indices")
        key_byte_indices = list(map(int, key_byte_indices))
        return key_byte_indices[0], key_byte_indices[1:]'''

NEW_GET_INDICES = '''    async def get_indices(self, home_page_response, session, headers):
        key_byte_indices = []
        response = self.validate_response(
            home_page_response) or self.home_page_response
        response_text = str(response)
        on_demand_file = ON_DEMAND_FILE_REGEX.search(response_text)
        if on_demand_file:
            chunk_index = on_demand_file.group(1)
            # Look up the hash for this chunk index
            hash_pattern = re.compile(ON_DEMAND_HASH_PATTERN.format(chunk_index))
            hash_match = hash_pattern.search(response_text)
            if hash_match:
                file_hash = hash_match.group(1)
            else:
                raise Exception("Couldn't find hash for ondemand.s chunk")
            on_demand_file_url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{file_hash}a.js"
            on_demand_file_response = await session.request(method="GET", url=on_demand_file_url, headers=headers)
            key_byte_indices_match = INDICES_REGEX.finditer(
                str(on_demand_file_response.text))
            for item in key_byte_indices_match:
                key_byte_indices.append(item.group(1))
        if not key_byte_indices:
            raise Exception("Couldn't get KEY_BYTE indices")
        key_byte_indices = list(map(int, key_byte_indices))
        return key_byte_indices[0], key_byte_indices[1:]'''


def main():
    path = find_transaction_py()
    if not path:
        print("ERROR: Could not find twikit transaction.py")
        sys.exit(1)

    print(f"Found: {path}")

    with open(path, "r") as f:
        content = f.read()

    if "ON_DEMAND_HASH_PATTERN" in content:
        print("Already patched!")
        return

    # Apply patches
    patched = content
    patched = patched.replace(OLD_ON_DEMAND, NEW_ON_DEMAND)
    patched = patched.replace(OLD_INDICES, NEW_INDICES)
    patched = patched.replace(OLD_GET_INDICES, NEW_GET_INDICES)

    if patched == content:
        print("WARNING: No replacements made - file may have unexpected format.")
        print("You may need to patch manually.")
        sys.exit(1)

    # Backup original
    backup = path + ".bak"
    with open(backup, "w") as f:
        f.write(content)
    print(f"Backup: {backup}")

    with open(path, "w") as f:
        f.write(patched)

    print("Patch applied successfully!")


if __name__ == "__main__":
    main()
