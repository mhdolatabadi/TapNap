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

document.getElementById("cancel-job").addEventListener("click", () => {
  document.getElementById("job-name").value = "";
  document.getElementById("save-status").textContent = "";
  hideNewJobPanel();
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

// Left-edge colour on each job card -- a quiet status cue that doesn't need
// the reader to parse the badge text.
const STATUS_COLORS = {
  running: "#2ecc71",
  error: "#ff5b5b",
  pending: "#e8b341",
  stopped: "#909aa8",
};

function jobRouteText(job) {
  const o = job.origin.label || `${job.origin.lat.toFixed(4)}, ${job.origin.lng.toFixed(4)}`;
  const d = job.destination.label || `${job.destination.lat.toFixed(4)}, ${job.destination.lng.toFixed(4)}`;
  return `${o} ← ${d}`;
}

// Cheapest current price per provider (a provider may run several services --
// take the "starting from" figure) so the job card can answer "who's cheaper
// right now" without opening the chart.
function priceSnapshot(job) {
  if (!job.latest_prices || !job.latest_prices.length) return null;
  const minByProvider = new Map();
  for (const p of job.latest_prices) {
    const cur = minByProvider.get(p.provider);
    if (cur == null || p.price < cur) minByProvider.set(p.provider, p.price);
  }
  const entries = Array.from(minByProvider.entries());
  const [cheapestProvider] = entries.reduce((a, b) => (b[1] < a[1] ? b : a));
  return { entries, cheapestProvider };
}

// One line above the job list: across every job with a current price, how
// many routes is each provider currently the cheaper one on.
function renderJobsSummary() {
  const el = document.getElementById("jobs-summary");
  const snapshots = state.jobs.map(priceSnapshot).filter(Boolean);
  if (!snapshots.length) {
    el.textContent = "";
    return;
  }
  const counts = new Map();
  for (const s of snapshots) counts.set(s.cheapestProvider, (counts.get(s.cheapestProvider) || 0) + 1);
  const parts = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([provider, count]) => `${PROVIDER_LABELS[provider] || provider} در ${count.toLocaleString("fa-IR")} مسیر`);
  el.textContent = `از ${snapshots.length.toLocaleString("fa-IR")} مسیر با قیمت لحظه‌ای — ارزون‌تر: ${parts.join(" · ")}`;
}

function renderJobs() {
  renderJobsSummary();
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
    card.style.setProperty("--card-status", STATUS_COLORS[job.status] || "#2a303c");

    const info = document.createElement("div");
    info.className = "job-info";
    info.innerHTML = `
      <div class="job-name">${job.name}</div>
      <div class="job-route">${jobRouteText(job)}</div>
    `;
    const snapshot = priceSnapshot(job);
    if (snapshot) {
      const row = document.createElement("div");
      row.className = "job-snapshot";
      row.innerHTML = snapshot.entries
        .map(([provider, price]) => {
          const label = PROVIDER_LABELS[provider] || provider;
          const cheap = provider === snapshot.cheapestProvider ? " cheapest" : "";
          return `<span class="job-snapshot-item${cheap}" data-provider="${provider}">${label}: ${Math.round(price).toLocaleString("fa-IR")} تومان</span>`;
        })
        .join("");
      info.appendChild(row);
    }
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
  await Promise.all([loadChart(), loadTravelChart(), loadByDayCharts(), loadStatus()]);
}

function selectJob(jobId) {
  state.selectedJobId = jobId;
  renderJobs();
  resetAllZoom();
  // Drop every chart up front so the switch never flashes the old job's data.
  chart = destroyChart(chart);
  travelChart = destroyChart(travelChart);
  priceByDayChart = destroyChart(priceByDayChart);
  travelByDayChart = destroyChart(travelByDayChart);
  loadChart();
  loadTravelChart();
  loadByDayCharts();
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

// ---- charts ----

const MODE_LABELS = { car: "ماشین", motorcycle: "موتور", bicycle: "دوچرخه" };
const TICK_COLOR = "#909aa8";
const GRID_COLOR = "#232833";

// Provider identity colours (mirror the --snapp / --tapsi CSS tokens) plus
// travel-mode colours kept off the provider hues. A series' colour is derived
// from its label, not its position, so the same service is always the same
// colour across refreshes and range changes.
const SNAPP_COLOR = "#1fbf6b";
const TAPSI_COLOR = "#ff6a3d";
const MODE_COLORS = { "ماشین": "#6b8cc7", "موتور": "#b48ce0", "دوچرخه": "#57c8c8" };
const PALETTE = ["#6b8cc7", "#b48ce0", "#57c8c8", "#e8b341", "#a06bff", "#00c2c2"];

function colorForLabel(label, i) {
  if (label.startsWith("اسنپ")) return SNAPP_COLOR;
  if (label.startsWith("تپسی")) return TAPSI_COLOR;
  if (MODE_COLORS[label]) return MODE_COLORS[label];
  return PALETTE[i % PALETTE.length];
}

// Both providers expose several services (اسنپ / سفر اشتراکی / اکوپلاس …),
// and they'd otherwise all be one flat provider colour. Keep the hue as the
// provider's identity but lighten each extra service toward white and dash it,
// so the primary service stays the solid brand line.
function shade(hex, step) {
  const amt = Math.min(0.55, step * 0.2);
  const n = parseInt(hex.slice(1), 16);
  const mix = (c) => Math.round(c + (255 - c) * amt);
  return `rgb(${mix(n >> 16)}, ${mix((n >> 8) & 255)}, ${mix(n & 255)})`;
}

function providerOf(label) {
  if (label.startsWith("اسنپ")) return "snapp";
  if (label.startsWith("تپسی")) return "tapsi";
  return label;
}

// chartjs-plugin-zoom ships as a UMD global and doesn't self-register in that
// build -- register it once so every chart below can opt into drag-to-zoom.
if (window.ChartZoom) Chart.register(window.ChartZoom);

let chart = null;
let travelChart = null;
let priceByDayChart = null;
let travelByDayChart = null;

// Render `config` into an existing chart in place when possible, so a periodic
// refresh doesn't wipe the user's legend toggles (hidden series), lose the
// current drag-zoom window, or flash a full redraw. Rebuilds only when there's
// no chart yet.
function renderChart(existing, canvas, config) {
  if (!existing) return new Chart(canvas, config);

  // Preserve which series the user has toggled off, keyed by label so it
  // survives series being added/removed/reordered between refreshes.
  const hiddenLabels = new Set();
  existing.data.datasets.forEach((ds, i) => {
    if (!existing.isDatasetVisible(i)) hiddenLabels.add(ds.label);
  });

  for (const ds of config.data.datasets) {
    if (hiddenLabels.has(ds.label)) ds.hidden = true;
  }

  existing.data.labels = config.data.labels;
  existing.data.datasets = config.data.datasets;
  // "none" keeps the plugin-zoom scale window intact across the refresh.
  existing.update("none");
  return existing;
}

// Tear a chart down so a canvas can't keep showing a previous job's data
// while the next job's fetch is still in flight (or when it has no data).
function destroyChart(existing) {
  if (existing) existing.destroy();
  return null;
}

// Shared options: dark ticks/grid + drag-to-zoom on the x axis. `group` links
// charts that share an x axis so a drag on one zooms the whole group;
// `resetBtnId` is the button revealed once that group is zoomed in.
function chartOptions(group, resetBtnId, yTickCallback) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    // Hover anywhere along the x axis -- not only dead-on a point -- and show
    // every series' value at that time in one tooltip.
    interaction: { mode: "index", intersect: false, axis: "x" },
    scales: {
      y: { ticks: { color: TICK_COLOR, font: { family: "Vazirmatn" }, callback: yTickCallback }, grid: { color: GRID_COLOR } },
      x: { ticks: { color: TICK_COLOR, font: { family: "Vazirmatn" }, maxRotation: 0, autoSkipPadding: 12 }, grid: { color: GRID_COLOR } },
    },
    plugins: {
      legend: {
        labels: {
          color: "#eef1f5",
          usePointStyle: true,
          pointStyle: "line",
          font: { family: "Vazirmatn" },
        },
      },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const v = ctx.parsed.y;
            if (v == null) return null;
            const shown = yTickCallback
              ? yTickCallback(Math.round(v))
              : `${Math.round(v).toLocaleString("fa-IR")} تومان`;
            return `${ctx.dataset.label}: ${shown}`;
          },
        },
      },
      zoom: {
        zoom: {
          drag: {
            enabled: true,
            backgroundColor: "rgba(238,241,245,0.12)",
            borderColor: "rgba(238,241,245,0.45)",
            borderWidth: 1,
          },
          mode: "x",
          onZoomComplete: ({ chart: c }) => syncGroupZoom(group, c),
        },
      },
    },
  };
}

function lineDatasets(series) {
  const ordByProvider = {};
  return Array.from(series.entries()).map(([label, points], i) => {
    const base = colorForLabel(label, i);
    const prov = providerOf(label);
    const ord = (ordByProvider[prov] = (ordByProvider[prov] ?? -1) + 1);
    const color = ord === 0 ? base : shade(base, ord);
    return {
      label,
      data: points,
      borderColor: color,
      backgroundColor: color,
      borderWidth: 2,
      borderDash: ord === 0 ? [] : [6, 3],
      pointRadius: 2,
      pointHoverRadius: 5,
      pointHitRadius: 12,
      spanGaps: true,
      tension: 0.25,
    };
  });
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
}

function selectedJob() {
  return state.jobs.find((j) => j.id === state.selectedJobId) || null;
}

// Turn a flat list of rows into aligned {labels, datasets}, one label per
// distinct formatted timestamp and one dataset per series key.
function timeSeries(rows, keyFn, valueFn) {
  const labelsSet = new Set();
  const series = new Map();
  for (const row of rows) {
    const label = formatTime(row.checked_at);
    labelsSet.add(label);
    const key = keyFn(row);
    if (!series.has(key)) series.set(key, new Map());
    series.get(key).set(label, valueFn(row));
  }
  const labels = Array.from(labelsSet);
  const aligned = new Map();
  for (const [key, points] of series) {
    aligned.set(key, labels.map((l) => (points.has(l) ? points.get(l) : null)));
  }
  return { labels, datasets: lineDatasets(aligned) };
}

const currentHours = () => document.getElementById("range-select").value;

async function fetchJson(url) {
  try {
    const resp = await fetch(url);
    if (resp.ok) return await resp.json();
  } catch {
    /* transient -- next poll retries */
  }
  return null;
}

async function loadChart() {
  const job = selectedJob();
  const canvas = document.getElementById("price-chart");
  const noDataMsg = document.getElementById("no-data-msg");
  const noJobMsg = document.getElementById("no-job-msg");
  document.getElementById("chart-job-name").textContent = job ? job.name : "—";

  if (!job) {
    chart = destroyChart(chart);
    canvas.hidden = true;
    noDataMsg.hidden = true;
    noJobMsg.hidden = false;
    return;
  }
  noJobMsg.hidden = true;

  const rows = await fetchJson(`/api/jobs/${job.id}/prices?hours=${currentHours()}`);
  if (rows == null || job.id !== state.selectedJobId) return;

  if (rows.length === 0) {
    chart = destroyChart(chart);
    noDataMsg.hidden = false;
    canvas.hidden = true;
    return;
  }
  noDataMsg.hidden = true;
  canvas.hidden = false;

  const { labels, datasets } = timeSeries(
    rows,
    (r) => `${PROVIDER_LABELS[r.provider] || r.provider} — ${r.service_name}`,
    (r) => r.price,
  );

  chart = renderChart(chart, canvas, {
    type: "line",
    data: { labels, datasets },
    options: chartOptions("timeline", "price-chart-reset"),
  });
}

async function loadTravelChart() {
  const job = selectedJob();
  const canvas = document.getElementById("travel-chart");
  const noDataMsg = document.getElementById("travel-no-data-msg");
  // The price and travel charts share one panel and one "no job" message.
  const noJobMsg = document.getElementById("no-job-msg");

  if (!job) {
    travelChart = destroyChart(travelChart);
    canvas.hidden = true;
    noDataMsg.hidden = true;
    noJobMsg.hidden = false;
    return;
  }
  noJobMsg.hidden = true;

  const rows = await fetchJson(`/api/jobs/${job.id}/travel-times?hours=${currentHours()}`);
  if (rows == null || job.id !== state.selectedJobId) return;

  if (rows.length === 0) {
    travelChart = destroyChart(travelChart);
    noDataMsg.hidden = false;
    canvas.hidden = true;
    return;
  }
  noDataMsg.hidden = true;
  canvas.hidden = false;

  const { labels, datasets } = timeSeries(
    rows,
    (r) => MODE_LABELS[r.mode] || r.mode,
    (r) => r.duration_seconds / 60,
  );

  travelChart = renderChart(travelChart, canvas, {
    type: "line",
    data: { labels, datasets },
    options: chartOptions("timeline", "travel-chart-reset", (v) => `${v} دقیقه`),
  });
}

// ---- day-over-day charts (value at a fixed time of day) ----

const TOD_TOLERANCE_MIN = 90; // how far from the picked time a sample may be

function todMinutes() {
  const [h, m] = document.getElementById("tod-time").value.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

// For each calendar day in `rows`, keep -- per series -- the single sample
// whose local clock time is closest to `targetMin` (within tolerance).
function dailyAtTime(rows, keyFn, valueFn, targetMin) {
  const days = new Map(); // dayKey -> { date, series: Map<key, {diff, value}> }
  for (const row of rows) {
    const d = new Date(row.checked_at);
    const minute = d.getHours() * 60 + d.getMinutes();
    let diff = Math.abs(minute - targetMin);
    diff = Math.min(diff, 1440 - diff); // wrap around midnight
    if (diff > TOD_TOLERANCE_MIN) continue;

    const dayKey = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    if (!days.has(dayKey)) days.set(dayKey, { date: d, series: new Map() });
    const bucket = days.get(dayKey).series;
    const key = keyFn(row);
    const prev = bucket.get(key);
    if (!prev || diff < prev.diff) bucket.set(key, { diff, value: valueFn(row) });
  }

  const dayKeys = Array.from(days.keys()).sort(
    (a, b) => days.get(a).date - days.get(b).date,
  );
  const labels = dayKeys.map((k) =>
    days.get(k).date.toLocaleDateString("fa-IR", { weekday: "long", day: "numeric", month: "short" }),
  );

  const seriesKeys = new Set();
  for (const k of dayKeys) for (const s of days.get(k).series.keys()) seriesKeys.add(s);

  const aligned = new Map();
  for (const s of seriesKeys) {
    aligned.set(
      s,
      dayKeys.map((k) => {
        const hit = days.get(k).series.get(s);
        return hit ? hit.value : null;
      }),
    );
  }
  return { labels, datasets: lineDatasets(aligned) };
}

async function loadByDayCharts() {
  const job = selectedJob();
  const noJobMsg = document.getElementById("by-day-no-job-msg");
  document.getElementById("by-day-job-name").textContent = job ? job.name : "—";

  const priceCanvas = document.getElementById("price-by-day-chart");
  const travelCanvas = document.getElementById("travel-by-day-chart");
  const priceNoData = document.getElementById("price-by-day-no-data");
  const travelNoData = document.getElementById("travel-by-day-no-data");

  if (!job) {
    priceByDayChart = destroyChart(priceByDayChart);
    travelByDayChart = destroyChart(travelByDayChart);
    priceCanvas.hidden = true;
    travelCanvas.hidden = true;
    priceNoData.hidden = true;
    travelNoData.hidden = true;
    noJobMsg.hidden = false;
    return;
  }
  noJobMsg.hidden = true;

  const days = Number(document.getElementById("tod-days").value);
  const targetMin = todMinutes();
  const hours = days * 24;

  const [priceRows, travelRows] = await Promise.all([
    fetchJson(`/api/jobs/${job.id}/prices?hours=${hours}`),
    fetchJson(`/api/jobs/${job.id}/travel-times?hours=${hours}`),
  ]);
  if (job.id !== state.selectedJobId) return;

  if (priceRows != null) {
    const { labels, datasets } = dailyAtTime(
      priceRows,
      (r) => `${PROVIDER_LABELS[r.provider] || r.provider} — ${r.service_name}`,
      (r) => r.price,
      targetMin,
    );
    if (labels.length === 0) {
      priceByDayChart = destroyChart(priceByDayChart);
      priceNoData.hidden = false;
      priceCanvas.hidden = true;
    } else {
      priceNoData.hidden = true;
      priceCanvas.hidden = false;
      priceByDayChart = renderChart(priceByDayChart, priceCanvas, {
        type: "line",
        data: { labels, datasets },
        options: chartOptions("daily", "price-by-day-reset"),
      });
    }
  }

  if (travelRows != null) {
    const { labels, datasets } = dailyAtTime(
      travelRows,
      (r) => MODE_LABELS[r.mode] || r.mode,
      (r) => r.duration_seconds / 60,
      targetMin,
    );
    if (labels.length === 0) {
      travelByDayChart = destroyChart(travelByDayChart);
      travelNoData.hidden = false;
      travelCanvas.hidden = true;
    } else {
      travelNoData.hidden = true;
      travelCanvas.hidden = false;
      travelByDayChart = renderChart(travelByDayChart, travelCanvas, {
        type: "line",
        data: { labels, datasets },
        options: chartOptions("daily", "travel-by-day-reset", (v) => `${v} دقیقه`),
      });
    }
  }
}

// ---- linked (grouped) zoom ----
//
// Charts in the same group share an x axis (the "timeline" pair covers the
// same recent window; the "daily" pair covers the same span of days), so a
// drag-zoom on any one of them applies the identical x window to the others.

const ZOOM_GROUPS = {
  timeline: { charts: () => [chart, travelChart], buttons: ["price-chart-reset", "travel-chart-reset"] },
  daily: { charts: () => [priceByDayChart, travelByDayChart], buttons: ["price-by-day-reset", "travel-by-day-reset"] },
};
const BUTTON_GROUP = {
  "price-chart-reset": "timeline",
  "travel-chart-reset": "timeline",
  "price-by-day-reset": "daily",
  "travel-by-day-reset": "daily",
};

let syncingZoom = false;

function setGroupResetButtons(group, zoomed) {
  for (const id of ZOOM_GROUPS[group].buttons) document.getElementById(id).hidden = !zoomed;
}

// Copy `source`'s current x window onto its group-mates. Both charts in a
// group are built from the same fetch cycle, so their category indices line
// up and an index range transfers directly.
function syncGroupZoom(group, source) {
  const zoomed = source.isZoomedOrPanned();
  setGroupResetButtons(group, zoomed);
  if (syncingZoom) return;
  syncingZoom = true;
  try {
    const { min, max } = source.scales.x;
    for (const c of ZOOM_GROUPS[group].charts()) {
      if (!c || c === source) continue;
      if (zoomed) c.zoomScale("x", { min, max }, "none");
      else c.resetZoom("none");
    }
  } finally {
    syncingZoom = false;
  }
}

function resetGroupZoom(group) {
  syncingZoom = true;
  try {
    for (const c of ZOOM_GROUPS[group].charts()) if (c && c.isZoomedOrPanned()) c.resetZoom();
  } finally {
    syncingZoom = false;
  }
  setGroupResetButtons(group, false);
}

function wireZoomResets() {
  for (const id of Object.keys(BUTTON_GROUP)) {
    document.getElementById(id).addEventListener("click", () => resetGroupZoom(BUTTON_GROUP[id]));
  }
}

function resetAllZoom() {
  resetGroupZoom("timeline");
  resetGroupZoom("daily");
}

document.getElementById("range-select").addEventListener("change", () => {
  resetAllZoom();
  loadChart();
  loadTravelChart();
});

for (const id of ["tod-time", "tod-days"]) {
  document.getElementById(id).addEventListener("change", () => {
    resetAllZoom();
    loadByDayCharts();
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

(async function main() {
  initNewJobMap();
  wireZoomResets();
  await loadJobs();
  setInterval(loadJobs, 30000);
})();
