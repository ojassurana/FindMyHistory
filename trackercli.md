# FindMyHistory — AI Agent Skill Document

## Overview

FindMyHistory is a **live location tracking system** that tracks iPhones via Apple's iCloud API. It runs 24/7 on Heroku, polling iCloud every 5 seconds and saving location history to a Supabase database. The system tracks multiple devices simultaneously and provides both real-time and historical location data through a REST API.

**Base URL:** `https://track.ojassurana.com`

---

## Response Style — KEEP IT SHORT

The agent MUST keep all responses **short and to the point**. No fluff, no narration, no explaining what you're about to do or what you just did. Just the data.

- Present results as a **compact table** with the key info (device, location, distance, etc.)
- If there's a distance, show it clearly (meters + km)
- If there's a `note` from the API (e.g., data gap), include it as a single line below the table
- Do NOT add commentary like "They're pretty close" or "about a 5-minute walk" — just the numbers
- Do NOT narrate your API calls or explain your process — the user doesn't care, they want the answer
- Do NOT repeat device info the user already knows

**Good example:**
```
| Device | Location |
|---|---|
| Ojas's 17 Pro Max | 1.3979, 103.7470 |
| iPad (2) | 1.3977, 103.7507 |

**Distance: 416.8m (0.42 km)**
```

**Bad example:**
```
Let me first check the tracked devices... OK, I found 3 devices.
Now let me get the distance... Here's the result:
[table]
They're pretty close — about a 5-minute walk apart!
```

---

## IMPORTANT: First Thing To Do

**Before handling ANY location request, the agent MUST call `/api/tracked-devices` first** to get the list of all currently tracked devices. This gives the agent context about who is being tracked, their device names, and models. Cache this for the rest of the conversation — no need to call it again unless the user adds or removes a device.

```
GET /api/tracked-devices
```

This returns the full list of tracked devices. Use the device names from this response for all subsequent API calls. This ensures the agent always knows which devices are available and can match user requests to the correct device.

---

## System Architecture

```
iCloud API ──(5s poll)──> Background Worker ──(20m threshold)──> Supabase DB
                               │
                               ├── live_locations cache (in-memory, every 5s)
                               │
Browser/Agent ──(HTTP)──> FastAPI Web Server ──(reads cache)──> JSON Response
```

- A **background worker thread** polls iCloud every 5 seconds for all tracked devices
- Location data is **only saved to the database** when a device moves more than **20 meters** from its last saved position
- The **live location cache** is always up-to-date (refreshed every 5s) regardless of whether the point was saved
- The web server reads from the live cache for current locations and from Supabase for historical data

---

## API Endpoints

### 1. Where Is a Device? (Live or Historical)

```
GET /api/where/{device_name}
GET /api/where/{device_name}?time={time}
```

**Purpose:** Get the current or historical location of a tracked device.

**Parameters:**
- `device_name` (path) — Partial, case-insensitive name match. e.g., `ojas` matches "Ojas's 17 Pro Max", `divi` matches "Divijaa's iPhone"
- `time` (query, optional) — If omitted, returns **live location**. If provided, returns the **closest saved historical point** to that time.
  - Accepts `HH:MM` format (assumes today, UTC) e.g., `13:45`
  - Accepts full ISO format e.g., `2026-04-22T13:45:00+00:00`

**Response (live — no time param):**
```json
{
  "device": "Ojas's 17 Pro Max",
  "latitude": 1.397853,
  "longitude": 103.746986,
  "accuracy": 9.25,
  "polled_at": 1776867200624,
  "source": "live"
}
```

**Response (historical — with time param):**
```json
{
  "device": "Ojas's 17 Pro Max",
  "latitude": 1.321768,
  "longitude": 103.770988,
  "accuracy": 2.31,
  "recorded_at": "2026-04-22T13:45:02.375430+00:00",
  "source": "history"
}
```

**Response (time requested but no data — falls back to live):**
```json
{
  "device": "Ojas's 17 Pro Max",
  "latitude": 1.397853,
  "longitude": 103.746986,
  "accuracy": 9.25,
  "polled_at": 1776867200624,
  "source": "live",
  "note": "No history data for the requested time. Showing current live location instead."
}
```

**Response (time requested, closest data is far off):**
```json
{
  "device": "Ojas's 17 Pro Max",
  "latitude": 1.274975,
  "longitude": 103.845750,
  "accuracy": 12.61,
  "recorded_at": "2026-04-22T12:08:36.204083+00:00",
  "source": "history",
  "note": "No exact data for the requested time. Closest record is 548 minutes away."
}
```

**Important behavior:**
- If the `source` is `"live"`, the data is from the real-time iCloud poll (updated every 5s)
- If the `source` is `"history"`, the data is from the Supabase database (saved points only, 20m threshold)
- If a `note` field is present, it means the exact requested data wasn't available — communicate this to the user
- `polled_at` is a Unix timestamp in milliseconds (live source only)
- `recorded_at` is an ISO 8601 timestamp (history source only)

---

### 2. Distance Between Two Devices

```
GET /api/distance/{device_name_1}/{device_name_2}
```

**Purpose:** Get the real-time distance between two tracked devices.

**Parameters:**
- `device_name_1` (path) — Partial, case-insensitive name match
- `device_name_2` (path) — Partial, case-insensitive name match

**Response:**
```json
{
  "device_1": "Ojas's 17 Pro Max",
  "device_2": "Divijaa's iPhone",
  "distance_m": 415.9,
  "distance_km": 0.42,
  "device_1_location": {
    "latitude": 1.397853,
    "longitude": 103.746986
  },
  "device_2_location": {
    "latitude": 1.397718,
    "longitude": 103.750725
  }
}
```

---

### 3. List All Tracked Devices

```
GET /api/tracked-devices
```

**Response:**
```json
{
  "devices": [
    {
      "device_id": "ATzmq...",
      "device_name": "Ojas's 17 Pro Max",
      "device_model": "iPhone 17 Pro Max",
      "color": "#3b82f6"
    },
    {
      "device_id": "AdGgB...",
      "device_name": "Divijaa's iPhone",
      "device_model": "iPhone 15",
      "color": "#22c55e"
    }
  ]
}
```

---

### 4. All Live Locations

```
GET /api/locations
```

**Purpose:** Get live locations for ALL tracked devices at once.

**Response:**
```json
{
  "locations": [
    {
      "device_id": "ATzmq...",
      "device_name": "Ojas's 17 Pro Max",
      "color": "#3b82f6",
      "latitude": 1.397853,
      "longitude": 103.746986,
      "accuracy": 9.25,
      "position_type": "GPS",
      "timestamp": 1776860881082,
      "polled_at": 1776867200624,
      "is_old": false,
      "battery": null
    }
  ]
}
```

---

### 5. History Dates for a Device

```
GET /api/history/dates/{device_id}
```

**Note:** This endpoint uses the full `device_id`, not the name. Get the `device_id` from `/api/tracked-devices` first.

**Response:**
```json
{
  "dates": [
    {
      "date": "2026-04-22",
      "points": 65,
      "distance_m": 22879.0
    }
  ]
}
```

---

### 6. History Points for a Specific Date

```
GET /api/history/{device_id}/{date}
```

**Parameters:**
- `device_id` (path) — Full device ID
- `date` (path) — Date in `YYYY-MM-DD` format

**Response:**
```json
{
  "points": [
    {
      "latitude": 1.274975,
      "longitude": 103.845750,
      "accuracy": 12.61,
      "position_type": "Cell",
      "timestamp": 1776854916204,
      "created_at": "2026-04-22T12:08:36.204083+00:00"
    }
  ],
  "total_distance_m": 22879.0
}
```

---

## Error Handling — Self-Correction

When a device name doesn't match, the API returns all available devices so the agent can retry:

```json
{
  "error": "No tracked device matching 'bob'.",
  "available_devices": [
    {"name": "Ojas's 17 Pro Max", "model": "iPhone 17 Pro Max"},
    {"name": "Divijaa's iPhone", "model": "iPhone 15"}
  ],
  "hint": "Try again with one of the available device names (partial match supported)."
}
```

**Agent behavior on error:**
1. Receive the error with `available_devices`
2. Match the user's intent to the closest available device name
3. Retry the API call with a correct partial name (e.g., `ojas`, `divi`)

---

## Device Name Matching

All endpoints that accept a device name support **partial, case-insensitive matching**:

| Input | Matches |
|-------|---------|
| `ojas` | Ojas's 17 Pro Max |
| `17 pro` | Ojas's 17 Pro Max |
| `divi` | Divijaa's iPhone |
| `iphone 15` | Divijaa's iPhone |
| `divijaa` | Divijaa's iPhone |

---

## Database Schema (Supabase)

### `tracked_device`
| Column | Type | Description |
|--------|------|-------------|
| id | bigint | Primary key |
| device_id | text | iCloud device identifier |
| device_name | text | Human-readable name (e.g., "Ojas's 17 Pro Max") |
| device_model | text | Model name (e.g., "iPhone 17 Pro Max") |
| created_at | timestamptz | When the device was added to tracking |

### `location_history`
| Column | Type | Description |
|--------|------|-------------|
| id | bigint | Primary key |
| device_id | text | References tracked_device.device_id |
| latitude | double precision | GPS latitude |
| longitude | double precision | GPS longitude |
| accuracy | double precision | Horizontal accuracy in meters |
| position_type | text | GPS, WiFi, or Cell |
| altitude | double precision | Altitude in meters |
| icloud_timestamp | bigint | iCloud's timestamp (Unix ms) |
| created_at | timestamptz | When the point was saved to DB |

### `icloud_session`
| Column | Type | Description |
|--------|------|-------------|
| id | bigint | Primary key |
| apple_id | text | Apple ID email |
| cookie_data | jsonb | Serialized iCloud session cookies |
| is_active | boolean | Whether this session is current |
| created_at | timestamptz | Session creation time |
| updated_at | timestamptz | Last cookie update |

---

## Key Behaviors

1. **Polling:** The server polls iCloud every 5 seconds for all tracked devices. This happens 24/7 regardless of whether anyone is viewing the website.

2. **20m Threshold:** A new location point is only saved to the database when the device has moved more than 20 meters from its last saved position. This prevents thousands of duplicate points when a device is stationary.

3. **Live vs History:**
   - **Live** = from the in-memory cache, updated every 5 seconds, always current
   - **History** = from the database, only includes points where the device moved 20m+

4. **Session Management:** iCloud sessions last ~2 months. When a session expires, all users see a login wall until someone re-authenticates with Apple ID + 2FA.

5. **Public Access:** The website and API are publicly accessible. No per-user authentication. Anyone can view any tracked device's location.

---

## Example Agent Workflows

### "Where is Ojas?"
```
GET /api/where/ojas
→ Returns live location with coordinates
```

### "Where was Divijaa at 2pm?"
```
GET /api/where/divi?time=14:00
→ Returns closest saved point to 2pm UTC
→ If note field present, inform user the data is approximate
```

### "How far apart are Ojas and Divijaa?"
```
GET /api/distance/ojas/divi
→ Returns distance in meters and kilometers
```

### "Where is Bob?" (unknown device)
```
GET /api/where/bob
→ 404 with available_devices list
→ Agent retries with correct name from the list
```

### "Where was Ojas at 3am?" (no data for that time)
```
GET /api/where/ojas?time=03:00
→ Returns closest available data or live fallback
→ note field explains the data gap
→ Agent tells user: "I don't have data for 3am, but here's where Ojas is right now"
```
