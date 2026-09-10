// Standalone page, deliberately not linked from the main app (index.html) --
// reachable only by whoever already knows the URL and the admin password.
// Password never touches localStorage/sessionStorage, just this variable,
// for the life of the page load (mirrors the pattern the old in-app Snapp
// admin panel used before it was pulled out of the public UI entirely).
let adminPassword = "";

function apiAdminFetch(url, options = {}) {
  return fetch(url, {
    ...options,
    headers: { ...options.headers, "X-Admin-Password": adminPassword, "Content-Type": "application/json" },
  });
}

function relativeTime(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "همین الان";
  if (minutes < 60) return `${minutes.toLocaleString("fa-IR")} دقیقه پیش`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours.toLocaleString("fa-IR")} ساعت پیش`;
  return `${Math.round(hours / 24).toLocaleString("fa-IR")} روز پیش`;
}

function showGate(message) {
  document.getElementById("admin-gate").hidden = false;
  document.getElementById("admin-users-panel").hidden = true;
  document.getElementById("admin-gate-status").textContent = message || "";
}

function showPanel() {
  document.getElementById("admin-gate").hidden = true;
  document.getElementById("admin-users-panel").hidden = false;
}

function renderPending(users) {
  const listEl = document.getElementById("admin-users-list");
  const countEl = document.getElementById("admin-pending-count");

  if (!users.length) {
    countEl.hidden = true;
    listEl.innerHTML = '<p class="inbox-empty">صندوق خالیه — چیزی برای بررسی نیست.</p>';
    return;
  }

  countEl.hidden = false;
  countEl.textContent = users.length.toLocaleString("fa-IR");

  listEl.innerHTML = "";
  for (const u of users) {
    const item = document.createElement("div");
    item.className = "inbox-item";

    const info = document.createElement("div");
    info.className = "inbox-item-info";
    info.innerHTML = `<div class="inbox-item-email">${u.email}</div><div class="inbox-item-time">${relativeTime(u.created_at)}</div>`;

    const actions = document.createElement("div");
    actions.className = "job-actions";

    const approveBtn = document.createElement("button");
    approveBtn.className = "primary-btn";
    approveBtn.textContent = "تایید";
    approveBtn.addEventListener("click", () => decide(u.id, "approve"));

    const rejectBtn = document.createElement("button");
    rejectBtn.className = "job-btn job-btn-danger";
    rejectBtn.textContent = "رد";
    rejectBtn.addEventListener("click", () => decide(u.id, "reject"));

    actions.append(approveBtn, rejectBtn);
    item.append(info, actions);
    listEl.appendChild(item);
  }
}

async function loadPending() {
  const listEl = document.getElementById("admin-users-list");
  listEl.textContent = "در حال بارگذاری…";
  try {
    const resp = await apiAdminFetch("/api/admin/users/pending");
    if (resp.status === 401) {
      showGate("رمز اشتباهه یا منقضی شده.");
      return;
    }
    if (!resp.ok) throw new Error("bad response");
    renderPending(await resp.json());
  } catch {
    listEl.textContent = "سرور در دسترس نیست.";
  }
}

async function decide(userId, action) {
  try {
    const resp = await apiAdminFetch(`/api/admin/users/${userId}/${action}`, { method: "POST" });
    if (resp.ok) await loadPending();
  } catch {
    /* transient network failure -- admin can retry */
  }
}

document.getElementById("admin-password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  adminPassword = document.getElementById("admin-password-input").value;
  const statusEl = document.getElementById("admin-gate-status");
  statusEl.textContent = "در حال بررسی…";
  try {
    const resp = await apiAdminFetch("/api/admin/users/pending");
    if (resp.ok) {
      statusEl.textContent = "";
      showPanel();
      renderPending(await resp.json());
    } else if (resp.status === 401) {
      statusEl.textContent = "رمز اشتباهه.";
    } else {
      statusEl.textContent = "خطا در ورود.";
    }
  } catch {
    statusEl.textContent = "سرور در دسترس نیست.";
  }
});

document.getElementById("admin-refresh").addEventListener("click", loadPending);
