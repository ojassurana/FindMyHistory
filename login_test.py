"""
Simple CLI script to test the pyicloud login flow.
Helps identify the full authentication behavior:
  1. Apple ID + password (skipped if valid session cookie exists)
  2. 2FA prompt (if enabled)
  3. Device selection
  4. Location fetch

Stores the Apple ID and cookies locally so repeat runs are fully automatic.
"""

import json
import os
from pathlib import Path
from pyicloud import PyiCloudService

COOKIE_DIR = Path(__file__).parent / ".icloud_cookies"
SESSION_FILE = Path(__file__).parent / ".icloud_session.json"


def load_saved_session():
    """Load the saved Apple ID from the session file."""
    if SESSION_FILE.exists():
        data = json.loads(SESSION_FILE.read_text())
        return data.get("apple_id")
    return None


def save_session(apple_id):
    """Save the Apple ID to the session file."""
    SESSION_FILE.write_text(json.dumps({"apple_id": apple_id}))


def main():
    print("=== iCloud Login Test ===\n")

    saved_apple_id = load_saved_session()
    api = None

    # Try reusing cached session with saved Apple ID
    if saved_apple_id:
        print(f"Saved Apple ID found: {saved_apple_id}")
        print("Checking for cached session...")
        try:
            api = PyiCloudService(saved_apple_id, cookie_directory=str(COOKIE_DIR))
            if not api.requires_2fa and not api.requires_2sa:
                print("Valid session — skipping login.\n")
            else:
                print("Session expired — need to re-authenticate.")
                api = None
        except Exception:
            print("Cached session invalid.")
            api = None

    # No valid session — ask for credentials
    if api is None:
        if not saved_apple_id:
            apple_id = input("Apple ID (email): ").strip()
        else:
            apple_id = saved_apple_id
        password = input("Password: ").strip()
        api = PyiCloudService(apple_id, password, cookie_directory=str(COOKIE_DIR))
        save_session(apple_id)

    # Handle 2FA
    if api.requires_2fa:
        print("\n2FA is required.")
        code = input("Enter the 2FA code sent to your device: ").strip()
        result = api.validate_2fa_code(code)
        print(f"2FA validation: {'Success' if result else 'Failed'}")
        if not result:
            print("Exiting.")
            return

    # Handle 2-step verification (older accounts)
    elif api.requires_2sa:
        print("\n2-step verification is required.")
        devices = api.trusted_devices
        for i, device in enumerate(devices):
            name = device.get("deviceName", f"Device {i}")
            print(f"  [{i}] {name}")
        idx = int(input("Choose a device to receive the code: ").strip())
        device = devices[idx]
        if not api.send_verification_code(device):
            print("Failed to send code.")
            return
        code = input("Enter the verification code: ").strip()
        if not api.validate_verification_code(device, code):
            print("Verification failed.")
            return
        print("Verification successful.")

    print("=== Logged in successfully ===\n")

    # List devices from Find My iPhone
    print("Devices available in Find My iPhone:")
    devices = api.devices
    for i, device in enumerate(devices):
        status = device.status()
        name = status.get("name", f"Unknown Device {i}")
        model = status.get("deviceDisplayName", "Unknown Model")
        print(f"  [{i}] {name} ({model})")

    # Pick a device and fetch location
    idx = int(input("\nChoose a device to locate: ").strip())
    device = devices[idx]

    print(f"\n=== Device status (full dump) ===")
    print(json.dumps(device.status(), indent=2, default=str))

    print(f"\n=== Location (full dump) ===")
    location = device.location()
    print(json.dumps(location, indent=2, default=str) if location else "No location available.")


if __name__ == "__main__":
    main()
