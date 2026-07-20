// Каркас SPA: навигация по разделам, текущий пользователь, раздел «Администрирование».
import { api } from "/static/js/api.js";
import { initChat, setRagAvailable } from "/static/js/chat.js";
import { initNotes } from "/static/js/notes.js";
import { initCalendar } from "/static/js/calendar.js";

const SECTIONS = {
  chat: "Чат",
  notes: "Заметки",
  calendar: "Календарь",
  admin: "Администрирование",
};

let currentUser = null;

// Метки времени в БД — UTC; в интерфейсе показываем московское время
function fmtMsk(iso, withTime = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const opts = { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", year: "numeric" };
  if (withTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return d.toLocaleString("ru-RU", opts);
}

const toastEl = document.getElementById("toast");
let toastTimer = null;

function toast(message, isError = false) {
  toastEl.textContent = message;
  toastEl.classList.toggle("error", isError);
  toastEl.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("visible"), 3500);
}

// --- Навигация ---

function showSection(name) {
  if (!SECTIONS[name] || (name === "admin" && currentUser.role !== "admin")) {
    name = "chat";
  }
  for (const key of Object.keys(SECTIONS)) {
    document.getElementById(`section-${key}`).hidden = key !== name;
  }
  document.querySelectorAll("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.section === name);
  });
  document.getElementById("page-title").textContent = SECTIONS[name];
  document.querySelector(".content").classList.toggle("content-full", name !== "admin");
  // В чате шапка скрыта — колонка чата до самого верха окна
  document.querySelector(".main").classList.toggle("no-topbar", name === "chat");
  // Панели списков в левом сайдбаре: чаты — в Чате, заметки — в Заметках
  document.getElementById("sidebar-chat").hidden = name !== "chat";
  document.getElementById("sidebar-notes").hidden = name !== "notes";
  if (name === "admin") loadUsers();
  window.dispatchEvent(new CustomEvent("section-shown", { detail: name }));
}

function currentSectionFromHash() {
  return location.hash.replace(/^#/, "") || "chat";
}

// --- Администрирование ---

function userRow(u) {
  const tr = document.createElement("tr");

  const cells = {
    login: u.login,
    name: u.display_name,
    role: null,
    status: null,
    created: fmtMsk(u.created_at, false),
  };

  for (const [key, text] of Object.entries(cells)) {
    const td = document.createElement("td");
    if (key === "role") {
      const badge = document.createElement("span");
      badge.className = u.role === "admin" ? "badge admin" : "badge";
      badge.textContent = u.role === "admin" ? "администратор" : "пользователь";
      td.appendChild(badge);
    } else if (key === "status") {
      const badge = document.createElement("span");
      badge.className = u.is_active ? "badge" : "badge blocked";
      badge.textContent = u.is_active ? "активен" : "заблокирован";
      td.appendChild(badge);
    } else {
      td.textContent = text;
    }
    tr.appendChild(td);
  }

  const actions = document.createElement("td");
  actions.className = "actions";

  const resetBtn = document.createElement("button");
  resetBtn.className = "btn btn-small";
  resetBtn.textContent = "Сбросить пароль";
  resetBtn.addEventListener("click", () => resetPassword(u));
  actions.appendChild(resetBtn);

  if (u.id !== currentUser.id) {
    const blockBtn = document.createElement("button");
    blockBtn.className = "btn btn-small ml-8";
    blockBtn.textContent = u.is_active ? "Заблокировать" : "Разблокировать";
    blockBtn.addEventListener("click", () => setActive(u, !u.is_active));
    actions.appendChild(blockBtn);

    const delBtn = document.createElement("button");
    delBtn.className = "btn btn-small btn-danger ml-8";
    delBtn.textContent = "Удалить";
    delBtn.title = "Удалить пользователя и все его данные";
    delBtn.addEventListener("click", () => deleteUser(u));
    actions.appendChild(delBtn);
  }

  tr.appendChild(actions);
  return tr;
}

async function loadUsers() {
  try {
    const users = await api("/api/admin/users");
    const tbody = document.getElementById("users-tbody");
    tbody.replaceChildren(...users.map(userRow));
  } catch (e) {
    toast(e.detail || "Не удалось загрузить пользователей", true);
  }
  loadSpecs();
  loadExamplesAdmin();
  loadMetrics();
  loadAudit();
}

// --- Администрирование: метрики (§13) ---

const METRIC_LABELS = {
  requests_total: "Всего запросов",
  requests_success: "Успешных",
  requests_failed: "Неуспешных",
  avg_tokens_per_sec: "Токенов/с (средн.)",
};

async function loadMetrics() {
  try {
    const m = await api("/api/admin/metrics");
    const grid = document.getElementById("metrics-grid");
    const tiles = Object.entries(METRIC_LABELS).map(([key, label]) => {
      const tile = document.createElement("div");
      tile.className = "metric-tile";
      const value = document.createElement("div");
      value.className = "metric-value";
      value.textContent = m[key] ?? 0;
      const cap = document.createElement("div");
      cap.className = "metric-label";
      cap.textContent = label;
      tile.append(value, cap);
      return tile;
    });
    const pii = m.pii_masked_by_type || {};
    const piiTotal = Object.values(pii).reduce((a, b) => a + b, 0);
    if (piiTotal) {
      const tile = document.createElement("div");
      tile.className = "metric-tile";
      const value = document.createElement("div");
      value.className = "metric-value";
      value.textContent = piiTotal;
      const cap = document.createElement("div");
      cap.className = "metric-label";
      cap.textContent = "ПДн замаскировано (" +
        Object.entries(pii).map(([k, v]) => `${k}: ${v}`).join(", ") + ")";
      tile.append(value, cap);
      tiles.push(tile);
    }
    grid.replaceChildren(...tiles);
  } catch { /* метрики необязательны */ }
}

// --- Администрирование: журнал аудита (§13) ---

let auditOffset = 0;
const AUDIT_LIMIT = 50;

async function loadAudit() {
  const action = document.getElementById("audit-action").value;
  const params = new URLSearchParams({ limit: AUDIT_LIMIT, offset: auditOffset });
  if (action) params.set("action", action);
  try {
    const data = await api(`/api/admin/audit?${params}`);
    const tbody = document.getElementById("audit-tbody");
    tbody.replaceChildren(...data.items.map(auditRow));
    document.getElementById("audit-total").textContent = `Всего записей: ${data.total}`;
    document.getElementById("audit-prev").disabled = auditOffset === 0;
    document.getElementById("audit-next").disabled = auditOffset + AUDIT_LIMIT >= data.total;
  } catch (e) {
    toast(e.detail || "Не удалось загрузить журнал", true);
  }
}

function auditRow(item) {
  const tr = document.createElement("tr");
  const object = [item.object_type, item.object_id].filter(Boolean).join(" #");
  const cells = [
    fmtMsk(item.created_at),
    item.user_login || "—",
    item.action,
    object || "—",
    item.details || "",
    item.ip || "",
  ];
  for (const text of cells) {
    const td = document.createElement("td");
    td.textContent = text;
    tr.appendChild(td);
  }
  return tr;
}

// --- Администрирование: специализации ---

function specRow(spec) {
  const tr = document.createElement("tr");
  const mk = (value, type = "text") => {
    const input = document.createElement(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") input.type = type;
    input.value = value ?? "";
    return input;
  };
  const order = mk(spec.sort_order, "number");
  order.style.width = "60px";
  const name = mk(spec.name);
  const promptField = mk(spec.system_prompt, "textarea");
  promptField.rows = 2;
  const active = document.createElement("input");
  active.type = "checkbox";
  active.checked = Boolean(spec.is_active);

  for (const cell of [order, name, promptField, active]) {
    const td = document.createElement("td");
    td.appendChild(cell);
    tr.appendChild(td);
  }

  const actions = document.createElement("td");
  actions.className = "actions";
  const saveBtn = document.createElement("button");
  saveBtn.className = "btn btn-small";
  saveBtn.textContent = "Сохранить";
  saveBtn.addEventListener("click", () => saveSpec(spec.id, {
    name: name.value.trim(),
    system_prompt: promptField.value,
    is_active: active.checked,
    sort_order: Number(order.value) || 0,
  }));
  actions.appendChild(saveBtn);

  const delBtn = document.createElement("button");
  delBtn.className = "btn btn-small ml-8";
  delBtn.textContent = "Удалить";
  delBtn.addEventListener("click", () => deleteSpec(spec.id, spec.name));
  actions.appendChild(delBtn);
  tr.appendChild(actions);
  return tr;
}

async function loadSpecs() {
  try {
    const specs = await api("/api/admin/specializations");
    document.getElementById("specs-tbody").replaceChildren(...specs.map(specRow));
  } catch (e) {
    toast(e.detail || "Не удалось загрузить специализации", true);
  }
}

async function saveSpec(id, body) {
  if (!body.name) { toast("Название не может быть пустым", true); return; }
  try {
    if (id) await api(`/api/admin/specializations/${id}`, { method: "PUT", body });
    else await api("/api/admin/specializations", { method: "POST", body });
    toast("Специализация сохранена");
    loadSpecs();
  } catch (e) {
    toast(e.detail, true);
  }
}

async function deleteSpec(id, name) {
  if (!confirm(`Удалить специализацию «${name}»?`)) return;
  try {
    await api(`/api/admin/specializations/${id}`, { method: "DELETE" });
    toast("Специализация удалена");
    loadSpecs();
  } catch (e) {
    toast(e.detail, true);
  }
}

async function loadExamplesAdmin() {
  try {
    const examples = await api("/api/admin/examples");
    document.getElementById("examples-text").value = examples.map((e) => e.text).join("\n");
  } catch { /* необязательно */ }
}

async function saveExamples() {
  const items = document.getElementById("examples-text").value.split("\n");
  try {
    const r = await api("/api/admin/examples", { method: "PUT", body: { items } });
    toast(`Сохранено примеров: ${r.count}`);
  } catch (e) {
    toast(e.detail, true);
  }
}

async function exportFeedback() {
  try {
    const r = await fetch("/api/admin/feedback/export");
    if (!r.ok) throw new Error();
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "feedback.jsonl";
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    toast("Не удалось выгрузить обратную связь", true);
  }
}

// --- Профиль: масштаб шрифта и смена пароля ---

function applyFontScale(scale) {
  document.documentElement.dataset.fontScale = String(scale);
  document.querySelectorAll("#font-scale button").forEach((b) => {
    b.classList.toggle("active", Number(b.dataset.scale) === scale);
  });
}

async function setFontScale(scale) {
  try {
    await api("/api/me/settings", { method: "POST", body: { font_scale: scale } });
    currentUser.font_scale = scale;
    applyFontScale(scale);
  } catch (e) {
    toast(e.detail || "Не удалось сохранить настройку", true);
  }
}

async function changePassword(e) {
  e.preventDefault();
  const cur = document.getElementById("cur-password").value;
  const next = document.getElementById("new-password2").value;
  try {
    await api("/api/me/password", { method: "POST",
      body: { current_password: cur, new_password: next } });
    toast("Пароль изменён");
    e.target.reset();
  } catch (err) {
    toast(err.detail, true);
  }
}

async function setActive(user, isActive) {
  const action = isActive ? "разблокировать" : "заблокировать";
  if (!confirm(`Вы уверены, что хотите ${action} пользователя «${user.login}»?`)) return;
  try {
    await api(`/api/admin/users/${user.id}/active`, { method: "POST", body: { is_active: isActive } });
    toast(`Пользователь «${user.login}» ${isActive ? "разблокирован" : "заблокирован"}`);
    loadUsers();
  } catch (e) {
    toast(e.detail, true);
  }
}

async function deleteUser(user) {
  // Двойное подтверждение: попросим ввести логин вручную —
  // действие необратимо и уносит все чаты, заметки и события владельца.
  const typed = prompt(
    `Удалить пользователя «${user.login}» вместе с его чатами, заметками ` +
    `и событиями? Действие необратимо.\n\nВведите логин пользователя ` +
    "для подтверждения:");
  if (typed === null) return;
  if (typed.trim() !== user.login) {
    toast("Логин не совпадает — удаление отменено", true);
    return;
  }
  try {
    await api(`/api/admin/users/${user.id}`, { method: "DELETE" });
    toast(`Пользователь «${user.login}» удалён`);
    loadUsers();
  } catch (e) {
    toast(e.detail || "Не удалось удалить пользователя", true);
  }
}

async function resetPassword(user) {
  const newPassword = prompt(`Новый пароль для «${user.login}» (мин. 10 символов):`);
  if (newPassword === null) return;
  try {
    await api(`/api/admin/users/${user.id}/password`, { method: "POST", body: { new_password: newPassword } });
    toast(`Пароль пользователя «${user.login}» сброшен, его сессии завершены`);
  } catch (e) {
    toast(e.detail, true);
  }
}

async function createUser(e) {
  e.preventDefault();
  try {
    const login = document.getElementById("new-login").value.trim();
    await api("/api/admin/users", {
      method: "POST",
      body: {
        login,
        display_name: document.getElementById("new-name").value.trim(),
        password: document.getElementById("new-password").value,
        role: document.getElementById("new-role").value,
      },
    });
    toast(`Пользователь «${login}» создан`);
    e.target.reset();
    loadUsers();
  } catch (err) {
    toast(err.detail, true);
  }
}

// --- Инициализация ---

async function init() {
  currentUser = await api("/api/me"); // 401 → редирект на /login внутри api()
  document.getElementById("user-name").textContent = currentUser.display_name;
  if (currentUser.role === "admin") {
    document.getElementById("nav-admin").hidden = false;
  }

  document.getElementById("logout-btn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
  });

  // Профиль
  applyFontScale(currentUser.font_scale ?? 1);
  const profileModal = document.getElementById("profile-modal");
  document.getElementById("profile-btn").addEventListener("click", () => {
    profileModal.hidden = false;
  });
  document.getElementById("profile-close-btn").addEventListener("click", () => {
    profileModal.hidden = true;
  });
  profileModal.addEventListener("click", (e) => {
    if (e.target === profileModal) profileModal.hidden = true;
  });
  document.getElementById("font-scale").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-scale]");
    if (btn) setFontScale(Number(btn.dataset.scale));
  });
  document.getElementById("password-form").addEventListener("submit", changePassword);

  // Администрирование: примеры и выгрузка
  document.getElementById("create-user-form").addEventListener("submit", createUser);
  document.getElementById("spec-add-btn").addEventListener("click", () =>
    saveSpec(null, { name: "Новая специализация", system_prompt: "", is_active: true, sort_order: 0 }));
  document.getElementById("examples-save-btn").addEventListener("click", saveExamples);
  document.getElementById("feedback-export-btn").addEventListener("click", exportFeedback);
  document.getElementById("audit-refresh").addEventListener("click", () => { auditOffset = 0; loadAudit(); });
  document.getElementById("audit-action").addEventListener("change", () => { auditOffset = 0; loadAudit(); });
  document.getElementById("audit-prev").addEventListener("click", () => {
    auditOffset = Math.max(0, auditOffset - AUDIT_LIMIT); loadAudit();
  });
  document.getElementById("audit-next").addEventListener("click", () => {
    auditOffset += AUDIT_LIMIT; loadAudit();
  });
  initChat(toast);
  setRagAvailable(Boolean(currentUser.rag_enabled));
  initNotes(toast);
  initCalendar(toast);
  window.addEventListener("hashchange", () => showSection(currentSectionFromHash()));
  showSection(currentSectionFromHash());
}

init();
