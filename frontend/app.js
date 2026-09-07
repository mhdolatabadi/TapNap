const STORAGE_KEY = "tapnap_route";
const CLIENT_ID_KEY = "tapnap_client_id";

const state = {
  mode: "origin",
  route: null,
};

// No accounts/login -- each browser keeps its own id so everyone gets
// their own saved route and their own chart instead of sharing one
// global route that whoever saved last overwrites for everyone else.
function getClientId() {
  let id = localStorage.getItem(CLIENT_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    try {
      localStorage.setItem(CLIENT_ID_KEY, id);
    } catch {
      /* localStorage unavailable -- id just won't persist across reloads */
    }
  }
  return id;
}

const CLIENT_ID = getClientId();

function apiFetch(url, options = {}) {
  return fetch(url, {
    ...options,
    headers: { ...options.headers, "X-Client-Id": CLIENT_ID },
  });
}

const map = L.map("map");

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

function makeIcon(colorClass) {
  return L.divIcon({
    className: `marker-pin ${colorClass}`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

const originMarker = L.marker([0, 0], { icon: makeIcon("origin"), draggable: true }).addTo(map);
const destinationMarker = L.marker([0, 0], { icon: makeIcon("destination"), draggable: true }).addTo(map);

function loadCachedRoute() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function cacheRoute(route) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(route));
  } catch {
    /* localStorage unavailable (private mode etc.) -- non-fatal */
  }
}

function applyRouteToMap(route) {
  state.route = route;
  originMarker.setLatLng([route.origin.lat, route.origin.lng]);
  destinationMarker.setLatLng([route.destination.lat, route.destination.lng]);
  document.getElementById("origin-label").textContent =
    route.origin.label || `${route.origin.lat.toFixed(4)}, ${route.origin.lng.toFixed(4)}`;
  document.getElementById("destination-label").textContent =
    route.destination.label || `${route.destination.lat.toFixed(4)}, ${route.destination.lng.toFixed(4)}`;
  const bounds = L.latLngBounds([
    [route.origin.lat, route.origin.lng],
    [route.destination.lat, route.destination.lng],
  ]);
  map.fitBounds(bounds, { padding: [40, 40] });
}

async function initRoute() {
  const cached = loadCachedRoute();
  if (cached) applyRouteToMap(cached);

  try {
    const resp = await apiFetch("/api/route");
    if (resp.ok) {
      const serverRoute = await resp.json();
      applyRouteToMap(serverRoute);
      cacheRoute(serverRoute);
      return;
    }
  } catch {
    /* backend unreachable -- fall back to cached/default below */
  }

  if (!cached) {
    applyRouteToMap({
      origin: { lat: 35.6997, lng: 51.338, label: "میدان آزادی" },
      destination: { lat: 35.7448, lng: 51.3752, label: "برج میلاد" },
    });
  }
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
}

document.getElementById("mode-origin").addEventListener("click", () => setMode("origin"));
document.getElementById("mode-destination").addEventListener("click", () => setMode("destination"));

map.on("click", (e) => {
  const point = { lat: e.latlng.lat, lng: e.latlng.lng, label: null };
  if (state.mode === "origin") {
    state.route.origin = point;
    originMarker.setLatLng(e.latlng);
    document.getElementById("origin-label").textContent = `${point.lat.toFixed(4)}, ${point.lng.toFixed(4)}`;
  } else {
    state.route.destination = point;
    destinationMarker.setLatLng(e.latlng);
    document.getElementById("destination-label").textContent = `${point.lat.toFixed(4)}, ${point.lng.toFixed(4)}`;
  }
});

originMarker.on("dragend", () => {
  const { lat, lng } = originMarker.getLatLng();
  state.route.origin = { lat, lng, label: null };
  document.getElementById("origin-label").textContent = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
});

destinationMarker.on("dragend", () => {
  const { lat, lng } = destinationMarker.getLatLng();
  state.route.destination = { lat, lng, label: null };
  document.getElementById("destination-label").textContent = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
});

document.getElementById("save-route").addEventListener("click", async () => {
  const statusEl = document.getElementById("save-status");
  statusEl.textContent = "در حال ذخیره…";
  cacheRoute(state.route);
  try {
    const resp = await apiFetch("/api/route", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.route),
    });
    statusEl.textContent = resp.ok ? "ذخیره شد ✓" : "خطا در ذخیره روی سرور (فقط محلی ذخیره شد)";
    if (resp.ok) loadChart();
  } catch {
    statusEl.textContent = "سرور در دسترس نیست (فقط محلی ذخیره شد)";
  }
  setTimeout(() => (statusEl.textContent = ""), 4000);
});

// ---- chart ----

const PALETTE = ["#4f8cff", "#2ecc71", "#ff5b5b", "#f5a623", "#a06bff", "#00c2c2"];
let chart = null;

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
}

async function loadChart() {
  const hours = document.getElementById("range-select").value;
  let rows = [];
  try {
    const resp = await apiFetch(`/api/prices?hours=${hours}`);
    if (resp.ok) rows = await resp.json();
  } catch {
    return;
  }

  const noDataMsg = document.getElementById("no-data-msg");
  const canvas = document.getElementById("price-chart");

  if (rows.length === 0) {
    noDataMsg.hidden = false;
    canvas.hidden = true;
    return;
  }
  noDataMsg.hidden = true;
  canvas.hidden = false;

  const labelsSet = new Set();
  const series = new Map();
  for (const row of rows) {
    const label = formatTime(row.checked_at);
    labelsSet.add(label);
    const key = `${row.provider} — ${row.service_name}`;
    if (!series.has(key)) series.set(key, new Map());
    series.get(key).set(label, row.price);
  }

  const labels = Array.from(labelsSet);
  const datasets = Array.from(series.entries()).map(([key, points], i) => ({
    label: key,
    data: labels.map((l) => (points.has(l) ? points.get(l) : null)),
    borderColor: PALETTE[i % PALETTE.length],
    backgroundColor: PALETTE[i % PALETTE.length],
    spanGaps: true,
    tension: 0.25,
  }));

  if (chart) chart.destroy();
  chart = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { ticks: { color: "#9aa3b2" }, grid: { color: "#2a2f3a" } },
        x: { ticks: { color: "#9aa3b2" }, grid: { color: "#2a2f3a" } },
      },
      plugins: { legend: { labels: { color: "#e8eaed" } } },
    },
  });
}

document.getElementById("range-select").addEventListener("change", loadChart);

// ---- status ----

async function loadStatus() {
  const el = document.getElementById("status-list");
  try {
    const resp = await apiFetch("/api/status");
    if (!resp.ok) throw new Error("bad response");
    const data = await resp.json();
    if (!data.last_fetch.length) {
      el.textContent = "هنوز هیچ دریافتی انجام نشده";
      return;
    }
    el.innerHTML = "";
    for (const entry of data.last_fetch) {
      const row = document.createElement("div");
      row.className = "status-row";
      const time = formatTime(entry.checked_at);
      row.innerHTML = `<span>${entry.provider}</span><span class="${entry.ok ? "status-ok" : "status-bad"}">${
        entry.ok ? "OK" : "خطا"
      } · ${time}${entry.message ? " · " + entry.message : ""}</span>`;
      el.appendChild(row);
    }
  } catch {
    el.textContent = "سرور در دسترس نیست";
  }
}

// ---- admin: snapp re-login ----
// Password never touches localStorage/sessionStorage -- kept in a plain
// JS variable only for the life of this page load, cleared on success.

document.getElementById("admin-send-otp").addEventListener("click", async () => {
  const statusEl = document.getElementById("admin-status");
  const password = document.getElementById("admin-password").value;
  const cellphone = document.getElementById("admin-cellphone").value.trim();
  if (!password || !cellphone) {
    statusEl.textContent = "رمز ادمین و شماره موبایل رو وارد کن.";
    return;
  }
  statusEl.textContent = "در حال ارسال کد…";
  try {
    const resp = await fetch("/api/admin/snapp/request-otp", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Password": password },
      body: JSON.stringify({ cellphone }),
    });
    if (resp.ok) {
      statusEl.textContent = "کد تایید پیامک شد.";
      document.getElementById("admin-otp-row").hidden = false;
    } else if (resp.status === 401) {
      statusEl.textContent = "رمز ادمین اشتباهه.";
    } else {
      const err = await resp.json().catch(() => ({}));
      statusEl.textContent = err.detail || "خطا در ارسال کد.";
    }
  } catch {
    statusEl.textContent = "سرور در دسترس نیست.";
  }
});

document.getElementById("admin-verify-otp").addEventListener("click", async () => {
  const statusEl = document.getElementById("admin-status");
  const password = document.getElementById("admin-password").value;
  const cellphone = document.getElementById("admin-cellphone").value.trim();
  const otp = document.getElementById("admin-otp").value.trim();
  if (!otp) {
    statusEl.textContent = "کد تایید رو وارد کن.";
    return;
  }
  statusEl.textContent = "در حال ورود…";
  try {
    const resp = await fetch("/api/admin/snapp/verify-otp", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Password": password },
      body: JSON.stringify({ cellphone, otp }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      statusEl.textContent = `ورود موفق${data.fullname ? " برای " + data.fullname : ""} ✓`;
      document.getElementById("admin-password").value = "";
      document.getElementById("admin-otp").value = "";
      document.getElementById("admin-otp-row").hidden = true;
      loadStatus();
    } else if (resp.status === 401) {
      statusEl.textContent = "رمز ادمین اشتباهه.";
    } else {
      statusEl.textContent = data.detail || "خطا در تایید کد.";
    }
  } catch {
    statusEl.textContent = "سرور در دسترس نیست.";
  }
});

(async function main() {
  await initRoute();
  await loadChart();
  await loadStatus();
  setInterval(loadChart, 60000);
  setInterval(loadStatus, 60000);
})();
