// --- State ---
let map = null;
let markers = {};       // {device_id: L.circleMarker}
let accuracyCircles = {};
let pollInterval = null;
let playbackInterval = null;
let playbackPoints = [];
let playbackIndex = 0;
let isPlaying = false;
let trailLine = null;
let selectedHistoryDevice = null;

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

        if (data.authenticated && data.has_devices) {
            showApp();
        } else if (data.authenticated && !data.has_devices) {
            showApp();
            openAddDevice();
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
            showApp();
            openAddDevice();
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
            showApp();
            openAddDevice();
        }
    } catch {
        errorEl.textContent = "Connection failed.";
        errorEl.classList.add("visible");
    } finally {
        btn.disabled = false;
        btn.textContent = "Verify";
    }
});

// --- Device Picker (Add Device modal) ---
async function openAddDevice() {
    const modal = document.getElementById("device-modal");
    modal.classList.add("active");
    const list = document.getElementById("device-picker-list");
    list.innerHTML = '<li class="device-picker-loading">Scanning for devices...</li>';

    try {
        const res = await fetch("/api/devices");
        const data = await res.json();
        list.innerHTML = "";

        if (!data.devices.length) {
            list.innerHTML = '<li class="device-picker-loading">No available devices with location.</li>';
            return;
        }

        data.devices.forEach((device) => {
            const li = document.createElement("li");
            li.innerHTML = `
                <div class="device-picker-info">
                    <div class="device-name">${device.name}</div>
                    <div class="device-model">${device.model}${device.battery ? " · " + Math.round(device.battery * 100) + "%" : ""}</div>
                </div>
                <div class="device-picker-coords">
                    <span>${device.latitude}, ${device.longitude}</span>
                </div>
            `;
            li.addEventListener("click", () => addDevice(device.index));
            list.appendChild(li);
        });
    } catch {
        list.innerHTML = '<li class="device-picker-loading" style="color: var(--danger);">Failed to load devices.</li>';
    }
}

function closeAddDevice() {
    document.getElementById("device-modal").classList.remove("active");
}

async function addDevice(index) {
    try {
        const res = await fetch("/api/add-device", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ index }),
        });
        if (res.ok) {
            closeAddDevice();
            loadTrackedDevices();
        }
    } catch (e) {
        console.error("Failed to add device:", e);
    }
}

async function removeDevice(deviceId) {
    try {
        const res = await fetch("/api/remove-device", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ device_id: deviceId }),
        });
        if (res.ok) {
            // Remove marker from map
            if (markers[deviceId]) {
                map.removeLayer(markers[deviceId]);
                delete markers[deviceId];
            }
            if (accuracyCircles[deviceId]) {
                map.removeLayer(accuracyCircles[deviceId]);
                delete accuracyCircles[deviceId];
            }
            loadTrackedDevices();
        }
    } catch (e) {
        console.error("Failed to remove device:", e);
    }
}

// --- Main App ---
function showApp() {
    hideAll();
    appLayout.classList.add("active");
    initMap();
    loadTrackedDevices();
    startPolling();
}

async function loadTrackedDevices() {
    const container = document.getElementById("tracked-devices-list");
    const historySelect = document.getElementById("history-device-select");

    try {
        const res = await fetch("/api/tracked-devices");
        const data = await res.json();

        // Tracked devices list in sidebar
        container.innerHTML = "";
        historySelect.innerHTML = '<option value="">Select device</option>';

        if (!data.devices.length) {
            container.innerHTML = '<div class="no-devices">No devices tracked yet.</div>';
            return;
        }

        data.devices.forEach((dev) => {
            // Sidebar device card
            const div = document.createElement("div");
            div.className = "tracked-device-card";
            div.innerHTML = `
                <div class="tracked-device-dot" style="background: ${dev.color};"></div>
                <div class="tracked-device-info">
                    <div class="tracked-device-name">${dev.device_name}</div>
                    <div class="tracked-device-model">${dev.device_model}</div>
                </div>
                <button class="tracked-device-remove" onclick="removeDevice('${dev.device_id.replace(/'/g, "\\'")}')">✕</button>
            `;
            container.appendChild(div);

            // History dropdown option
            const option = document.createElement("option");
            option.value = dev.device_id;
            option.textContent = dev.device_name;
            historySelect.appendChild(option);
        });

        // Auto-select first device for history
        if (data.devices.length && !selectedHistoryDevice) {
            selectedHistoryDevice = data.devices[0].device_id;
            historySelect.value = selectedHistoryDevice;
            loadHistoryDates(selectedHistoryDevice);
        }
    } catch {
        container.innerHTML = '<div class="no-devices">Failed to load devices.</div>';
    }
}

function initMap() {
    if (map) return;

    map = L.map("map", {
        zoomControl: true,
        attributionControl: true,
    }).setView([1.3521, 103.8198], 13);

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: "abcd",
        maxZoom: 20,
    }).addTo(map);
}

function startPolling() {
    pollLocations();
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollLocations, POLL_MS);
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

async function pollLocations() {
    try {
        const res = await fetch("/api/locations");

        if (res.status === 401) {
            stopPolling();
            hideAll();
            showLogin();
            return;
        }

        if (!res.ok) return;

        const data = await res.json();
        const bounds = [];

        data.locations.forEach((loc) => {
            const latlng = [loc.latitude, loc.longitude];
            bounds.push(latlng);

            // Create or update marker
            if (!markers[loc.device_id]) {
                markers[loc.device_id] = L.circleMarker(latlng, {
                    radius: 8,
                    fillColor: loc.color,
                    fillOpacity: 1,
                    color: "#fff",
                    weight: 2,
                }).addTo(map);

                // Tooltip with device name
                markers[loc.device_id].bindTooltip(loc.device_name, {
                    permanent: false,
                    direction: "top",
                    className: "device-tooltip",
                });

                // Click to show info
                markers[loc.device_id].on("click", () => updateInfoPanel(loc));
            } else {
                markers[loc.device_id].setLatLng(latlng);
                markers[loc.device_id].setStyle({ fillColor: loc.color });
            }

            // Accuracy circle
            if (loc.accuracy) {
                if (!accuracyCircles[loc.device_id]) {
                    accuracyCircles[loc.device_id] = L.circle(latlng, {
                        radius: loc.accuracy,
                        color: loc.color,
                        fillColor: loc.color,
                        fillOpacity: 0.08,
                        weight: 1,
                    }).addTo(map);
                } else {
                    accuracyCircles[loc.device_id].setLatLng(latlng);
                    accuracyCircles[loc.device_id].setRadius(loc.accuracy);
                }
            }
        });

        // Update info panel with first device if none selected
        if (data.locations.length) {
            const panel = document.getElementById("info-panel");
            if (!panel.classList.contains("active")) {
                updateInfoPanel(data.locations[0]);
            }
        }

        // Fit bounds on first load
        if (bounds.length && !window._initialBoundsSet) {
            if (bounds.length === 1) {
                map.setView(bounds[0], 16);
            } else {
                map.fitBounds(bounds, { padding: [50, 50] });
            }
            window._initialBoundsSet = true;
        }
    } catch {
        // Network error, keep polling
    }
}

function updateInfoPanel(loc) {
    const panel = document.getElementById("info-panel");
    panel.classList.add("active");

    document.getElementById("info-device-name").textContent = loc.device_name;
    const ts = loc.timestamp ? new Date(loc.timestamp).toLocaleTimeString() : "—";
    document.getElementById("info-updated").textContent = ts;
    document.getElementById("info-accuracy").textContent = loc.accuracy ? `${loc.accuracy.toFixed(1)}m` : "—";
    document.getElementById("info-coords").textContent = `${loc.latitude.toFixed(6)}, ${loc.longitude.toFixed(6)}`;
    document.getElementById("info-battery").textContent = loc.battery ? `${Math.round(loc.battery * 100)}%` : "—";
}

// --- History ---
function onHistoryDeviceChange(select) {
    selectedHistoryDevice = select.value;
    if (selectedHistoryDevice) {
        loadHistoryDates(selectedHistoryDevice);
    } else {
        document.getElementById("date-list").innerHTML = '<div class="date-list-empty">Select a device to view history.</div>';
    }
}

async function loadHistoryDates(deviceId) {
    const dateList = document.getElementById("date-list");
    dateList.innerHTML = '<div class="date-list-empty">Loading...</div>';

    try {
        const res = await fetch(`/api/history/dates/${encodeURIComponent(deviceId)}`);
        const data = await res.json();

        if (!data.dates.length) {
            dateList.innerHTML = '<div class="date-list-empty">No history yet for this device.</div>';
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
                <div class="date-item-meta">${entry.points} points · ${dist}</div>
            `;
            div.addEventListener("click", () => loadHistory(deviceId, entry.date, div));
            dateList.appendChild(div);
        });
    } catch {
        dateList.innerHTML = '<div class="date-list-empty">Failed to load history.</div>';
    }
}

async function loadHistory(deviceId, date, el) {
    document.querySelectorAll(".date-item").forEach((d) => d.classList.remove("active"));
    el.classList.add("active");
    stopPlayback();

    try {
        const res = await fetch(`/api/history/${encodeURIComponent(deviceId)}/${date}`);
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

        if (trailLine) {
            map.removeLayer(trailLine);
            trailLine = null;
        }

        showPlaybackPoint(0);

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

    // Use a temporary playback marker
    if (!window._playbackMarker) {
        window._playbackMarker = L.circleMarker(latlng, {
            radius: 8,
            fillColor: "#3b82f6",
            fillOpacity: 1,
            color: "#fff",
            weight: 2,
        }).addTo(map);
    } else {
        window._playbackMarker.setLatLng(latlng);
    }

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
    stopPolling();

    const speed = parseFloat(document.getElementById("speed-select").value);
    const baseDuration = 15000;
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
    if (window._playbackMarker) {
        map.removeLayer(window._playbackMarker);
        window._playbackMarker = null;
    }
}

document.getElementById("scrubber").addEventListener("input", (e) => {
    pausePlayback();
    playbackIndex = parseInt(e.target.value);
    showPlaybackPoint(playbackIndex);
});

document.getElementById("speed-select").addEventListener("change", () => {
    if (isPlaying) {
        pausePlayback();
        startPlayback();
    }
});

function backToLive() {
    stopPlayback();
    document.getElementById("playback-bar").classList.remove("active");
    document.querySelectorAll(".date-item").forEach((d) => d.classList.remove("active"));
    window._initialBoundsSet = false;
    startPolling();
}

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
