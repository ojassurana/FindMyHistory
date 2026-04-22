"""
Simple CLI script to test the pyicloud login flow.
Helps identify the full authentication behavior:
  1. Apple ID + password
  2. 2FA prompt (if enabled)
  3. Device selection
  4. Location fetch
"""

from pyicloud import PyiCloudService

def main():
    print("=== iCloud Login Test ===\n")

    apple_id = input("Apple ID (email): ").strip()
    password = input("Password: ").strip()

    print("\nConnecting to iCloud...")
    api = PyiCloudService(apple_id, password)

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

    print("\n=== Logged in successfully ===\n")

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
    device = list(devices.values())[idx]
    location = device.location()

    if location:
        print(f"\nLocation found:")
        print(f"  Latitude:  {location['latitude']}")
        print(f"  Longitude: {location['longitude']}")
        print(f"  Accuracy:  {location.get('horizontalAccuracy', 'N/A')}m")
        print(f"  Timestamp: {location.get('timeStamp', 'N/A')}")
    else:
        print("\nCould not retrieve location. Device may be offline.")


if __name__ == "__main__":
    main()
