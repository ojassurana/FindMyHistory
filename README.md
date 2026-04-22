# FindMyHistory

A live location tracker that displays your iPhone's real-time position on a map using the iCloud API — with full historical playback of past movements.

---

## Overview

### How It Works
- A one-time iCloud authentication on the website starts server-side location tracking
- Once authenticated, **all visitors** can see the live location — no per-user login required
- The server polls iCloud every **5 seconds** and pushes updates to the map in real time
- Coordinates are persisted to the database every 5 seconds, skipping writes when the device hasn't moved more than **20 meters**
- If the iCloud session expires, the login form reappears — historical data remains viewable
- Apple credentials are **never stored** on the server

### The Interface
1. **Login** — On first visit (no active iCloud session), a clean Apple ID form: username, password, and 2FA
2. **Device Selection** — After authentication, choose which device to track from a list of your iCloud-linked devices
3. **Live Map** — Full-screen map with a pin on the tracked device's current location
4. **Info Panel** — A sleek bottom panel showing last updated time, GPS accuracy, and reverse-geocoded address
5. **History Playback** — A date bar along the bottom; tap a date to open a timeline scrubber that replays the day's movement on the map

---

## Development

### Tech Stack
- **Backend:** FastAPI (Python) — powers both the Telegram bot and the web application
- **Frontend:** HTML, CSS, and JavaScript
- **Maps:** Leaflet + OpenStreetMap — free, no API keys required
- **Database:** Supabase

### Local Development
- Developing on **localhost**
- Using **ngrok** to expose the local server for Telegram webhook callbacks during development

## Production

### Deployment
- Hosted on **Heroku**
- Code is pushed to **GitHub**, which is linked to Heroku for automatic deployments via the **Heroku CLI**

## Skills I've Used

### `/grill-me`
Interviews you relentlessly about a plan or design, probing every branch of the decision tree until you reach shared understanding.
Great for stress-testing ideas and making sure your thinking holds up before you start building.

### `/frontend-design`
A Claude Code skill for creating distinctive, production-grade frontend interfaces with high design quality.
Used to build the polished web UI for FindMyHistory, avoiding generic AI aesthetics in favor of clean, modern design.

### `/mobile-app-design`
Reviews and guides UI design for mobile-friendly interfaces, ensuring adherence to iOS/Android design patterns and best practices.
Used to keep the FindMyHistory web app fully responsive and optimized for mobile browsers.
