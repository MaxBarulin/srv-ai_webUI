// Раздел «Чат»: список чатов, история, SSE-стриминг, «Размышления», стоп.
import { api } from "/static/js/api.js";
import { escapeHtml, renderMarkdown } from "/static/js/markdown.js";

let chats = [];
let activeChatId = null;
let abortController = null; // не null — идёт генерация

const els = {};

function $(id) { return document.getElementById(id); }

export function initChat(toast) {
  els.list = $("chat-list");
  els.newBtn = $("chat-new-btn");
  els.messages = $("chat-messages");
  els.input = $("chat-input");
  els.form = $("chat-form");
  els.sendBtn = $("chat-send-btn");
  els.stopBtn = $("chat-stop-btn");
  els.empty = $("chat-empty");
  els.context = $("chat-context");
  els.ctxTools = $("ctx-tools");
  els.toast = toast;

  els.newBtn.addEventListener("click", createChat);
  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage();
  });
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  els.stopBtn.addEventListener("click", () => abortController?.abort());

  // Кнопки «Копировать» в блоках кода (инлайн-обработчики запрещены CSP)
  els.messages.addEventListener("click", (e) => {
    const btn = e.target.closest(".code-copy");
    if (!btn) return;
    const code = btn.parentElement.querySelector("code")?.textContent ?? "";
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = "Скопировано";
      setTimeout(() => { btn.textContent = "Копировать"; }, 1500);
    });
  });

  // Кнопки «Подтвердить» деструктивных действий инструментов
  els.messages.addEventListener("click", async (e) => {
    const btn = e.target.closest(".tool-confirm-btn");
    if (!btn) return;
    btn.disabled = true;
    const chip = btn.closest(".tool-chip");
    try {
      const r = await api("/api/tools/confirm", { method: "POST", body: { token: btn.dataset.token } });
      chip.classList.remove("confirm");
      chip.textContent = `🔧 ${r.label}`;
    } catch (err) {
      chip.classList.add("error");
      chip.textContent = `🔧 ${err.detail || "Не удалось выполнить действие"}`;
    }
  });

  window.addEventListener("section-shown", (e) => {
    if (e.detail === "chat") refreshChats();
  });
}

// --- Список чатов ---

async function refreshChats() {
  chats = await api("/api/chats");
  if (activeChatId === null && chats.length) {
    activeChatId = chats[0].id;
    await loadMessages();
  }
  renderChatList();
  updateInputState();
}

function renderChatList() {
  els.list.replaceChildren(...chats.map((chat) => {
    const li = document.createElement("li");
    li.className = chat.id === activeChatId ? "chat-item active" : "chat-item";

    const titleBtn = document.createElement("button");
    titleBtn.type = "button";
    titleBtn.className = "chat-title";
    titleBtn.textContent = chat.title;
    titleBtn.title = chat.title;
    titleBtn.addEventListener("click", () => selectChat(chat.id));
    li.appendChild(titleBtn);

    const renameBtn = document.createElement("button");
    renameBtn.type = "button";
    renameBtn.className = "chat-action";
    renameBtn.textContent = "✎";
    renameBtn.title = "Переименовать";
    renameBtn.addEventListener("click", () => renameChat(chat));
    li.appendChild(renameBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "chat-action";
    deleteBtn.textContent = "✕";
    deleteBtn.title = "Удалить";
    deleteBtn.addEventListener("click", () => deleteChat(chat));
    li.appendChild(deleteBtn);

    return li;
  }));
}

async function selectChat(id) {
  if (id === activeChatId) return;
  activeChatId = id;
  renderChatList();
  await loadMessages();
  updateInputState();
}

async function createChat() {
  const chat = await api("/api/chats", { method: "POST", body: {} });
  activeChatId = chat.id;
  await refreshChats();
  els.input.focus();
}

async function renameChat(chat) {
  const title = prompt("Новое название чата:", chat.title);
  if (title === null || !title.trim()) return;
  try {
    await api(`/api/chats/${chat.id}`, { method: "PUT", body: { title: title.trim() } });
    await refreshChats();
  } catch (e) {
    els.toast(e.detail, true);
  }
}

async function deleteChat(chat) {
  if (!confirm(`Удалить чат «${chat.title}» вместе с историей?`)) return;
  try {
    await api(`/api/chats/${chat.id}`, { method: "DELETE" });
    if (activeChatId === chat.id) activeChatId = null;
    await refreshChats();
    if (activeChatId === null) els.messages.replaceChildren();
  } catch (e) {
    els.toast(e.detail, true);
  }
}

// --- Сообщения ---

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function reasoningBlock(text, open = false) {
  const details = document.createElement("details");
  details.className = "reasoning";
  if (open) details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "Размышления";
  details.appendChild(summary);
  const body = document.createElement("div");
  body.className = "reasoning-body";
  body.textContent = text;
  details.appendChild(body);
  return details;
}

function toolChip({ label, status, token }) {
  const chip = document.createElement("div");
  chip.className = "tool-chip";
  if (status === "error") chip.classList.add("error");
  if (status === "confirm" && token) {
    chip.classList.add("confirm");
    chip.textContent = `🔧 Модель запрашивает: ${label} `;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-small tool-confirm-btn";
    btn.dataset.token = token;
    btn.textContent = "Подтвердить";
    chip.appendChild(btn);
  } else if (status === "confirm") {
    // из истории: токен не сохраняется, подтверждение доступно только в живом стриме
    chip.textContent = `🔧 Запрошено подтверждение: ${label}`;
  } else {
    chip.textContent = `🔧 ${label}`;
  }
  return chip;
}

function messageNode(msg) {
  const div = document.createElement("div");
  div.className = `msg msg-${msg.role}`;
  if (msg.role === "assistant") {
    if (msg.reasoning) div.appendChild(reasoningBlock(msg.reasoning));
    for (const activity of msg.tool_activity || []) div.appendChild(toolChip(activity));
    const body = document.createElement("div");
    body.className = "msg-body";
    body.innerHTML = renderMarkdown(msg.content);
    div.appendChild(body);
  } else {
    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = msg.content;
    div.appendChild(body);
  }
  return div;
}

async function loadMessages() {
  if (activeChatId === null) return;
  const messages = await api(`/api/chats/${activeChatId}/messages`);
  els.messages.replaceChildren(...messages.map(messageNode));
  scrollToBottom();
}

function updateInputState() {
  const streaming = abortController !== null;
  const hasChat = activeChatId !== null;
  els.empty.hidden = hasChat;
  els.form.hidden = !hasChat;
  els.context.hidden = !hasChat;
  els.input.disabled = streaming;
  els.sendBtn.hidden = streaming;
  els.stopBtn.hidden = !streaming;
}

// --- Отправка и стриминг ---

function parseSseBuffer(buffer, onEvent) {
  // Возвращает необработанный остаток буфера
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop();
  for (const block of blocks) {
    let event = null, data = null;
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data = line.slice(5).trim();
    }
    if (event && data !== null) onEvent(event, JSON.parse(data));
  }
  return rest;
}

async function sendMessage() {
  const content = els.input.value.trim();
  if (!content || activeChatId === null || abortController !== null) return;

  els.input.value = "";
  els.messages.appendChild(messageNode({ role: "user", content }));
  scrollToBottom();

  // Живой контейнер ответа
  const live = document.createElement("div");
  live.className = "msg msg-assistant";
  const liveReasoning = reasoningBlock("", false);
  liveReasoning.hidden = true;
  const liveBody = document.createElement("div");
  liveBody.className = "msg-body";
  live.appendChild(liveReasoning);
  live.appendChild(liveBody);
  els.messages.appendChild(live);

  let reasoningText = "";
  let contentText = "";
  const chatId = activeChatId;

  abortController = new AbortController();
  updateInputState();

  const render = () => {
    if (reasoningText) {
      liveReasoning.hidden = false;
      liveReasoning.querySelector(".reasoning-body").textContent = reasoningText;
    }
    liveBody.innerHTML = renderMarkdown(contentText);
    scrollToBottom();
  };

  try {
    const r = await fetch(`/api/chats/${chatId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, use_tools: els.ctxTools.checked }),
      signal: abortController.signal,
    });
    if (r.status === 401) { location.href = "/login"; return; }
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || `Ошибка ${r.status}`);
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let renamed = null;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer = parseSseBuffer(buffer + decoder.decode(value, { stream: true }), (event, data) => {
        if (event === "reasoning") reasoningText += data.text;
        else if (event === "content") contentText += data.text;
        else if (event === "tool") {
          live.insertBefore(toolChip({ label: data.label, status: data.error ? "error" : "ok" }), liveBody);
        } else if (event === "tool_confirm") {
          live.insertBefore(toolChip({ label: data.label, status: "confirm", token: data.token }), liveBody);
        } else if (event === "error") throw new Error(data.detail);
        else if (event === "done" && data.title) renamed = data.title;
      });
      render();
    }

    if (renamed) {
      const chat = chats.find((c) => c.id === chatId);
      if (chat) { chat.title = renamed; renderChatList(); }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      const note = document.createElement("div");
      note.className = "msg-note";
      note.textContent = "Генерация остановлена пользователем";
      live.appendChild(note);
    } else {
      const note = document.createElement("div");
      note.className = "msg-note msg-error";
      note.textContent = e.message || "Ошибка при обращении к модели";
      live.appendChild(note);
    }
  } finally {
    abortController = null;
    updateInputState();
    if (!reasoningText && !contentText
        && !live.querySelector(".msg-note") && !live.querySelector(".tool-chip")) live.remove();
    els.input.focus();
  }
}
