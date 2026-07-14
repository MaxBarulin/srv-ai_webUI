// Каркас SPA: навигация по разделам, текущий пользователь, раздел «Администрирование».
import { api } from "/static/js/api.js";
import { initChat } from "/static/js/chat.js";
import { initNotes } from "/static/js/notes.js";
import { initCalendar } from "/static/js/calendar.js";

const SECTIONS = {
  chat: "Чат",
  notes: "Заметки",
  calendar: "Календарь",
  admin: "Администрирование",
};

let currentUser = null;

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
    created: (u.created_at || "").slice(0, 10),
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

  document.getElementById("create-user-form").addEventListener("submit", createUser);
  initChat(toast);
  initNotes(toast);
  initCalendar(toast);
  window.addEventListener("hashchange", () => showSection(currentSectionFromHash()));
  showSection(currentSectionFromHash());
}

init();
