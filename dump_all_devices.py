"""
Dump all device data — status, location, and raw data dict — for every device.
Uses cached session cookies. No interactive prompts.
"""

import json
from pathlib import Path
from pyicloud import PyiCloudService

COOKIE_DIR = Path(__file__).parent / ".icloud_cookies"
SESSION_FILE = Path(__file__).parent / ".icloud_session.json"

def main():
    session_data = json.loads(SESSION_FILE.read_text())
    apple_id = session_data["apple_id"]

    print(f"Connecting as {apple_id}...\n")
    api = PyiCloudService(apple_id, cookie_directory=str(COOKIE_DIR))

    if api.requires_2fa or api.requires_2sa:
        print("ERROR: Session expired. Run login_test.py first to re-authenticate.")
        return

    print(f"Found {len(api.devices)} devices.\n")
    print("=" * 80)

    for i, device in enumerate(api.devices):
        print(f"\n{'=' * 80}")
        print(f"DEVICE [{i}]")
        print(f"{'=' * 80}")

        # Raw data dict
        print(f"\n--- device.data ---")
        print(json.dumps(device.data, indent=2, default=str))

        # Status
        print(f"\n--- device.status() ---")
        print(json.dumps(device.status(), indent=2, default=str))

        # Location (it's a property, not a method)
        print(f"\n--- device.location ---")
        loc = device.location
        print(json.dumps(loc, indent=2, default=str) if loc else "No location available.")

        print()


if __name__ == "__main__":
    main()
