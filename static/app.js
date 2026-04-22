// --- State ---
let map = null;
let marker = null;
let accuracyCircle = null;
let pollInterval = null;
let playbackInterval = null;
let playbackPoints = [];
let playbackIndex = 0;
let isPlaying = false;
let trailLine = null;

const POLL_MS = 5000;

// --- DOM refs ---
const loginScreen = document.getElementById("login-screen");
const deviceScreen = document.getElementById("device-screen");
const tfaScreen = document.getElementById("tfa-screen");
const appLayout = document.getElementById("app-layout");

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
    checkStatus();
});

async function checkStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();

        if (data.authenticated && data.has_device) {
            showApp(data.device);
        } else if (data.authenticated && !data.has_device) {
            showDeviceSelection();
        } else {
            showLogin();
        }
    } catch {
        showLogin();
    }
}

// --- Login ---
function showLogin() {
    hideAll();
    loginScreen.style.display = "flex";
}

document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button");
    const errorEl = document.getElementById("login-error");
    errorEl.classList.remove("visible");

    const apple_id = document.getElementById("apple-id").value.trim();
    const password = document.getElementById("password").value.trim();

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';

    try {
        const res = await fetch("/api/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ apple_id, password }),
        });
        const data = await res.json();

        if (!res.ok) {
            errorEl.textContent = data.error;
            errorEl.classList.add("visible");
        } else if (data.status === "2fa_required" || data.status === "2sa_required") {
            show2FA();
        } else if (data.status === "authenticated") {
            showDeviceSelection();
        }
    } catch {
        errorEl.textContent = "Connection failed.";
        errorEl.classList.add("visible");
    } finally {
        btn.disabled = false;
        btn.textContent = "Sign In";
    }
});

// --- 2FA ---
function show2FA() {
    hideAll();
    tfaScreen.style.display = "flex";
    document.getElementById("tfa-code").value = "";
    document.getElementById("tfa-code").focus();
}

document.getElementById("tfa-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button");
    const errorEl = document.getElementById("tfa-error");
    errorEl.classList.remove("visible");

    const code = document.getElementById("tfa-code").value.trim();
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';

    try {
        const res = await fetch("/api/verify-2fa", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code }),
        });
        const data = await res.json();

        if (!res.ok) {
            errorEl.textContent = data.error;
            errorEl.classList.add("visible");
        } else {
            showDeviceSelection();
        }
    } catch {
        errorEl.textContent = "Connection failed.";
        errorEl.classList.add("visible");
    } finally {
        btn.disabled = false;
        btn.textContent = "Verify";
    }
});

// --- Device Selection ---
async function showDeviceSelection() {
    hideAll();
    deviceScreen.style.display = "flex";
    const list = document.getElementById("device-list");
    list.innerHTML = '<li style="color: var(--text-muted);">Loading devices...</li>';

    try {
        const res = await fetch("/api/devices");
        const data = await res.json();
        list.innerHTML = "";

        data.devices.forEach((device) => {
            const li = document.createElement("li");
            li.innerHTML = `
                <div>
                    <div class="device-name">${device.name}</div>
                    <div class="device-model">${device.model}</div>
                </div>
                <div class="device-model">${device.battery ? Math.round(device.battery * 100) + "%" : ""}</div>
            `;
            li.addEventListener("click", () => selectDevice(device.index));
            list.appendChild(li);
        });
    } catch {
        list.innerHTML = '<li style="color: var(--danger);">Failed to load devices.</li>';
    }
}

async function selectDevice(index) {
    try {
        const res = await fetch("/api/select-device", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ index }),
        });
        const data = await res.json();
        if (res.ok) {
            const statusRes = await fetch("/api/status");
            const statusData = await statusRes.json();
            showApp(statusData.device);
        }
    } catch (e) {
        console.error("Failed to select device:", e);
    }
}

// --- Main App ---
function showApp(device) {
    hideAll();
    appLayout.classList.add("active");

    document.getElementById("tracking-device-name").textContent = device.device_name;
    document.getElementById("tracking-device-model").textContent = device.device_model;

    initMap();
    startPolling();
    loadHistoryDates();
}

function initMap() {
    if (map) return;

    map = L.map("map", {
        zoomControl: true,
        attributionControl: true,
    }).setView([1.3521, 103.8198], 13); // Default to Singapore

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: "abcd",
        maxZoom: 20,
    }).addTo(map);
}

function startPolling() {
    pollLocation();
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollLocation, POLL_MS);
}

async function pollLocation() {
    try {
        const res = await fetch("/api/location");

        if (res.status === 401) {
            // Session expired — show login wall
            stopPolling();
            hideAll();
            showLogin();
            return;
        }

        if (!res.ok) return;

        const loc = await res.json();
        updateMap(loc);
        updateInfoPanel(loc);
    } catch {
        // Network error, keep polling
    }
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

function updateMap(loc) {
    const latlng = [loc.latitude, loc.longitude];

    if (!marker) {
        marker = L.circleMarker(latlng, {
            radius: 8,
            fillColor: "#3b82f6",
            fillOpacity: 1,
            color: "#fff",
            weight: 2,
        }).addTo(map);

        map.setView(latlng, 16);
    } else {
        marker.setLatLng(latlng);
    }

    // Accuracy circle
    if (loc.accuracy) {
        if (!accuracyCircle) {
            accuracyCircle = L.circle(latlng, {
                radius: loc.accuracy,
                color: "#3b82f6",
                fillColor: "#3b82f6",
                fillOpacity: 0.08,
                weight: 1,
            }).addTo(map);
        } else {
            accuracyCircle.setLatLng(latlng);
            accuracyCircle.setRadius(loc.accuracy);
        }
    }
}

function updateInfoPanel(loc) {
    const panel = document.getElementById("info-panel");
    panel.classList.add("active");

    const ts = loc.timestamp ? new Date(loc.timestamp).toLocaleTimeString() : "—";
    document.getElementById("info-updated").textContent = ts;
    document.getElementById("info-accuracy").textContent = loc.accuracy ? `${loc.accuracy.toFixed(1)}m` : "—";
    document.getElementById("info-coords").textContent = `${loc.latitude.toFixed(6)}, ${loc.longitude.toFixed(6)}`;
    document.getElementById("info-battery").textContent = loc.battery ? `${Math.round(loc.battery * 100)}%` : "—";
}

// --- History ---
async function loadHistoryDates() {
    const dateList = document.getElementById("date-list");
    dateList.innerHTML = '<div class="date-list-empty">Loading...</div>';

    try {
        const res = await fetch("/api/history/dates");
        const data = await res.json();

        if (!data.dates.length) {
            dateList.innerHTML = '<div class="date-list-empty">No history yet. Tracking will build up over time.</div>';
            return;
        }

        dateList.innerHTML = "";
        data.dates.forEach((entry) => {
            const div = document.createElement("div");
            div.className = "date-item";
            const d = new Date(entry.date + "T00:00:00");
            const today = new Date();
            const isToday = entry.date === today.toISOString().slice(0, 10);
            const dateLabel = isToday ? `Today — ${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })}` : d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });
            const dist = formatDistance(entry.distance_m);
            div.innerHTML = `
                <div class="date-item-title">${dateLabel}</div>
                <div class="date-item-meta">${entry.points} points &middot; ${dist}</div>
            `;
            div.addEventListener("click", () => loadHistory(entry.date, div));
            dateList.appendChild(div);
        });
    } catch {
        dateList.innerHTML = '<div class="date-list-empty">Failed to load history.</div>';
    }
}

async function loadHistory(date, el) {
    // Highlight active date
    document.querySelectorAll(".date-item").forEach((d) => d.classList.remove("active"));
    el.classList.add("active");

    // Stop live polling while playing history
    stopPlayback();

    try {
        const res = await fetch(`/api/history/${date}`);
        const data = await res.json();

        if (!data.points.length) return;

        playbackPoints = data.points;
        playbackIndex = 0;

        const playbackBar = document.getElementById("playback-bar");
        playbackBar.classList.add("active");
        document.getElementById("playback-date").textContent = date;
        document.getElementById("playback-distance").textContent = formatDistance(data.total_distance_m);

        const scrubber = document.getElementById("scrubber");
        scrubber.max = playbackPoints.length - 1;
        scrubber.value = 0;

        // Clear previous trail
        if (trailLine) {
            map.removeLayer(trailLine);
            trailLine = null;
        }

        // Show first point
        showPlaybackPoint(0);

        // Fit map to bounds
        const bounds = playbackPoints.map((p) => [p.latitude, p.longitude]);
        map.fitBounds(bounds, { padding: [50, 50] });
    } catch {
        console.error("Failed to load history");
    }
}

function showPlaybackPoint(index) {
    if (index >= playbackPoints.length) return;

    const point = playbackPoints[index];
    const latlng = [point.latitude, point.longitude];

    if (!marker) {
        marker = L.circleMarker(latlng, {
            radius: 8,
            fillColor: "#3b82f6",
            fillOpacity: 1,
            color: "#fff",
            weight: 2,
        }).addTo(map);
    } else {
        marker.setLatLng(latlng);
    }

    // Draw trail up to current point
    const trailCoords = playbackPoints.slice(0, index + 1).map((p) => [p.latitude, p.longitude]);
    if (trailLine) {
        trailLine.setLatLngs(trailCoords);
    } else {
        trailLine = L.polyline(trailCoords, {
            color: "#3b82f6",
            weight: 3,
            opacity: 0.7,
        }).addTo(map);
    }

    document.getElementById("scrubber").value = index;
}

function togglePlayback() {
    if (isPlaying) {
        pausePlayback();
    } else {
        startPlayback();
    }
}

function startPlayback() {
    if (!playbackPoints.length) return;
    isPlaying = true;
    document.getElementById("play-btn").textContent = "⏸";

    // Stop live polling during playback
    stopPolling();

    const speed = parseFloat(document.getElementById("speed-select").value);
    const baseDuration = 15000; // 15 seconds default
    const duration = baseDuration / speed;
    const intervalMs = duration / playbackPoints.length;

    playbackInterval = setInterval(() => {
        playbackIndex++;
        if (playbackIndex >= playbackPoints.length) {
            pausePlayback();
            return;
        }
        showPlaybackPoint(playbackIndex);
    }, intervalMs);
}

function pausePlayback() {
    isPlaying = false;
    document.getElementById("play-btn").textContent = "▶";
    if (playbackInterval) {
        clearInterval(playbackInterval);
        playbackInterval = null;
    }
}

function stopPlayback() {
    pausePlayback();
    playbackIndex = 0;
    if (trailLine) {
        map.removeLayer(trailLine);
        trailLine = null;
    }
}

// Scrubber input
document.getElementById("scrubber").addEventListener("input", (e) => {
    pausePlayback();
    playbackIndex = parseInt(e.target.value);
    showPlaybackPoint(playbackIndex);
});

// Speed change
document.getElementById("speed-select").addEventListener("change", () => {
    if (isPlaying) {
        pausePlayback();
        startPlayback();
    }
});

// Back to live button
function backToLive() {
    stopPlayback();
    document.getElementById("playback-bar").classList.remove("active");
    document.querySelectorAll(".date-item").forEach((d) => d.classList.remove("active"));
    startPolling();
}

// Switch device
async function switchDevice() {
    stopPolling();
    stopPlayback();
    appLayout.classList.remove("active");
    showDeviceSelection();
}

// Sidebar toggle (mobile)
function toggleSidebar() {
    document.getElementById("sidebar").classList.toggle("open");
}

// --- Helpers ---
function hideAll() {
    loginScreen.style.display = "none";
    deviceScreen.style.display = "none";
    tfaScreen.style.display = "none";
    appLayout.classList.remove("active");
}

function formatDistance(meters) {
    if (meters >= 1000) {
        return `${(meters / 1000).toFixed(1)} km`;
    }
    return `${Math.round(meters)} m`;
}
