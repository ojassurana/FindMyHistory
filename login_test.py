"""
Simple CLI script to test the pyicloud login flow.
Automatically targets "Ojas's 17 Pro Max" and continuously prints location.

Stores the Apple ID and cookies locally so repeat runs are fully automatic.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from pyicloud import PyiCloudService

COOKIE_DIR = Path(__file__).parent / ".icloud_cookies"
SESSION_FILE = Path(__file__).parent / ".icloud_session.json"
TARGET_DEVICE = "17 Pro Max"
POLL_INTERVAL = 5


def load_saved_session():
    if SESSION_FILE.exists():
        data = json.loads(SESSION_FILE.read_text())
        return data.get("apple_id")
    return None


def save_session(apple_id):
    SESSION_FILE.write_text(json.dumps({"apple_id": apple_id}))


def find_device(api):
    for device in api.devices:
        if TARGET_DEVICE in device.status().get("name", ""):
            return device
    return None


def main():
    print("=== iCloud Location Tracker ===\n")

    saved_apple_id = load_saved_session()
    api = None

    # Try reusing cached session
    if saved_apple_id:
        print(f"Saved Apple ID: {saved_apple_id}")
        print("Checking cached session...")
        try:
            api = PyiCloudService(saved_apple_id, cookie_directory=str(COOKIE_DIR))
            if not api.requires_2fa and not api.requires_2sa:
                print("Valid session found.\n")
            else:
                print("Session expired.")
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
        code = input("Enter the 2FA code: ").strip()
        result = api.validate_2fa_code(code)
        print(f"2FA: {'Success' if result else 'Failed'}")
        if not result:
            return

    elif api.requires_2sa:
        print("\n2-step verification required.")
        devices = api.trusted_devices
        for i, device in enumerate(devices):
            print(f"  [{i}] {device.get('deviceName', f'Device {i}')}")
        idx = int(input("Device to receive code: ").strip())
        device = devices[idx]
        if not api.send_verification_code(device):
            print("Failed to send code.")
            return
        code = input("Verification code: ").strip()
        if not api.validate_verification_code(device, code):
            print("Verification failed.")
            return

    print("Logged in.\n")

    # Find the target device
    device = find_device(api)
    if not device:
        print(f"ERROR: Could not find device matching '{TARGET_DEVICE}'")
        print("Available devices:")
        for d in api.devices:
            print(f"  - {d.status().get('name', 'Unknown')}")
        return

    status = device.status()
    print(f"Tracking: {status.get('name')} ({status.get('deviceDisplayName')})")
    print(f"Polling every {POLL_INTERVAL}s. Ctrl+C to stop.\n")

    # Continuous location polling
    try:
        while True:
            location = device.location
            now = datetime.now().strftime("%H:%M:%S")
            if location:
                ts = datetime.fromtimestamp(location["timeStamp"] / 1000).strftime("%H:%M:%S")
                print(
                    f"[{now}] "
                    f"lat={location['latitude']:.6f}  "
                    f"lng={location['longitude']:.6f}  "
                    f"acc={location.get('horizontalAccuracy', '?')}m  "
                    f"type={location.get('positionType', '?')}  "
                    f"device_ts={ts}  "
                    f"old={location.get('isOld', '?')}"
                )
            else:
                print(f"[{now}] No location available.")
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
