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

function hideToast() {
  clearTimeout(toastTimer);
  toastEl.classList.remove("visible");
}

function toast(message, isError = false) {
  clearTimeout(toastTimer);
  // Пересобираем текст, чтобы живая область (role="status") заметила изменение
  // и повторное одинаковое сообщение тоже было объявлено.
  toastEl.textContent = "";
  toastEl.textContent = message;
  toastEl.classList.toggle("error", isError);
  toastEl.classList.add("visible");
  // Обычное уведомление гаснет само; ошибку пользователь закрывает сам —
  // за 3,5 секунды её можно не успеть прочитать.
  if (!isError) toastTimer = setTimeout(hideToast, 3500);
}

toastEl.addEventListener("click", hideToast);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && toastEl.classList.contains("visible")) hideToast();
});

// Объявление состояния генерации для скринридера (см. #sr-status в разметке).
const srStatusEl = document.getElementById("sr-status");
function announce(message) {
  if (!srStatusEl) return;
  srStatusEl.textContent = "";
  srStatusEl.textContent = message;
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
    group: null,
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
    } else if (key === "group") {
      // Группу меняем прямо в строке — отдельная форма ради одного поля лишняя
      const select = document.createElement("select");
      select.className = "group-select";
      select.title = "Группа определяет, какие общие заметки и события видит сотрудник";
      select.replaceChildren(...groupOptions(u.group_id, "Без группы", u.group_name));
      select.addEventListener("change", () => setUserGroup(u, select));
      td.appendChild(select);
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
  // Группы грузим первыми: из них строится выпадающий список в строке сотрудника
  await loadGroups();
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

// --- Администрирование: вкладки ---
//
// Панели уже загружены и наполнены (loadUsers тянет всё сразу, как и раньше) —
// вкладки только показывают и прячут. Так переключение мгновенное, а поведение
// запросов к серверу осталось прежним.

const ADMIN_TAB_KEY = "admin-tab";

function showAdminTab(name) {
  const tabs = [...document.querySelectorAll(".admin-tabs .tab")];
  if (!tabs.some((t) => t.dataset.tab === name)) name = tabs[0]?.dataset.tab;
  if (!name) return;
  for (const tab of tabs) {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    // Подвижный фокус: в полосу вкладок Tab заходит один раз, дальше — стрелки
    tab.tabIndex = active ? 0 : -1;
    document.getElementById(`admin-panel-${tab.dataset.tab}`).hidden = !active;
  }
  try { localStorage.setItem(ADMIN_TAB_KEY, name); } catch { /* приватный режим */ }
}

function initAdminTabs() {
  const bar = document.querySelector(".admin-tabs");
  if (!bar) return;
  const tabs = [...bar.querySelectorAll(".tab")];

  bar.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (tab) showAdminTab(tab.dataset.tab);
  });

  bar.addEventListener("keydown", (e) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    let next = null;
    if (step) {
      const i = tabs.findIndex((t) => t.dataset.tab === currentAdminTab());
      next = tabs[(i + step + tabs.length) % tabs.length];
    } else if (e.key === "Home") next = tabs[0];
    else if (e.key === "End") next = tabs[tabs.length - 1];
    if (!next) return;
    e.preventDefault();
    showAdminTab(next.dataset.tab);
    next.focus();
  });

  let saved = null;
  try { saved = localStorage.getItem(ADMIN_TAB_KEY); } catch { /* приватный режим */ }
  showAdminTab(saved || tabs[0].dataset.tab);
}

function currentAdminTab() {
  const active = document.querySelector(".admin-tabs .tab.active");
  return active ? active.dataset.tab : null;
}

// --- Администрирование: группы (отделы, бюро) ---

let groups = [];

// Пустое значение = «без группы»: такой сотрудник видит все общие записи.
// Если текущей группы нет в справочнике (список не загрузился), добавляем её
// отдельным пунктом — иначе поле показало бы «Без группы» и админ решил бы,
// что доступ уже открыт всем.
function groupOptions(selectedId, emptyLabel = "Без группы", selectedName = "") {
  const known = groups.some((g) => String(g.id) === String(selectedId ?? ""));
  const missing = (!known && selectedId !== null && selectedId !== undefined && selectedId !== "")
    ? [{ id: selectedId, name: selectedName || `Группа #${selectedId}` }]
    : [];
  const options = [{ id: "", name: emptyLabel }, ...groups, ...missing];
  return options.map((g) => {
    const opt = document.createElement("option");
    opt.value = String(g.id);
    opt.textContent = g.name;
    opt.selected = String(g.id) === String(selectedId ?? "");
    return opt;
  });
}

async function loadGroups() {
  try {
    groups = await api("/api/admin/groups");
  } catch (e) {
    groups = [];
    toast(e.detail || "Не удалось загрузить группы", true);
    return;
  }
  document.getElementById("groups-tbody").replaceChildren(...groups.map(groupRow));
  const newGroup = document.getElementById("new-group");
  newGroup.replaceChildren(
    ...groupOptions(newGroup.value, "Без группы — видит все общие записи"));
}

function groupRow(g) {
  const tr = document.createElement("tr");

  const nameTd = document.createElement("td");
  const name = document.createElement("input");
  name.type = "text";
  name.value = g.name;
  name.maxLength = 80;
  nameTd.appendChild(name);
  tr.appendChild(nameTd);

  for (const n of [g.user_count, g.note_count, g.event_count]) {
    const td = document.createElement("td");
    td.textContent = n;
    tr.appendChild(td);
  }

  const actions = document.createElement("td");
  actions.className = "actions";
  const saveBtn = document.createElement("button");
  saveBtn.className = "btn btn-small";
  saveBtn.textContent = "Переименовать";
  saveBtn.addEventListener("click", () => renameGroup(g, name.value));
  actions.appendChild(saveBtn);

  const delBtn = document.createElement("button");
  delBtn.className = "btn btn-small btn-danger ml-8";
  delBtn.textContent = "Удалить";
  delBtn.addEventListener("click", () => deleteGroup(g));
  actions.appendChild(delBtn);
  tr.appendChild(actions);
  return tr;
}

async function createGroup() {
  const input = document.getElementById("new-group-name");
  const name = input.value.trim();
  if (!name) { toast("Введите название группы", true); return; }
  try {
    await api("/api/admin/groups", { method: "POST", body: { name } });
    input.value = "";
    toast(`Группа «${name}» создана`);
    loadUsers();
  } catch (e) {
    toast(e.detail || "Не удалось создать группу", true);
  }
}

async function renameGroup(group, name) {
  const trimmed = name.trim();
  if (!trimmed) { toast("Название не может быть пустым", true); return; }
  if (trimmed === group.name) return;
  try {
    await api(`/api/admin/groups/${group.id}`, { method: "PUT", body: { name: trimmed } });
    toast(`Группа переименована в «${trimmed}»`);
    loadUsers();
  } catch (e) {
    toast(e.detail || "Не удалось переименовать группу", true);
    loadGroups();
  }
}

async function deleteGroup(group) {
  // Удаление группы расширяет видимость, а не сужает: проговариваем это числами
  const consequences = [
    group.user_count ? `${group.user_count} сотр. останутся без группы и увидят все общие записи` : "",
    group.note_count ? `${group.note_count} заметок станут общими для всех` : "",
    group.event_count ? `${group.event_count} событий станут общими для всех` : "",
  ].filter(Boolean);
  const tail = consequences.length ? `\n\n${consequences.join(";\n")}.` : "";
  if (!confirm(`Удалить группу «${group.name}»?${tail}`)) return;
  try {
    await api(`/api/admin/groups/${group.id}`, { method: "DELETE" });
    toast(`Группа «${group.name}» удалена`);
    loadUsers();
  } catch (e) {
    toast(e.detail || "Не удалось удалить группу", true);
  }
}

async function setUserGroup(user, select) {
  const groupId = select.value ? Number(select.value) : null;
  const label = select.options[select.selectedIndex].textContent;
  try {
    await api(`/api/admin/users/${user.id}/group`, { method: "POST", body: { group_id: groupId } });
    toast(`«${user.login}» → ${groupId === null ? "без группы" : `группа «${label}»`}`);
    loadUsers();
  } catch (e) {
    toast(e.detail || "Не удалось изменить группу", true);
    select.value = String(user.group_id ?? "");  // откатываем поле к прежнему
  }
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

// --- Тема оформления (светлая / тёмная / как в системе) ---
// Источник истины — настройка пользователя на сервере (users.theme), как и
// размер шрифта: выбор следует за аккаунтом на любом устройстве. localStorage —
// лишь кэш последнего выбора для мгновенной отрисовки без «вспышки» и для
// экрана входа (там сессии ещё нет). Раннее применение — theme-init.js в <head>.

function cachedTheme() {
  try { return localStorage.getItem("theme") || "light"; } catch { return "light"; }
}

function cacheTheme(theme) {
  try { localStorage.setItem("theme", theme); } catch { /* приватный режим */ }
}

function applyTheme(theme) {
  const dark = theme === "dark" ||
    (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  if (dark) document.documentElement.setAttribute("data-theme", "dark");
  else document.documentElement.removeAttribute("data-theme");
  document.querySelectorAll("#theme-select button").forEach((b) => {
    b.classList.toggle("active", b.dataset.theme === theme);
  });
}

async function setTheme(theme) {
  cacheTheme(theme);        // мгновенно — и как кэш для входа
  applyTheme(theme);
  currentUser.theme = theme;
  try {
    await api("/api/me/settings", { method: "POST", body: { theme } });
  } catch (e) {
    toast(e.detail || "Не удалось сохранить тему", true);
  }
}

function initTheme() {
  // Серверное значение (из /api/me) — источник истины; синхронизируем кэш.
  const theme = currentUser.theme || "light";
  cacheTheme(theme);
  applyTheme(theme);
  // Если выбрано «как в системе» — реагируем на смену системной темы
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if ((currentUser.theme || "light") === "system") applyTheme("system");
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
        group_id: Number(document.getElementById("new-group").value) || null,
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
  initTheme();
  document.getElementById("theme-select").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-theme]");
    if (btn) setTheme(btn.dataset.theme);
  });
  document.getElementById("password-form").addEventListener("submit", changePassword);

  // Администрирование: примеры и выгрузка
  initAdminTabs();
  document.getElementById("create-user-form").addEventListener("submit", createUser);
  document.getElementById("group-add-btn").addEventListener("click", createGroup);
  document.getElementById("new-group-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); createGroup(); }
  });
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
  initChat(toast, announce);
  setRagAvailable(Boolean(currentUser.rag_enabled));
  initNotes(toast, currentUser);
  initCalendar(toast, currentUser);
  window.addEventListener("hashchange", () => showSection(currentSectionFromHash()));
  showSection(currentSectionFromHash());
}

init();
