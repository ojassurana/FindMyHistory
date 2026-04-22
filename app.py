"""
FindMyHistory — Live iCloud multi-device location tracker with historical playback.
"""

import asyncio
import json
import math
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudFailedLoginException
from supabase import create_client

load_dotenv()

# --- Supabase ---
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

# --- Global state ---
icloud_api = None
tracked_devices = {}  # {device_id: icloud_device_object}
last_saved_locations = {}  # {device_id: {latitude, longitude}}
polling_task = None
POLL_INTERVAL = 5
MIN_DISTANCE_M = 20
COOKIE_DIR = tempfile.mkdtemp(prefix="icloud_cookies_")

# Device colors for map pins
DEVICE_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#ec4899", "#06b6d4", "#84cc16"]


def haversine(lat1, lon1, lat2, lon2):
    """Distance in meters between two lat/lng points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def restore_cookies_from_db(apple_id):
    """Restore pyicloud cookies from Supabase to the temp cookie directory."""
    result = (
        supabase.table("icloud_session")
        .select("cookie_data")
        .eq("apple_id", apple_id)
        .eq("is_active", True)
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data and result.data[0].get("cookie_data"):
        cookie_data = result.data[0]["cookie_data"]
        for filename, content in cookie_data.items():
            filepath = Path(COOKIE_DIR) / filename
            filepath.write_text(content if isinstance(content, str) else json.dumps(content))
        return True
    return False


def save_cookies_to_db(apple_id):
    """Save pyicloud cookies from disk to Supabase."""
    cookie_data = {}
    cookie_path = Path(COOKIE_DIR)
    for f in cookie_path.iterdir():
        if f.is_file():
            cookie_data[f.name] = f.read_text()

    existing = (
        supabase.table("icloud_session")
        .select("id")
        .eq("apple_id", apple_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    now = datetime.now(timezone.utc).isoformat()
    if existing.data:
        supabase.table("icloud_session").update(
            {"cookie_data": cookie_data, "updated_at": now}
        ).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase.table("icloud_session").insert(
            {"apple_id": apple_id, "cookie_data": cookie_data, "is_active": True}
        ).execute()


def get_all_tracked_devices_from_db():
    """Get all tracked devices from Supabase."""
    result = supabase.table("tracked_device").select("*").order("created_at", desc=False).execute()
    return result.data


def save_tracked_device_to_db(device_id, device_name, device_model):
    """Save a device to the tracked list in Supabase."""
    # Check if already tracked
    existing = supabase.table("tracked_device").select("id").eq("device_id", device_id).execute()
    if existing.data:
        return existing.data[0]
    result = supabase.table("tracked_device").insert(
        {"device_id": device_id, "device_name": device_name, "device_model": device_model}
    ).execute()
    return result.data[0] if result.data else None


def remove_tracked_device_from_db(device_id):
    """Remove a device from the tracked list (keeps history)."""
    supabase.table("tracked_device").delete().eq("device_id", device_id).execute()


def save_location(device_id, location):
    """Save a location point to Supabase."""
    supabase.table("location_history").insert(
        {
            "device_id": device_id,
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "accuracy": location.get("horizontalAccuracy"),
            "position_type": location.get("positionType"),
            "altitude": location.get("altitude"),
            "icloud_timestamp": location.get("timeStamp"),
        }
    ).execute()


def find_icloud_device_by_id(device_id):
    """Find an iCloud device object by its ID."""
    if not icloud_api:
        return None
    for device in icloud_api.devices:
        if device.data.get("id", "") == device_id:
            return device
    return None


async def poll_location():
    """Background task: poll iCloud for all tracked devices every POLL_INTERVAL seconds."""
    global icloud_api, tracked_devices

    print(f"[poll] Background poll task started")
    while True:
        try:
            if not icloud_api:
                print(f"[poll] No iCloud API, skipping")
            if icloud_api:
                # Check if session is still valid
                if icloud_api.requires_2fa or icloud_api.requires_2sa:
                    icloud_api = None
                    tracked_devices = {}
                    continue

                # Sync tracked_devices from DB in case new ones were added
                db_devices = get_all_tracked_devices_from_db()
                print(f"[poll] DB has {len(db_devices)} devices, memory has {len(tracked_devices)}")
                for db_dev in db_devices:
                    did = db_dev["device_id"]
                    if did not in tracked_devices:
                        icloud_dev = find_icloud_device_by_id(did)
                        if icloud_dev:
                            tracked_devices[did] = icloud_dev
                            print(f"[poll] Started tracking: {db_dev['device_name']}")
                        else:
                            print(f"[poll] Could not find iCloud device for: {db_dev['device_name']}")

                # Remove devices no longer in DB
                db_ids = {d["device_id"] for d in db_devices}
                for did in list(tracked_devices.keys()):
                    if did not in db_ids:
                        tracked_devices.pop(did, None)
                        last_saved_locations.pop(did, None)

                # Refresh all device data from iCloud (blocking call, run in thread)
                try:
                    await asyncio.to_thread(icloud_api.devices.refresh)
                except Exception as e:
                    print(f"[poll] Refresh failed: {e}")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # Re-resolve device references after refresh (refresh creates new objects)
                for did in list(tracked_devices.keys()):
                    fresh = find_icloud_device_by_id(did)
                    if fresh:
                        tracked_devices[did] = fresh

                # Poll each tracked device
                for device_id, device in list(tracked_devices.items()):
                    try:
                        location = device.location
                        if location and location.get("latitude"):
                            should_save = True
                            last = last_saved_locations.get(device_id)
                            if last:
                                dist = haversine(
                                    last["latitude"], last["longitude"],
                                    location["latitude"], location["longitude"],
                                )
                                if dist < MIN_DISTANCE_M:
                                    should_save = False

                            if should_save:
                                save_location(device_id, location)
                                last_saved_locations[device_id] = {
                                    "latitude": location["latitude"],
                                    "longitude": location["longitude"],
                                }
                                print(f"[poll] Saved: {location['latitude']:.4f},{location['longitude']:.4f}")
                            # else: not moved enough, skip silently
                        else:
                            print(f"[poll] No location for {device_id[:20]}")
                    except Exception as e:
                        print(f"[poll] Error polling {device_id[:20]}...: {e}")
        except Exception as e:
            print(f"[poll] Error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background polling on app startup."""
    global polling_task, icloud_api, tracked_devices

    # Try to restore session from DB
    try:
        session = (
            supabase.table("icloud_session")
            .select("apple_id")
            .eq("is_active", True)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if session.data:
            apple_id = session.data[0]["apple_id"]
            if restore_cookies_from_db(apple_id):
                api = PyiCloudService(apple_id, cookie_directory=COOKIE_DIR)
                if not api.requires_2fa and not api.requires_2sa:
                    icloud_api = api
                    # Restore all tracked devices
                    db_devices = get_all_tracked_devices_from_db()
                    for db_dev in db_devices:
                        icloud_dev = find_icloud_device_by_id(db_dev["device_id"])
                        if icloud_dev:
                            tracked_devices[db_dev["device_id"]] = icloud_dev
                    print(f"[startup] Restored session for {apple_id}, tracking {len(tracked_devices)} devices")
    except Exception as e:
        print(f"[startup] Could not restore session: {e}")

    # Load last saved locations from DB for all tracked devices
    try:
        for device_id in tracked_devices:
            last_loc = (
                supabase.table("location_history")
                .select("latitude,longitude")
                .eq("device_id", device_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if last_loc.data:
                last_saved_locations[device_id] = last_loc.data[0]
    except Exception as e:
        print(f"[startup] Could not load last saved locations: {e}")

    polling_task = asyncio.create_task(poll_location())
    yield
    polling_task.cancel()


app = FastAPI(lifespan=lifespan)


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect HTTP to HTTPS on Heroku (checks X-Forwarded-Proto header)."""
    async def dispatch(self, request: Request, call_next):
        if request.headers.get("x-forwarded-proto") == "http":
            url = request.url.replace(scheme="https")
            return RedirectResponse(url=str(url), status_code=301)
        return await call_next(request)


app.add_middleware(HTTPSRedirectMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- Routes ---


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/status")
async def api_status():
    """Check if there's an active session and tracked devices."""
    if icloud_api and not icloud_api.requires_2fa and not icloud_api.requires_2sa:
        db_devices = get_all_tracked_devices_from_db()
        return {
            "authenticated": True,
            "has_devices": len(db_devices) > 0,
            "devices": db_devices,
        }
    return {"authenticated": False, "has_devices": False, "devices": []}


@app.post("/api/login")
async def api_login(request: Request):
    """Step 1: Apple ID + password login."""
    global icloud_api
    body = await request.json()
    apple_id = body.get("apple_id", "").strip()
    password = body.get("password", "").strip()

    if not apple_id or not password:
        return JSONResponse({"error": "Apple ID and password are required."}, status_code=400)

    try:
        api = PyiCloudService(apple_id, password, cookie_directory=COOKIE_DIR)
    except PyiCloudFailedLoginException:
        return JSONResponse({"error": "Invalid email/password combination."}, status_code=401)
    except Exception as e:
        error_msg = str(e)
        if "locked" in error_msg.lower():
            return JSONResponse(
                {"error": "This Apple Account has been locked. Visit https://iforgot.apple.com to unlock."},
                status_code=403,
            )
        return JSONResponse({"error": f"Login failed: {error_msg}"}, status_code=500)

    icloud_api = api
    save_cookies_to_db(apple_id)

    if api.requires_2fa:
        return {"status": "2fa_required"}
    elif api.requires_2sa:
        return {"status": "2sa_required"}
    else:
        return {"status": "authenticated"}


@app.post("/api/verify-2fa")
async def api_verify_2fa(request: Request):
    """Step 2: Verify 2FA code."""
    global icloud_api
    if not icloud_api:
        return JSONResponse({"error": "No active login session."}, status_code=400)

    body = await request.json()
    code = body.get("code", "").strip()

    if not code:
        return JSONResponse({"error": "2FA code is required."}, status_code=400)

    result = icloud_api.validate_2fa_code(code)
    if result:
        # Trust the session so Apple doesn't keep asking for 2FA
        # This is critical to avoid account lockouts
        if not icloud_api.is_trusted_session:
            icloud_api.trust_session()

        session = (
            supabase.table("icloud_session")
            .select("apple_id")
            .eq("is_active", True)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if session.data:
            save_cookies_to_db(session.data[0]["apple_id"])
        return {"status": "authenticated"}
    else:
        return JSONResponse({"error": "Invalid 2FA code."}, status_code=401)


@app.get("/api/devices")
async def api_devices():
    """List all available iCloud devices that have a location."""
    if not icloud_api:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)

    # Get already tracked device IDs
    db_devices = get_all_tracked_devices_from_db()
    tracked_ids = {d["device_id"] for d in db_devices}

    devices = []
    for i, device in enumerate(icloud_api.devices):
        status = device.status()
        location = device.location
        device_id = device.data.get("id", "")

        # Only show devices with a location
        if not location or not location.get("latitude"):
            continue

        # Skip already tracked devices
        if device_id in tracked_ids:
            continue

        devices.append(
            {
                "index": i,
                "id": device_id,
                "name": status.get("name", f"Device {i}"),
                "model": status.get("deviceDisplayName", "Unknown"),
                "battery": status.get("batteryLevel"),
                "latitude": round(location["latitude"], 4),
                "longitude": round(location["longitude"], 4),
            }
        )
    return {"devices": devices}


@app.post("/api/add-device")
async def api_add_device(request: Request):
    """Add a device to the tracked list."""
    global tracked_devices
    if not icloud_api:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)

    body = await request.json()
    device_index = body.get("index")

    devices_list = list(icloud_api.devices)
    if device_index is None or device_index < 0 or device_index >= len(devices_list):
        return JSONResponse({"error": "Invalid device index."}, status_code=400)

    device = devices_list[device_index]
    device_id = device.data.get("id", "")
    status = device.status()
    device_name = status.get("name", "Unknown")
    device_model = status.get("deviceDisplayName", "Unknown")

    # Add to DB and memory
    save_tracked_device_to_db(device_id, device_name, device_model)
    tracked_devices[device_id] = device
    last_saved_locations.pop(device_id, None)

    return {"status": "ok", "device": {"device_id": device_id, "name": device_name, "model": device_model}}


@app.post("/api/remove-device")
async def api_remove_device(request: Request):
    """Remove a device from tracking (keeps history)."""
    global tracked_devices
    body = await request.json()
    device_id = body.get("device_id")

    if not device_id:
        return JSONResponse({"error": "device_id is required."}, status_code=400)

    remove_tracked_device_from_db(device_id)
    tracked_devices.pop(device_id, None)
    last_saved_locations.pop(device_id, None)

    return {"status": "ok"}


@app.get("/api/tracked-devices")
async def api_tracked_devices():
    """Get all currently tracked devices with their colors."""
    db_devices = get_all_tracked_devices_from_db()
    result = []
    for i, dev in enumerate(db_devices):
        result.append({
            "device_id": dev["device_id"],
            "device_name": dev["device_name"],
            "device_model": dev["device_model"],
            "color": DEVICE_COLORS[i % len(DEVICE_COLORS)],
        })
    return {"devices": result}


@app.get("/api/locations")
async def api_locations():
    """Get current live locations for all tracked devices."""
    if not icloud_api:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)

    if icloud_api.requires_2fa or icloud_api.requires_2sa:
        return JSONResponse({"error": "Session expired."}, status_code=401)

    db_devices = get_all_tracked_devices_from_db()
    locations = []

    for i, db_dev in enumerate(db_devices):
        device = tracked_devices.get(db_dev["device_id"])
        if not device:
            continue

        try:
            location = device.location
            if location and location.get("latitude"):
                status = device.status()
                locations.append({
                    "device_id": db_dev["device_id"],
                    "device_name": db_dev["device_name"],
                    "color": DEVICE_COLORS[i % len(DEVICE_COLORS)],
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "accuracy": location.get("horizontalAccuracy"),
                    "position_type": location.get("positionType"),
                    "timestamp": location.get("timeStamp"),
                    "is_old": location.get("isOld"),
                    "battery": status.get("batteryLevel"),
                })
        except Exception as e:
            print(f"[location] Error for {db_dev['device_name']}: {e}")

    return {"locations": locations}


@app.get("/api/history/dates/{device_id:path}")
async def api_history_dates(device_id: str):
    """Get all dates that have location history for a specific device."""
    result = (
        supabase.table("location_history")
        .select("latitude,longitude,created_at")
        .eq("device_id", device_id)
        .order("created_at", desc=False)
        .execute()
    )

    date_stats = {}
    for row in result.data:
        dt = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        date_key = dt.strftime("%Y-%m-%d")
        if date_key not in date_stats:
            date_stats[date_key] = {"points": [], "count": 0, "distance": 0}
        entry = date_stats[date_key]
        if entry["points"]:
            prev = entry["points"][-1]
            entry["distance"] += haversine(prev[0], prev[1], row["latitude"], row["longitude"])
        entry["points"].append((row["latitude"], row["longitude"]))
        entry["count"] += 1

    dates = []
    for date_key in sorted(date_stats.keys(), reverse=True):
        stats = date_stats[date_key]
        dates.append({
            "date": date_key,
            "points": stats["count"],
            "distance_m": round(stats["distance"], 1),
        })

    return {"dates": dates}


@app.get("/api/history/{device_id:path}/{date}")
async def api_history_for_date(device_id: str, date: str):
    """Get all location points for a specific device on a specific date."""
    start = f"{date}T00:00:00+00:00"
    end = f"{date}T23:59:59+00:00"

    result = (
        supabase.table("location_history")
        .select("*")
        .eq("device_id", device_id)
        .gte("created_at", start)
        .lte("created_at", end)
        .order("created_at", desc=False)
        .execute()
    )

    points = []
    total_distance = 0
    prev = None
    for row in result.data:
        point = {
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "accuracy": row["accuracy"],
            "position_type": row["position_type"],
            "timestamp": row["icloud_timestamp"],
            "created_at": row["created_at"],
        }
        if prev:
            total_distance += haversine(prev["latitude"], prev["longitude"], point["latitude"], point["longitude"])
        prev = point
        points.append(point)

    return {"points": points, "total_distance_m": round(total_distance, 1)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
