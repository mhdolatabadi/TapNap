const state = {
  mode: "origin",
  newJobRoute: { origin: null, destination: null },
  jobs: [],
  selectedJobId: null,
  pendingDeleteId: null,
};

function apiFetch(url, options = {}) {
  return fetch(url, {
    ...options,
    headers: { ...options.headers, "Content-Type": "application/json" },
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

const originMarker = L.marker([0, 0], { icon: makeIcon("origin"), draggable: true });
const destinationMarker = L.marker([0, 0], { icon: makeIcon("destination"), draggable: true });

const DEFAULT_ORIGIN = { lat: 35.6997, lng: 51.338, label: "میدان آزادی" };
const DEFAULT_DESTINATION = { lat: 35.7448, lng: 51.3752, label: "برج میلاد" };

function applyPointToMap(kind, point) {
  state.newJobRoute[kind] = point;
  const marker = kind === "origin" ? originMarker : destinationMarker;
  if (!map.hasLayer(marker)) marker.addTo(map);
  marker.setLatLng([point.lat, point.lng]);
  document.getElementById(`${kind}-label`).textContent =
    point.label || `${point.lat.toFixed(4)}, ${point.lng.toFixed(4)}`;
}

function initNewJobMap() {
  applyPointToMap("origin", DEFAULT_ORIGIN);
  applyPointToMap("destination", DEFAULT_DESTINATION);
}

const newJobPanel = document.getElementById("new-job-panel");

function showNewJobPanel() {
  newJobPanel.hidden = false;
  // Leaflet can't size itself while its container is display:none --
  // fix it up once the panel is actually visible.
  map.invalidateSize();
  map.fitBounds(
    L.latLngBounds([
      [state.newJobRoute.origin.lat, state.newJobRoute.origin.lng],
      [state.newJobRoute.destination.lat, state.newJobRoute.destination.lng],
    ]),
    { padding: [40, 40] },
  );
}

function hideNewJobPanel() {
  newJobPanel.hidden = true;
}

document.getElementById("new-job-toggle").addEventListener("click", () => {
  if (newJobPanel.hidden) showNewJobPanel();
  else hideNewJobPanel();
});

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
}

document.getElementById("mode-origin").addEventListener("click", () => setMode("origin"));
document.getElementById("mode-destination").addEventListener("click", () => setMode("destination"));

map.on("click", (e) => {
  applyPointToMap(state.mode, { lat: e.latlng.lat, lng: e.latlng.lng, label: null });
});

originMarker.on("dragend", () => {
  const { lat, lng } = originMarker.getLatLng();
  applyPointToMap("origin", { lat, lng, label: null });
});

destinationMarker.on("dragend", () => {
  const { lat, lng } = destinationMarker.getLatLng();
  applyPointToMap("destination", { lat, lng, label: null });
});

document.getElementById("save-job").addEventListener("click", async () => {
  const statusEl = document.getElementById("save-status");
  const name = document.getElementById("job-name").value.trim();
  const { origin, destination } = state.newJobRoute;
  if (!name) {
    statusEl.textContent = "اول یک اسم برای کار بذار.";
    return;
  }
  if (!origin || !destination) {
    statusEl.textContent = "مبدا و مقصد رو روی نقشه مشخص کن.";
    return;
  }
  statusEl.textContent = "در حال افزودن…";
  try {
    const resp = await apiFetch("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ name, origin, destination }),
    });
    if (resp.ok) {
      const job = await resp.json();
      statusEl.textContent = "اضافه شد ✓";
      document.getElementById("job-name").value = "";
      state.selectedJobId = job.id;
      hideNewJobPanel();
      await loadJobs();
    } else {
      const err = await resp.json().catch(() => ({}));
      statusEl.textContent = err.detail || "خطا در افزودن کار.";
    }
  } catch {
    statusEl.textContent = "سرور در دسترس نیست.";
  }
  setTimeout(() => (statusEl.textContent = ""), 4000);
});

// ---- jobs list ----

const STATUS_LABELS = {
  running: "در حال اجرا",
  stopped: "متوقف",
  error: "خطا",
  pending: "در انتظار اولین دریافت",
};

function jobRouteText(job) {
  const o = job.origin.label || `${job.origin.lat.toFixed(4)}, ${job.origin.lng.toFixed(4)}`;
  const d = job.destination.label || `${job.destination.lat.toFixed(4)}, ${job.destination.lng.toFixed(4)}`;
  return `${o} ← ${d}`;
}

function renderJobs() {
  const el = document.getElementById("jobs-list");
  if (!state.jobs.length) {
    el.textContent = "هنوز هیچ کاری تعریف نشده.";
    return;
  }
  el.innerHTML = "";
  for (const job of state.jobs) {
    const card = document.createElement("div");
    card.className = "job-card" + (job.id === state.selectedJobId ? " selected" : "");
    card.dataset.jobId = job.id;

    const info = document.createElement("div");
    info.className = "job-info";
    info.innerHTML = `
      <div class="job-name">${job.name}</div>
      <div class="job-route">${jobRouteText(job)}</div>
    `;
    info.addEventListener("click", () => selectJob(job.id));

    const badge = document.createElement("span");
    badge.className = `status-badge status-${job.status}`;
    badge.textContent = STATUS_LABELS[job.status] || job.status;

    const toggleBtn = document.createElement("button");
    toggleBtn.className = "job-btn";
    toggleBtn.textContent = job.active ? "غیرفعال کردن" : "فعال کردن";
    toggleBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleJobActive(job);
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "job-btn job-btn-danger";
    deleteBtn.textContent = state.pendingDeleteId === job.id ? "مطمئنی؟ دوباره بزن" : "حذف";
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteJob(job);
    });

    const actions = document.createElement("div");
    actions.className = "job-actions";
    actions.append(badge, toggleBtn, deleteBtn);

    card.append(info, actions);
    el.appendChild(card);
  }
}

async function loadJobs() {
  try {
    const resp = await fetch("/api/jobs");
    if (!resp.ok) throw new Error("bad response");
    state.jobs = await resp.json();
  } catch {
    document.getElementById("jobs-list").textContent = "سرور در دسترس نیست";
    return;
  }

  if (state.selectedJobId == null || !state.jobs.some((j) => j.id === state.selectedJobId)) {
    state.selectedJobId = state.jobs.length ? state.jobs[0].id : null;
  }

  renderJobs();
  await Promise.all([loadChart(), loadTravelChart(), loadStatus()]);
}

function selectJob(jobId) {
  state.selectedJobId = jobId;
  renderJobs();
  loadChart();
  loadTravelChart();
  loadStatus();
}

async function toggleJobActive(job) {
  try {
    const resp = await apiFetch(`/api/jobs/${job.id}`, {
      method: "PATCH",
      body: JSON.stringify({ active: !job.active }),
    });
    if (resp.ok) await loadJobs();
  } catch {
    /* transient network failure -- next poll will retry */
  }
}

async function deleteJob(job) {
  if (state.pendingDeleteId !== job.id) {
    // Two-click confirm instead of a native confirm() dialog, which
    // would block the whole page (and this app's own browser-automation
    // testing) until dismissed.
    state.pendingDeleteId = job.id;
    renderJobs();
    setTimeout(() => {
      if (state.pendingDeleteId === job.id) {
        state.pendingDeleteId = null;
        renderJobs();
      }
    }, 5000); // generous window -- a mis-timed second click just re-arms the confirm, never deletes silently
    return;
  }
  state.pendingDeleteId = null;
  try {
    const resp = await apiFetch(`/api/jobs/${job.id}`, { method: "DELETE" });
    if (resp.ok) {
      if (state.selectedJobId === job.id) state.selectedJobId = null;
      await loadJobs();
    }
  } catch {
    /* transient network failure -- user can retry */
  }
}

// ---- chart ----

const PALETTE = ["#4f8cff", "#2ecc71", "#ff5b5b", "#f5a623", "#a06bff", "#00c2c2"];
let chart = null;

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
}

function selectedJob() {
  return state.jobs.find((j) => j.id === state.selectedJobId) || null;
}

async function loadChart() {
  const job = selectedJob();
  const canvas = document.getElementById("price-chart");
  const noDataMsg = document.getElementById("no-data-msg");
  const noJobMsg = document.getElementById("no-job-msg");
  document.getElementById("chart-job-name").textContent = job ? job.name : "—";

  if (!job) {
    if (chart) {
      chart.destroy();
      chart = null;
    }
    canvas.hidden = true;
    noDataMsg.hidden = true;
    noJobMsg.hidden = false;
    return;
  }
  noJobMsg.hidden = true;

  const hours = document.getElementById("range-select").value;
  let rows = [];
  try {
    const resp = await fetch(`/api/jobs/${job.id}/prices?hours=${hours}`);
    if (resp.ok) rows = await resp.json();
  } catch {
    return;
  }

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

document.getElementById("range-select").addEventListener("change", () => {
  loadChart();
  loadTravelChart();
});

// ---- travel-time chart ----

const MODE_LABELS = { car: "ماشین", motorcycle: "موتور", bicycle: "دوچرخه" };
let travelChart = null;

async function loadTravelChart() {
  const job = selectedJob();
  const canvas = document.getElementById("travel-chart");
  const noDataMsg = document.getElementById("travel-no-data-msg");
  const noJobMsg = document.getElementById("travel-no-job-msg");
  document.getElementById("travel-chart-job-name").textContent = job ? job.name : "—";

  if (!job) {
    if (travelChart) {
      travelChart.destroy();
      travelChart = null;
    }
    canvas.hidden = true;
    noDataMsg.hidden = true;
    noJobMsg.hidden = false;
    return;
  }
  noJobMsg.hidden = true;

  const hours = document.getElementById("range-select").value;
  let rows = [];
  try {
    const resp = await fetch(`/api/jobs/${job.id}/travel-times?hours=${hours}`);
    if (resp.ok) rows = await resp.json();
  } catch {
    return;
  }

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
    if (!series.has(row.mode)) series.set(row.mode, new Map());
    series.get(row.mode).set(label, row.duration_seconds / 60);
  }

  const labels = Array.from(labelsSet);
  const datasets = Array.from(series.entries()).map(([mode, points], i) => ({
    label: MODE_LABELS[mode] || mode,
    data: labels.map((l) => (points.has(l) ? points.get(l) : null)),
    borderColor: PALETTE[i % PALETTE.length],
    backgroundColor: PALETTE[i % PALETTE.length],
    spanGaps: true,
    tension: 0.25,
  }));

  if (travelChart) travelChart.destroy();
  travelChart = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { ticks: { color: "#9aa3b2", callback: (v) => `${v} دقیقه` }, grid: { color: "#2a2f3a" } },
        x: { ticks: { color: "#9aa3b2" }, grid: { color: "#2a2f3a" } },
      },
      plugins: { legend: { labels: { color: "#e8eaed" } } },
    },
  });
}

// ---- status ----

const PROVIDER_LABELS = {
  snapp: "اسنپ",
  tapsi: "تپسی",
  neshan_car: "نشان (ماشین)",
  neshan_motorcycle: "نشان (موتور)",
  neshan_bicycle: "نشان (دوچرخه)",
};

async function loadStatus() {
  const job = selectedJob();
  const el = document.getElementById("status-list");
  document.getElementById("status-job-name").textContent = job ? job.name : "—";

  if (!job) {
    el.textContent = "هیچ کاری انتخاب نشده";
    return;
  }

  try {
    const resp = await fetch(`/api/jobs/${job.id}`);
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
      const providerLabel = PROVIDER_LABELS[entry.provider] || entry.provider;
      row.innerHTML = `<span>${providerLabel}</span><span class="${entry.ok ? "status-ok" : "status-bad"}">${
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
  initNewJobMap();
  await loadJobs();
  setInterval(loadJobs, 30000);
})();
