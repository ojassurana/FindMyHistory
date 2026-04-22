"""
FindMyHistory — Live iCloud location tracker with historical playback.
"""

import asyncio
import json
import math
import os
import tempfile
import time
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
tracked_device = None
last_saved_location = None
polling_task = None
POLL_INTERVAL = 5
MIN_DISTANCE_M = 20
COOKIE_DIR = tempfile.mkdtemp(prefix="icloud_cookies_")


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


def get_tracked_device_from_db():
    """Get the currently tracked device from Supabase."""
    result = (
        supabase.table("tracked_device")
        .select("*")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def save_tracked_device_to_db(device_id, device_name, device_model):
    """Save the selected device to Supabase."""
    supabase.table("tracked_device").insert(
        {"device_id": device_id, "device_name": device_name, "device_model": device_model}
    ).execute()


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


async def poll_location():
    """Background task: poll iCloud for location every POLL_INTERVAL seconds."""
    global last_saved_location, icloud_api, tracked_device

    while True:
        try:
            if icloud_api and tracked_device:
                # Check if session is still valid
                if icloud_api.requires_2fa or icloud_api.requires_2sa:
                    icloud_api = None
                    tracked_device = None
                    continue

                location = tracked_device.location
                if location and location.get("latitude"):
                    # Check if moved enough to save
                    should_save = True
                    if last_saved_location:
                        dist = haversine(
                            last_saved_location["latitude"],
                            last_saved_location["longitude"],
                            location["latitude"],
                            location["longitude"],
                        )
                        if dist < MIN_DISTANCE_M:
                            should_save = False

                    if should_save:
                        db_device = get_tracked_device_from_db()
                        if db_device:
                            save_location(db_device["device_id"], location)
                            last_saved_location = location
        except Exception as e:
            print(f"[poll] Error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background polling on app startup."""
    global polling_task, icloud_api, tracked_device

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
                    # Restore tracked device
                    db_device = get_tracked_device_from_db()
                    if db_device:
                        for device in icloud_api.devices:
                            if db_device["device_id"] in device.data.get("id", ""):
                                tracked_device = device
                                break
                    print(f"[startup] Restored session for {apple_id}")
    except Exception as e:
        print(f"[startup] Could not restore session: {e}")

    polling_task = asyncio.create_task(poll_location())
    yield
    polling_task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# --- Routes ---


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/status")
async def api_status():
    """Check if there's an active session and tracked device."""
    if icloud_api and not icloud_api.requires_2fa and not icloud_api.requires_2sa:
        db_device = get_tracked_device_from_db()
        return {
            "authenticated": True,
            "has_device": db_device is not None,
            "device": db_device,
        }
    return {"authenticated": False, "has_device": False, "device": None}


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
        # Save cookies after successful 2FA
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
    """List all available iCloud devices."""
    if not icloud_api:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)

    devices = []
    for i, device in enumerate(icloud_api.devices):
        status = device.status()
        devices.append(
            {
                "index": i,
                "id": device.data.get("id", ""),
                "name": status.get("name", f"Device {i}"),
                "model": status.get("deviceDisplayName", "Unknown"),
                "battery": status.get("batteryLevel"),
            }
        )
    return {"devices": devices}


@app.post("/api/select-device")
async def api_select_device(request: Request):
    """Select which device to track."""
    global tracked_device, last_saved_location
    if not icloud_api:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)

    body = await request.json()
    device_index = body.get("index")

    devices_list = list(icloud_api.devices)
    if device_index is None or device_index < 0 or device_index >= len(devices_list):
        return JSONResponse({"error": "Invalid device index."}, status_code=400)

    device = devices_list[device_index]
    tracked_device = device
    last_saved_location = None

    status = device.status()
    device_id = device.data.get("id", "")
    device_name = status.get("name", "Unknown")
    device_model = status.get("deviceDisplayName", "Unknown")

    save_tracked_device_to_db(device_id, device_name, device_model)
    return {"status": "ok", "device": {"name": device_name, "model": device_model}}


@app.get("/api/location")
async def api_location():
    """Get the current live location."""
    if not icloud_api or not tracked_device:
        return JSONResponse({"error": "Not tracking."}, status_code=400)

    try:
        if icloud_api.requires_2fa or icloud_api.requires_2sa:
            return JSONResponse({"error": "Session expired."}, status_code=401)

        location = tracked_device.location
        if location and location.get("latitude"):
            status = tracked_device.status()
            return {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "accuracy": location.get("horizontalAccuracy"),
                "position_type": location.get("positionType"),
                "altitude": location.get("altitude"),
                "timestamp": location.get("timeStamp"),
                "is_old": location.get("isOld"),
                "battery": status.get("batteryLevel"),
            }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"error": "Location unavailable."}, status_code=404)


@app.get("/api/history/dates")
async def api_history_dates():
    """Get all dates that have location history, with point count and distance."""
    db_device = get_tracked_device_from_db()
    if not db_device:
        return {"dates": []}

    result = (
        supabase.table("location_history")
        .select("latitude,longitude,created_at")
        .eq("device_id", db_device["device_id"])
        .order("created_at", desc=False)
        .execute()
    )

    # Group by date and compute stats
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


@app.get("/api/history/{date}")
async def api_history_for_date(date: str):
    """Get all location points for a specific date."""
    db_device = get_tracked_device_from_db()
    if not db_device:
        return {"points": [], "total_distance_m": 0}

    start = f"{date}T00:00:00+00:00"
    end = f"{date}T23:59:59+00:00"

    result = (
        supabase.table("location_history")
        .select("*")
        .eq("device_id", db_device["device_id"])
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
