"""
FindMyHistory — Live iCloud multi-device location tracker with historical playback.
"""

import json
import math
import os
import tempfile
import threading
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
tracked_devices = {}  # {device_id: icloud_device_object}
last_saved_locations = {}  # {device_id: {latitude, longitude}}
live_locations = {}  # {device_id: {latitude, longitude, accuracy, ...}} — updated by worker
POLL_INTERVAL = 5
MIN_DISTANCE_M = 20
COOKIE_DIR = tempfile.mkdtemp(prefix="icloud_cookies_")

# Device colors for map pins
DEVICE_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#ec4899", "#06b6d4", "#84cc16"]


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def restore_cookies_from_db(apple_id):
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
    result = supabase.table("tracked_device").select("*").order("created_at", desc=False).execute()
    return result.data


def save_tracked_device_to_db(device_id, device_name, device_model):
    existing = supabase.table("tracked_device").select("id").eq("device_id", device_id).execute()
    if existing.data:
        return existing.data[0]
    result = supabase.table("tracked_device").insert(
        {"device_id": device_id, "device_name": device_name, "device_model": device_model}
    ).execute()
    return result.data[0] if result.data else None


def remove_tracked_device_from_db(device_id):
    supabase.table("tracked_device").delete().eq("device_id", device_id).execute()


def save_location(device_id, location):
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


# =============================================================================
# BACKGROUND WORKER — runs in a plain Python thread, no async involvement
# =============================================================================

def background_poll_worker():
    """
    Runs in a daemon thread with its OWN iCloud + Supabase connections.
    Creates a PyiCloudService with refresh_interval=5s so pyicloud's own
    monitor thread keeps device data fresh. This thread just reads the
    cached data and saves to Supabase.
    """
    worker_sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    worker_api = None
    worker_devices = {}  # {device_id: AppleDevice}
    worker_last_saved = {}

    print("[worker] Background poll worker started", flush=True)

    while True:
        try:
            # Connect to iCloud if not connected
            if worker_api is None:
                session = (
                    worker_sb.table("icloud_session")
                    .select("apple_id, cookie_data")
                    .eq("is_active", True)
                    .order("updated_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if not session.data:
                    time.sleep(POLL_INTERVAL)
                    continue

                apple_id = session.data[0]["apple_id"]
                cookie_data = session.data[0].get("cookie_data")

                worker_cookie_dir = tempfile.mkdtemp(prefix="icloud_worker_")
                if cookie_data:
                    for filename, content in cookie_data.items():
                        filepath = Path(worker_cookie_dir) / filename
                        filepath.write_text(content if isinstance(content, str) else json.dumps(content))

                # refresh_interval=5 means pyicloud's own monitor thread
                # refreshes device data every 5 seconds automatically
                api = PyiCloudService(apple_id, cookie_directory=worker_cookie_dir, refresh_interval=POLL_INTERVAL)
                if api.requires_2fa or api.requires_2sa:
                    print("[worker] Session expired, waiting for re-auth", flush=True)
                    time.sleep(30)
                    continue

                worker_api = api
                print(f"[worker] Connected to iCloud as {apple_id}", flush=True)

                # Cache device references (the init already fetched them)
                db_devices = worker_sb.table("tracked_device").select("*").execute().data
                for db_dev in db_devices:
                    for dev_id, dev in worker_api.devices._devices.items():
                        if dev_id == db_dev["device_id"]:
                            worker_devices[dev_id] = dev
                            break
                print(f"[worker] Tracking {len(worker_devices)} devices", flush=True)

                # Load last saved locations
                for d in db_devices:
                    last_loc = (
                        worker_sb.table("location_history")
                        .select("latitude,longitude")
                        .eq("device_id", d["device_id"])
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                    if last_loc.data:
                        worker_last_saved[d["device_id"]] = last_loc.data[0]

            # Check session validity
            if worker_api.requires_2fa or worker_api.requires_2sa:
                print("[worker] Session expired", flush=True)
                worker_api = None
                worker_devices = {}
                time.sleep(30)
                continue

            # Sync tracked devices from DB
            db_devices = worker_sb.table("tracked_device").select("*").execute().data
            db_ids = {d["device_id"] for d in db_devices}
            for db_dev in db_devices:
                did = db_dev["device_id"]
                if did not in worker_devices and did in worker_api.devices._devices:
                    worker_devices[did] = worker_api.devices._devices[did]

            # Read cached device data (refreshed by pyicloud's monitor thread)
            for device_id, device in worker_devices.items():
                if device_id not in db_ids:
                    continue

                location = device.location
                if not location or not location.get("latitude"):
                    continue

                # Update shared live_locations cache for the web endpoint
                live_locations[device_id] = {
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "horizontalAccuracy": location.get("horizontalAccuracy"),
                    "positionType": location.get("positionType"),
                    "timeStamp": location.get("timeStamp"),
                    "isOld": location.get("isOld"),
                    "altitude": location.get("altitude"),
                    "polled_at": int(time.time() * 1000),
                }

                dev_name = next((d["device_name"] for d in db_devices if d["device_id"] == device_id), device_id[:15])
                print(f"[worker] {dev_name}: {location['latitude']:.6f},{location['longitude']:.6f}", flush=True)

                should_save = True
                last = worker_last_saved.get(device_id)
                if last:
                    dist = haversine(
                        last["latitude"], last["longitude"],
                        location["latitude"], location["longitude"],
                    )
                    if dist < MIN_DISTANCE_M:
                        should_save = False

                if should_save:
                    worker_sb.table("location_history").insert({
                        "device_id": device_id,
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                        "accuracy": location.get("horizontalAccuracy"),
                        "position_type": location.get("positionType"),
                        "altitude": location.get("altitude"),
                        "icloud_timestamp": location.get("timeStamp"),
                    }).execute()
                    worker_last_saved[device_id] = {
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                    }
                    last_saved_locations[device_id] = worker_last_saved[device_id]
                    print(f"[worker] Saved: {location['latitude']:.4f},{location['longitude']:.4f}", flush=True)

        except Exception as e:
            print(f"[worker] Error: {e}", flush=True)
            worker_api = None
            worker_devices = {}

        time.sleep(POLL_INTERVAL)


# =============================================================================
# APP STARTUP
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global icloud_api, tracked_devices

    # Restore session from DB
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
                    db_devices = get_all_tracked_devices_from_db()
                    for db_dev in db_devices:
                        for device in icloud_api.devices:
                            if device.data.get("id", "") == db_dev["device_id"]:
                                tracked_devices[db_dev["device_id"]] = device
                                break
                    print(f"[startup] Restored session for {apple_id}, tracking {len(tracked_devices)} devices", flush=True)
    except Exception as e:
        print(f"[startup] Could not restore session: {e}", flush=True)

    # Load last saved locations from DB
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
        print(f"[startup] Could not load last saved locations: {e}", flush=True)

    # Start background worker in a daemon thread
    worker = threading.Thread(target=background_poll_worker, daemon=True)
    worker.start()
    print("[startup] Background worker thread started", flush=True)

    yield


app = FastAPI(lifespan=lifespan)


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
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
    global icloud_api
    if not icloud_api:
        return JSONResponse({"error": "No active login session."}, status_code=400)

    body = await request.json()
    code = body.get("code", "").strip()

    if not code:
        return JSONResponse({"error": "2FA code is required."}, status_code=400)

    result = icloud_api.validate_2fa_code(code)
    if result:
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
    if not icloud_api:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)

    db_devices = get_all_tracked_devices_from_db()
    tracked_ids = {d["device_id"] for d in db_devices}

    devices = []
    for i, device in enumerate(icloud_api.devices):
        status = device.status()
        location = device.location
        device_id = device.data.get("id", "")

        if not location or not location.get("latitude"):
            continue
        if device_id in tracked_ids:
            continue

        devices.append({
            "index": i,
            "id": device_id,
            "name": status.get("name", f"Device {i}"),
            "model": status.get("deviceDisplayName", "Unknown"),
            "battery": status.get("batteryLevel"),
            "latitude": round(location["latitude"], 4),
            "longitude": round(location["longitude"], 4),
        })
    return {"devices": devices}


@app.post("/api/add-device")
async def api_add_device(request: Request):
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

    save_tracked_device_to_db(device_id, device_name, device_model)
    tracked_devices[device_id] = device
    last_saved_locations.pop(device_id, None)

    return {"status": "ok", "device": {"device_id": device_id, "name": device_name, "model": device_model}}


@app.post("/api/remove-device")
async def api_remove_device(request: Request):
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
    """Returns live locations from the worker's cache (updated every 5s)."""
    if not live_locations:
        return JSONResponse({"error": "No location data yet."}, status_code=404)

    db_devices = get_all_tracked_devices_from_db()
    locations = []

    for i, db_dev in enumerate(db_devices):
        loc = live_locations.get(db_dev["device_id"])
        if not loc:
            continue

        locations.append({
            "device_id": db_dev["device_id"],
            "device_name": db_dev["device_name"],
            "color": DEVICE_COLORS[i % len(DEVICE_COLORS)],
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "accuracy": loc.get("horizontalAccuracy"),
            "position_type": loc.get("positionType"),
            "timestamp": loc.get("timeStamp"),
            "polled_at": loc.get("polled_at"),
            "is_old": loc.get("isOld"),
            "battery": None,
        })

    return {"locations": locations}


@app.get("/api/history/dates/{device_id:path}")
async def api_history_dates(device_id: str):
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
