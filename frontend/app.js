const state = {
  mode: "origin",
  newJobRoute: { origin: null, destination: null },
  jobs: [],
  selectedJobId: null,
  pendingDeleteId: null,
  // Which job cards currently have their settings (price/travel-time/
  // round-trip switches) panel expanded -- kept outside renderJobs() so a
  // card the user has open stays open across the 30s poll's re-render.
  openSettingsIds: new Set(),
};

// Small hand-drawn outline icons (no external icon font -- this app already
// vendors its own JS/CSS locally rather than pulling from a CDN). Each is a
// self-contained, static SVG string (no interpolated data, safe to drop
// straight into innerHTML) sized via the .icon CSS class (1em square) so it
// scales with whatever button/heading it sits in.
const ICONS = {
  power:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2v8"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/></svg>',
  tag:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.6 2.6a2 2 0 0 0-1.4-.6H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.8 8.8a2 2 0 0 0 2.8 0l7.2-7.2a2 2 0 0 0 0-2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/></svg>',
  clock:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
  swap:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7h11l-3-3"/><path d="M18 7l-3 3"/><path d="M17 17H6l3 3"/><path d="M6 17l3-3"/></svg>',
  trash:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
  pin:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s7-7.58 7-12a7 7 0 1 0-14 0c0 4.42 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/></svg>',
  chart:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/></svg>',
  calendar:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4"/><path d="M8 3v4"/><path d="M3 11h18"/></svg>',
  pulse:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2 8 4-16 2 8h6"/></svg>',
  settings:
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
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

function resetNewJobForm() {
  document.getElementById("job-name").value = "";
  document.getElementById("track-price").checked = true;
  document.getElementById("track-travel-time").checked = true;
}

document.getElementById("cancel-job").addEventListener("click", () => {
  resetNewJobForm();
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
  const trackPrice = document.getElementById("track-price").checked;
  const trackTravelTime = document.getElementById("track-travel-time").checked;
  if (!name) {
    statusEl.textContent = "اول یک اسم برای کار بذار.";
    return;
  }
  if (!origin || !destination) {
    statusEl.textContent = "مبدا و مقصد رو روی نقشه مشخص کن.";
    return;
  }
  if (!trackPrice && !trackTravelTime) {
    statusEl.textContent = "حداقل یکی از قیمت یا زمان مسیر رو انتخاب کن.";
    return;
  }
  statusEl.textContent = "در حال افزودن…";
  try {
    const resp = await apiFetch("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        name, origin, destination,
        track_price: trackPrice, track_travel_time: trackTravelTime,
      }),
    });
    if (resp.ok) {
      const job = await resp.json();
      statusEl.textContent = "اضافه شد ✓";
      resetNewJobForm();
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

// Each provider's current "standard" service price (job.latest_prices is
// already one row per provider -- see db.get_latest_prices) so the job card
// can answer "who's cheaper right now" without opening the chart. Keeping
// the lowest if a provider ever shows up more than once here is just
// defensive -- the backend already picks one row per provider.
function priceSnapshot(job) {
  if (!job.latest_prices || !job.latest_prices.length) return null;
  const priceByProvider = new Map();
  let updatedAt = job.latest_prices[0].checked_at;
  for (const p of job.latest_prices) {
    const cur = priceByProvider.get(p.provider);
    if (cur == null || p.price < cur) priceByProvider.set(p.provider, p.price);
    if (p.checked_at > updatedAt) updatedAt = p.checked_at;
  }
  const entries = Array.from(priceByProvider.entries());
  const [cheapestProvider] = entries.reduce((a, b) => (b[1] < a[1] ? b : a));
  return { entries, cheapestProvider, updatedAt };
}

// "۵ دقیقه پیش" etc, so a job card shows how fresh its snapshot price is --
// without this, a stale price (scheduler stuck, provider down for hours)
// looks identical to a fresh one.
function relativeTime(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "همین الان";
  if (minutes < 60) return `${minutes.toLocaleString("fa-IR")} دقیقه پیش`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours.toLocaleString("fa-IR")} ساعت پیش`;
  return `${Math.round(hours / 24).toLocaleString("fa-IR")} روز پیش`;
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

// A job in "error" state buried in a long list is easy to miss -- surface
// it here so a systemic problem (an expired token affecting every job at
// once) is visible without opening each job to find out.
function renderJobsErrorBanner() {
  const el = document.getElementById("jobs-error-banner");
  const errored = state.jobs.filter((j) => j.status === "error");
  if (!errored.length) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.textContent =
    errored.length === 1
      ? `«${errored[0].name}» با خطا مواجه شده — برای جزئیات، وضعیت دریافتش رو پایین صفحه ببین.`
      : `${errored.length.toLocaleString("fa-IR")} کار با خطا مواجه شدن — برای جزئیات هرکدوم، وضعیت دریافتش رو پایین صفحه ببین.`;
}

const MAX_JOBS_PER_USER = 3;

function updateNewJobToggle() {
  const btn = document.getElementById("new-job-toggle");
  const atLimit = state.jobs.length >= MAX_JOBS_PER_USER;
  btn.disabled = atLimit;
  btn.title = atLimit ? "هر حساب حداکثر ۳ مسیر می‌تونه داشته باشه" : "کار جدید";
}

// A labeled on/off switch for a job-card action. onToggle receives the
// desired new state and must resolve to true (applied -- the caller's own
// loadJobs() re-render, on success, replaces this element anyway) or false
// (rejected, e.g. the tracking-both-off guard or the 3-job cap) so the
// switch can be snapped back rather than showing a state the server
// refused.
function makeSwitchRow(iconSvg, labelText, checked, onToggle, title) {
  const row = document.createElement("label");
  row.className = "switch-row";
  if (title) row.title = title;
  row.addEventListener("click", (e) => e.stopPropagation());

  const labelSpan = document.createElement("span");
  labelSpan.className = "switch-label";
  labelSpan.innerHTML = iconSvg + labelText;

  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  input.addEventListener("change", async () => {
    const desired = input.checked;
    input.disabled = true;
    const ok = await onToggle(desired);
    if (!ok) input.checked = !desired;
    input.disabled = false;
  });

  const slider = document.createElement("span");
  slider.className = "switch-slider";

  const switchWrap = document.createElement("span");
  switchWrap.className = "switch";
  switchWrap.append(input, slider);

  row.append(labelSpan, switchWrap);
  return row;
}

function renderJobs() {
  renderJobsErrorBanner();
  renderJobsSummary();
  updateNewJobToggle();
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

    const cardTop = document.createElement("div");
    cardTop.className = "job-card-top";

    const info = document.createElement("div");
    info.className = "job-info";
    const returnTag = job.is_return_leg
      ? `<span class="job-return-tag">برگشتِ یک مسیر دیگه</span>`
      : "";
    info.innerHTML = `
      <div class="job-name">${job.name}</div>
      <div class="job-route">${jobRouteText(job)} ${returnTag}</div>
    `;
    const snapshot = priceSnapshot(job);
    if (snapshot) {
      const row = document.createElement("div");
      row.className = "job-snapshot";
      const chips = snapshot.entries
        .map(([provider, price]) => {
          const label = PROVIDER_LABELS[provider] || provider;
          const cheap = provider === snapshot.cheapestProvider ? " cheapest" : "";
          return `<span class="job-snapshot-item${cheap}" data-provider="${provider}">${label}: ${Math.round(price).toLocaleString("fa-IR")} تومان</span>`;
        })
        .join("");
      row.innerHTML = `${chips}<span class="job-snapshot-time">${relativeTime(snapshot.updatedAt)}</span>`;
      info.appendChild(row);
    }
    info.addEventListener("click", () => selectJob(job.id));

    const badge = document.createElement("span");
    badge.className = `status-badge status-${job.status}`;
    badge.textContent = STATUS_LABELS[job.status] || job.status;

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "job-btn job-btn-danger";
    deleteBtn.innerHTML =
      state.pendingDeleteId === job.id ? ICONS.trash + "مطمئنی؟ دوباره بزن" : ICONS.trash + "حذف";
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteJob(job);
    });

    // Settings panel: the three "what should this route poll" switches --
    // configured once and rarely touched again, so they're tucked behind
    // the gear button below rather than sitting in the always-visible row
    // alongside فعال/حذف.
    const settingsOpen = state.openSettingsIds.has(job.id);

    const settingsPanel = document.createElement("div");
    settingsPanel.className = "job-settings";
    settingsPanel.hidden = !settingsOpen;

    const settingsLabel = document.createElement("span");
    settingsLabel.className = "job-settings-label";
    settingsLabel.textContent = "این مسیر چی رو پایش کنه";
    settingsPanel.append(settingsLabel);

    settingsPanel.append(
      makeSwitchRow(ICONS.tag, "قیمت", job.track_price, (checked) =>
        toggleTracking(job, { track_price: checked }), "پایش قیمت (اسنپ و تپسی)"),
    );

    settingsPanel.append(
      makeSwitchRow(ICONS.clock, "زمان مسیر", job.track_travel_time, (checked) =>
        toggleTracking(job, { track_travel_time: checked }), "پایش زمان مسیر (نشان)"),
    );

    // A return leg's own round-trip state isn't meaningful (it can't have
    // a return leg of its own) -- only base jobs get this switch.
    if (!job.is_return_leg) {
      const roundTripOn = job.round_trip_job_id != null;
      settingsPanel.append(
        makeSwitchRow(ICONS.swap, "رفت‌وبرگشت", roundTripOn, (checked) => toggleRoundTrip(job, checked),
          "قیمت مسیر برگشت (مقصد به مبدا) هم پایش بشه"),
      );
    }

    const settingsToggle = document.createElement("button");
    settingsToggle.className = "job-settings-toggle" + (settingsOpen ? " open" : "");
    settingsToggle.title = "تنظیمات پایش";
    settingsToggle.innerHTML = ICONS.settings;
    settingsToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const nowOpen = settingsPanel.hidden; // about to toggle
      settingsPanel.hidden = !nowOpen;
      settingsToggle.classList.toggle("open", nowOpen);
      if (nowOpen) state.openSettingsIds.add(job.id);
      else state.openSettingsIds.delete(job.id);
    });

    const actions = document.createElement("div");
    actions.className = "job-actions";
    actions.append(
      badge,
      makeSwitchRow(ICONS.power, "فعال", job.active, () => toggleJobActive(job)),
      settingsToggle,
      deleteBtn,
    );

    cardTop.append(info, actions);
    card.append(cardTop, settingsPanel);
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
  await Promise.all([loadChart(), loadByDayCharts(), loadStatus()]);
}

function selectJob(jobId) {
  state.selectedJobId = jobId;
  renderJobs();
  resetAllZoom();
  // Drop every chart up front so the switch never flashes the old job's data.
  chart = destroyChart(chart);
  dayChart = destroyChart(dayChart);
  loadChart();
  loadByDayCharts();
  loadStatus();
}

// Each of these three returns true (applied) or false (rejected/failed) so
// the switch that triggered it (see makeSwitchRow) knows whether to snap
// back to its previous position instead of showing a state the server
// never actually accepted.

async function toggleJobActive(job) {
  try {
    const resp = await apiFetch(`/api/jobs/${job.id}`, {
      method: "PATCH",
      body: JSON.stringify({ active: !job.active }),
    });
    if (resp.ok) {
      await loadJobs();
      return true;
    }
  } catch {
    /* transient network failure -- next poll will retry */
  }
  return false;
}

async function toggleTracking(job, change) {
  const body = {
    track_price: job.track_price,
    track_travel_time: job.track_travel_time,
    ...change,
  };
  try {
    const resp = await apiFetch(`/api/jobs/${job.id}/tracking`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      await loadJobs();
      return true;
    }
    // Rejected e.g. for turning off the only tracking method left on --
    // reuses the error banner the same way toggleRoundTrip does.
    const err = await resp.json().catch(() => ({}));
    const el = document.getElementById("jobs-error-banner");
    el.hidden = false;
    el.textContent = err.detail || "خطا در تغییر پایش.";
  } catch {
    /* transient network failure -- user can retry */
  }
  return false;
}

async function toggleRoundTrip(job, enabled) {
  try {
    const resp = await apiFetch(`/api/jobs/${job.id}/round-trip`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
    if (resp.ok) {
      await loadJobs();
      return true;
    }
    // Reuses the error banner (normally for a job stuck in "error"
    // status) -- the next render (30s poll, or any other job action)
    // recomputes it back to normal once this stops being relevant.
    const err = await resp.json().catch(() => ({}));
    const el = document.getElementById("jobs-error-banner");
    el.hidden = false;
    el.textContent = err.detail || "خطا در تغییر رفت‌وبرگشت.";
  } catch {
    /* transient network failure -- user can retry */
  }
  return false;
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
let dayChart = null;

// Render `config` into an existing chart in place when possible, so a periodic
// refresh doesn't wipe the user's legend toggles (hidden series), lose the
// current drag-zoom window, or flash a full redraw. Rebuilds only when there's
// no chart yet.
function renderChart(existing, canvas, config) {
  if (!existing) return new Chart(canvas, config);

  // Preserve each already-known series' shown/hidden state, keyed by label
  // so it survives series being added/removed/reordered between refreshes --
  // both a manual toggle AND a series that started hidden by lineDatasets()'s
  // own default (secondary services) need to stick, not just "was hidden".
  // A label lineDatasets() hasn't produced before (a service appearing for
  // the first time) keeps whatever default `hidden` it was just given.
  const visibility = new Map();
  existing.data.datasets.forEach((ds, i) => {
    visibility.set(ds.label, existing.isDatasetVisible(i));
  });

  for (const ds of config.data.datasets) {
    if (visibility.has(ds.label)) ds.hidden = !visibility.get(ds.label);
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

const formatToman = (v) => `${Math.round(v).toLocaleString("fa-IR")} تومان`;
const formatMinutes = (v) => `${Math.round(v).toLocaleString("fa-IR")} دقیقه`;

// Shared options for a chart combining price (left axis, "y") and
// travel-time (right axis, "y1") series in one plot -- drag-to-zoom on the
// x axis, `resetBtnId` is the button revealed once zoomed in. Since each
// panel is now a single chart, zoom just applies to itself; no more
// syncing a pair of charts against each other.
function chartOptions(resetBtnId) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    // Hover anywhere along the x axis -- not only dead-on a point -- and show
    // every series' value at that time in one tooltip.
    interaction: { mode: "index", intersect: false, axis: "x" },
    scales: {
      y: {
        position: "left",
        ticks: { color: TICK_COLOR, font: { family: "Vazirmatn" }, callback: formatToman },
        grid: { color: GRID_COLOR },
      },
      y1: {
        position: "right",
        ticks: { color: TICK_COLOR, font: { family: "Vazirmatn" }, callback: formatMinutes },
        // Two overlapping gridlines from both axes would just look like
        // visual noise -- only the price axis draws them.
        grid: { drawOnChartArea: false },
      },
      x: { ticks: { color: TICK_COLOR, font: { family: "Vazirmatn" }, maxRotation: 0, autoSkipPadding: 12 }, grid: { color: GRID_COLOR } },
    },
    plugins: {
      // "right" made sense with 4-5 series; combining price and travel-time
      // onto one chart roughly doubles that, and a narrow vertical legend
      // column truncates long labels regardless of how wide the chart
      // itself is (confirmed against real data before this fix -- widening
      // the whole page didn't help, only moving the legend did). "bottom"
      // wraps across the chart's full width instead.
      legend: {
        position: "bottom",
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
            const shown = ctx.dataset.yAxisID === "y1" ? formatMinutes(v) : formatToman(v);
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
          onZoomComplete: ({ chart: c }) => {
            document.getElementById(resetBtnId).hidden = !c.isZoomedOrPanned();
          },
        },
      },
    },
  };
}

function lineDatasets(series, yAxisID) {
  const ordByProvider = {};
  return Array.from(series.entries()).map(([label, points], i) => {
    const base = colorForLabel(label, i);
    const prov = providerOf(label);
    const ord = (ordByProvider[prov] = (ordByProvider[prov] ?? -1) + 1);
    const color = ord === 0 ? base : shade(base, ord);
    return {
      label,
      data: points,
      yAxisID,
      borderColor: color,
      backgroundColor: color,
      borderWidth: 2,
      borderDash: ord === 0 ? [] : [6, 3],
      // Default view is just each provider's standard service (a provider
      // can list several -- اسنپ/اکوپلاس/سفر اشتراکی) -- the rest stay one
      // legend click away rather than cluttering the chart by default.
      hidden: ord !== 0,
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

// Combines one or more row sets (e.g. price rows + travel-time rows) onto
// one shared timestamp axis, each contributing its own series on its own
// y axis -- e.g. {rows: priceRows, keyFn, valueFn, yAxisID: "y"} and
// {rows: travelRows, ..., yAxisID: "y1"} for a chart plotting both at once.
// Price and travel-time poll on different schedules (see scheduler.py), so
// their timestamps don't line up -- rows from every group are merged and
// sorted chronologically first so labels land in true time order (the
// single-group case just has nothing to interleave with).
function combinedTimeSeries(groups) {
  const tagged = [];
  groups.forEach((g, gi) => {
    for (const row of g.rows) tagged.push({ row, gi });
  });
  tagged.sort((a, b) => new Date(a.row.checked_at) - new Date(b.row.checked_at));

  const labelsSet = new Set();
  const seriesByGroup = groups.map(() => new Map());
  for (const { row, gi } of tagged) {
    const label = formatTime(row.checked_at);
    labelsSet.add(label);
    const g = groups[gi];
    const key = g.keyFn(row);
    if (!seriesByGroup[gi].has(key)) seriesByGroup[gi].set(key, new Map());
    seriesByGroup[gi].get(key).set(label, g.valueFn(row));
  }

  const labels = Array.from(labelsSet);
  const datasets = groups.flatMap((g, gi) => {
    const aligned = new Map();
    for (const [key, points] of seriesByGroup[gi]) {
      aligned.set(key, labels.map((l) => (points.has(l) ? points.get(l) : null)));
    }
    return lineDatasets(aligned, g.yAxisID);
  });
  return { labels, datasets };
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

  const [priceRows, travelRows] = await Promise.all([
    fetchJson(`/api/jobs/${job.id}/prices?hours=${currentHours()}`),
    fetchJson(`/api/jobs/${job.id}/travel-times?hours=${currentHours()}`),
  ]);
  if (priceRows == null || travelRows == null || job.id !== state.selectedJobId) return;

  if (priceRows.length === 0 && travelRows.length === 0) {
    chart = destroyChart(chart);
    noDataMsg.hidden = false;
    canvas.hidden = true;
    return;
  }
  noDataMsg.hidden = true;
  canvas.hidden = false;

  const { labels, datasets } = combinedTimeSeries([
    {
      rows: priceRows,
      keyFn: (r) => `${PROVIDER_LABELS[r.provider] || r.provider} — ${r.service_name}`,
      valueFn: (r) => r.price,
      yAxisID: "y",
    },
    {
      rows: travelRows,
      keyFn: (r) => MODE_LABELS[r.mode] || r.mode,
      valueFn: (r) => r.duration_seconds / 60,
      yAxisID: "y1",
    },
  ]);

  chart = renderChart(chart, canvas, {
    type: "line",
    data: { labels, datasets },
    options: chartOptions("price-chart-reset"),
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
// Same combine-multiple-row-sets idea as combinedTimeSeries, but for the
// day-over-day view: one sample per calendar day (whichever is closest to
// targetMin), independently per group so price and travel-time each keep
// their own closest-sample-of-the-day logic before landing on the shared
// per-day x axis.
function combinedDailyAtTime(groups, targetMin) {
  const days = new Map(); // dayKey -> { date, seriesByGroup: [Map<key,{diff,value}>, ...] }
  groups.forEach((g, gi) => {
    for (const row of g.rows) {
      const d = new Date(row.checked_at);
      const minute = d.getHours() * 60 + d.getMinutes();
      let diff = Math.abs(minute - targetMin);
      diff = Math.min(diff, 1440 - diff); // wrap around midnight
      if (diff > TOD_TOLERANCE_MIN) continue;

      const dayKey = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      if (!days.has(dayKey)) days.set(dayKey, { date: d, seriesByGroup: groups.map(() => new Map()) });
      const bucket = days.get(dayKey).seriesByGroup[gi];
      const key = g.keyFn(row);
      const prev = bucket.get(key);
      if (!prev || diff < prev.diff) bucket.set(key, { diff, value: g.valueFn(row) });
    }
  });

  const dayKeys = Array.from(days.keys()).sort(
    (a, b) => days.get(a).date - days.get(b).date,
  );
  const labels = dayKeys.map((k) =>
    days.get(k).date.toLocaleDateString("fa-IR", { weekday: "long", day: "numeric", month: "short" }),
  );

  const datasets = groups.flatMap((g, gi) => {
    const seriesKeys = new Set();
    for (const k of dayKeys) for (const s of days.get(k).seriesByGroup[gi].keys()) seriesKeys.add(s);

    const aligned = new Map();
    for (const s of seriesKeys) {
      aligned.set(
        s,
        dayKeys.map((k) => {
          const hit = days.get(k).seriesByGroup[gi].get(s);
          return hit ? hit.value : null;
        }),
      );
    }
    return lineDatasets(aligned, g.yAxisID);
  });
  return { labels, datasets };
}

async function loadByDayCharts() {
  const job = selectedJob();
  const noJobMsg = document.getElementById("by-day-no-job-msg");
  document.getElementById("by-day-job-name").textContent = job ? job.name : "—";

  const canvas = document.getElementById("price-by-day-chart");
  const noData = document.getElementById("price-by-day-no-data");

  if (!job) {
    dayChart = destroyChart(dayChart);
    canvas.hidden = true;
    noData.hidden = true;
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
  if (priceRows == null || travelRows == null || job.id !== state.selectedJobId) return;

  const { labels, datasets } = combinedDailyAtTime(
    [
      {
        rows: priceRows,
        keyFn: (r) => `${PROVIDER_LABELS[r.provider] || r.provider} — ${r.service_name}`,
        valueFn: (r) => r.price,
        yAxisID: "y",
      },
      {
        rows: travelRows,
        keyFn: (r) => MODE_LABELS[r.mode] || r.mode,
        valueFn: (r) => r.duration_seconds / 60,
        yAxisID: "y1",
      },
    ],
    targetMin,
  );

  if (labels.length === 0) {
    dayChart = destroyChart(dayChart);
    noData.hidden = false;
    canvas.hidden = true;
  } else {
    noData.hidden = true;
    canvas.hidden = false;
    dayChart = renderChart(dayChart, canvas, {
      type: "line",
      data: { labels, datasets },
      options: chartOptions("price-by-day-reset"),
    });
  }
}

// ---- zoom reset ----
//
// Each panel is a single chart now (price and travel-time share one plot),
// so zoom just applies to that one chart -- no more syncing a pair against
// each other.

const RESETTABLE_CHARTS = [
  { chart: () => chart, buttonId: "price-chart-reset" },
  { chart: () => dayChart, buttonId: "price-by-day-reset" },
];

function wireZoomResets() {
  for (const { chart: getChart, buttonId } of RESETTABLE_CHARTS) {
    document.getElementById(buttonId).addEventListener("click", () => {
      const c = getChart();
      if (c) c.resetZoom();
      document.getElementById(buttonId).hidden = true;
    });
  }
}

function resetAllZoom() {
  for (const { chart: getChart, buttonId } of RESETTABLE_CHARTS) {
    const c = getChart();
    if (c && c.isZoomedOrPanned()) c.resetZoom();
    document.getElementById(buttonId).hidden = true;
  }
}

document.getElementById("range-select").addEventListener("change", () => {
  resetAllZoom();
  loadChart();
});

// <input type="time">'s own displayed digits follow the browser/OS locale,
// not this page's -- a Persian-language browser may already show them in
// Persian, but nothing here guarantees it. This readout always does.
const FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
const toFaDigits = (s) => String(s).replace(/[0-9]/g, (d) => FA_DIGITS[d]);

function updateTodTimeLabel() {
  document.getElementById("tod-time-fa").textContent = toFaDigits(document.getElementById("tod-time").value);
}

updateTodTimeLabel();
document.getElementById("tod-time").addEventListener("input", updateTodTimeLabel);

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
      const detail = entry.message ? `<span class="status-detail">${entry.message}</span>` : "";
      row.innerHTML = `<span>${providerLabel}</span><span class="${entry.ok ? "status-ok" : "status-bad"}">${
        entry.ok ? "موفق" : "خطا"
      } · ${time}${detail}</span>`;
      el.appendChild(row);
    }
  } catch {
    el.textContent = "سرور در دسترس نیست";
  }
}

// ---- auth ----

function setAppVisible(visible) {
  document.querySelectorAll("[data-app-section]").forEach((el) => {
    el.hidden = !visible;
  });
}

function showAuthGate() {
  setAppVisible(false);
  document.getElementById("auth-panel").hidden = false;
  document.getElementById("account-bar").hidden = true;
  document.body.classList.add("auth-mode");
}

function showApp(email) {
  document.getElementById("auth-panel").hidden = true;
  setAppVisible(true);
  document.getElementById("account-bar").hidden = false;
  document.getElementById("account-email").textContent = email;
  document.body.classList.remove("auth-mode");
}

async function checkAuth() {
  try {
    const resp = await fetch("/api/auth/me");
    if (resp.ok) return await resp.json();
  } catch {
    /* treated as logged out below */
  }
  return null;
}

let pollTimer = null;
async function startApp() {
  await loadJobs();
  if (!pollTimer) pollTimer = setInterval(loadJobs, 30000);
}

document.getElementById("show-signup").addEventListener("click", () => {
  document.getElementById("login-form").hidden = true;
  document.getElementById("signup-form").hidden = false;
  document.getElementById("show-signup-wrap").hidden = true;
  document.getElementById("show-login-wrap").hidden = false;
  document.getElementById("auth-status").textContent = "";
  document.getElementById("auth-heading").textContent = "ثبت‌نام";
});

document.getElementById("show-login").addEventListener("click", () => {
  document.getElementById("signup-form").hidden = true;
  document.getElementById("login-form").hidden = false;
  document.getElementById("show-login-wrap").hidden = true;
  document.getElementById("show-signup-wrap").hidden = false;
  document.getElementById("auth-status").textContent = "";
  document.getElementById("auth-heading").textContent = "ورود";
});

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const statusEl = document.getElementById("auth-status");
  const email = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-password").value;
  statusEl.textContent = "در حال ورود…";
  try {
    const resp = await apiFetch("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      statusEl.textContent = "";
      showApp(data.email);
      await startApp();
    } else {
      statusEl.textContent = data.detail || "خطا در ورود.";
    }
  } catch {
    statusEl.textContent = "سرور در دسترس نیست.";
  }
});

document.getElementById("signup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const statusEl = document.getElementById("auth-status");
  const email = document.getElementById("signup-email").value.trim();
  const password = document.getElementById("signup-password").value;
  statusEl.textContent = "در حال ثبت‌نام…";
  try {
    const resp = await apiFetch("/api/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      if (data.approved) {
        statusEl.textContent = "";
        showApp(data.email);
        await startApp();
      } else {
        statusEl.textContent = "ثبت‌نام انجام شد — حساب شما در انتظار تایید مدیره.";
      }
    } else {
      statusEl.textContent = data.detail || "خطا در ثبت‌نام.";
    }
  } catch {
    statusEl.textContent = "سرور در دسترس نیست.";
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch {
    /* best-effort -- reload either way */
  }
  location.reload();
});

(async function main() {
  initNewJobMap();
  wireZoomResets();
  const me = await checkAuth();
  if (me) {
    showApp(me.email);
    await startApp();
  } else {
    showAuthGate();
  }
})();
