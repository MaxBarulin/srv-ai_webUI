// Раздел «Чат»: список чатов, история, SSE-стриминг, «Размышления», стоп.
import { api } from "/static/js/api.js";
import { escapeHtml, renderMarkdown } from "/static/js/markdown.js";

let chats = [];
let activeChatId = null;
let abortController = null; // не null — идёт генерация
let pendingAttachments = []; // разобранные вложения для следующего сообщения
let specializations = [];    // кэш активных специализаций (для селектов)

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
  els.ctxRag = $("ctx-rag");
  els.ctxRagLabel = $("ctx-rag-label");
  els.spec = $("chat-spec");
  els.examples = $("chat-examples");
  els.attachBtn = $("chat-attach-btn");
  els.fileInput = $("chat-file");
  els.attachments = $("chat-attachments");
  els.promptBtn = $("chat-prompt-btn");
  els.promptModal = $("prompt-modal");
  els.promptSpec = $("prompt-spec");
  els.promptCustom = $("prompt-custom");
  els.toast = toast;

  els.attachBtn.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", onFileSelected);

  els.promptBtn.addEventListener("click", openPromptModal);
  $("prompt-save-btn").addEventListener("click", savePromptSettings);
  $("prompt-cancel-btn").addEventListener("click", () => { els.promptModal.hidden = true; });
  els.promptModal.addEventListener("click", (e) => {
    if (e.target === els.promptModal) els.promptModal.hidden = true;
  });

  loadSpecializations();
  loadExamples();

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

  // Кнопки «Копировать для Excel» у markdown-таблиц (§15)
  els.messages.addEventListener("click", (e) => {
    const btn = e.target.closest(".table-excel");
    if (!btn) return;
    const table = btn.parentElement.querySelector("table");
    if (!table) return;
    const tsv = [...table.rows].map((row) =>
      [...row.cells].map((c) => c.textContent.replace(/\t/g, " ")).join("\t")).join("\n");
    navigator.clipboard.writeText(tsv).then(() => {
      btn.textContent = "Скопировано";
      setTimeout(() => { btn.textContent = "Копировать для Excel"; }, 1500);
    });
  });

  // Кнопки обратной связи 👍/👎 (§15)
  els.messages.addEventListener("click", (e) => {
    const btn = e.target.closest(".fb-btn");
    if (!btn) return;
    const rating = Number(btn.dataset.rating);
    const msgId = Number(btn.closest(".msg").dataset.messageId);
    if (msgId) submitFeedback(msgId, rating, btn.closest(".msg"));
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

// Вызывается из app.js после /api/me
export function setRagAvailable(available) {
  els.ctxRagLabel.hidden = !available;
}

// --- Специализации и примеры (§15) ---

async function loadSpecializations() {
  try {
    specializations = await api("/api/specializations");
    els.spec.replaceChildren(...specializations.map((s) => {
      const opt = document.createElement("option");
      opt.value = String(s.id);
      opt.textContent = s.name;
      return opt;
    }));
    els.spec.hidden = specializations.length <= 1;
  } catch {
    els.spec.hidden = true;
  }
}

// --- Режим и системный промпт чата (§15) ---

function activeChat() {
  return chats.find((c) => c.id === activeChatId) || null;
}

function updatePromptButton() {
  const chat = activeChat();
  els.promptBtn.classList.toggle("has-custom", Boolean(chat && chat.custom_prompt));
}

function openPromptModal() {
  const chat = activeChat();
  if (!chat) return;
  const options = [{ id: "", name: "Без режима (общий)" },
                   ...specializations.map((s) => ({ id: String(s.id), name: s.name }))];
  els.promptSpec.replaceChildren(...options.map((o) => {
    const opt = document.createElement("option");
    opt.value = o.id;
    opt.textContent = o.name;
    return opt;
  }));
  els.promptSpec.value = chat.specialization_id === null ? "" : String(chat.specialization_id);
  els.promptCustom.value = chat.custom_prompt || "";
  els.promptModal.hidden = false;
  els.promptCustom.focus();
}

async function savePromptSettings() {
  const chat = activeChat();
  if (!chat) return;
  try {
    const updated = await api(`/api/chats/${chat.id}`, { method: "PUT", body: {
      specialization_id: els.promptSpec.value === "" ? null : Number(els.promptSpec.value),
      custom_prompt: els.promptCustom.value,
    }});
    Object.assign(chat, updated);
    els.promptModal.hidden = true;
    updatePromptButton();
    els.toast("Настройки чата сохранены — действуют со следующего сообщения");
  } catch (e) {
    els.toast(e.detail, true);
  }
}

async function loadExamples() {
  try {
    const examples = await api("/api/examples");
    els.examples.replaceChildren(...examples.map((ex) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "example-chip";
      btn.textContent = ex.text;
      btn.addEventListener("click", () => useExample(ex.text));
      return btn;
    }));
  } catch { /* примеры необязательны */ }
}

async function useExample(text) {
  await createChat();
  els.input.value = text;
  els.input.focus();
}

// --- Вложения (§16) ---

async function onFileSelected() {
  const file = els.fileInput.files[0];
  els.fileInput.value = ""; // разрешить повторный выбор того же файла
  if (!file) return;
  els.attachBtn.disabled = true;
  els.attachBtn.textContent = "Загрузка…";
  try {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch("/api/attachments", { method: "POST", body: form });
    if (r.status === 401) { location.href = "/login"; return; }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Ошибка ${r.status}`);
    pendingAttachments.push(data);
    (data.warnings || []).forEach((w) => els.toast(w));
    renderAttachments();
  } catch (e) {
    els.toast(e.message || "Не удалось обработать файл", true);
  } finally {
    els.attachBtn.disabled = false;
    els.attachBtn.textContent = "📎 Файл";
  }
}

function renderAttachments() {
  els.attachments.hidden = pendingAttachments.length === 0;
  els.attachments.replaceChildren(...pendingAttachments.map((att, idx) => {
    const chip = document.createElement("span");
    chip.className = "attach-chip";
    const isImage = att.images && att.images.length;
    chip.textContent = `${isImage ? "🖼" : "📄"} ${att.filename}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attach-remove";
    remove.textContent = "✕";
    remove.title = "Убрать вложение";
    remove.addEventListener("click", () => {
      pendingAttachments.splice(idx, 1);
      renderAttachments();
    });
    chip.appendChild(remove);
    return chip;
  }));
}

async function submitFeedback(messageId, rating, msgEl) {
  let comment = "";
  if (rating === -1) {
    const answer = prompt("Что не так с ответом? (необязательно)");
    if (answer === null) return; // отмена
    comment = answer;
  }
  try {
    await api(`/api/chats/${activeChatId}/messages/${messageId}/feedback`,
              { method: "POST", body: { rating, comment } });
    setFeedbackState(msgEl, rating);
    els.toast("Спасибо за оценку");
  } catch (e) {
    els.toast(e.detail || "Не удалось сохранить оценку", true);
  }
}

function setFeedbackState(msgEl, rating) {
  msgEl.querySelectorAll(".fb-btn").forEach((b) => {
    b.classList.toggle("active", Number(b.dataset.rating) === rating);
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
  const body = {};
  if (!els.spec.hidden && els.spec.value) body.specialization_id = Number(els.spec.value);
  const chat = await api("/api/chats", { method: "POST", body });
  activeChatId = chat.id;
  els.messages.replaceChildren(); // новый чат пуст — убрать сообщения предыдущего
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

function sourcesBlock(text) {
  const details = document.createElement("details");
  details.className = "reasoning sources";
  const summary = document.createElement("summary");
  summary.textContent = "Источники";
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

function feedbackBar(rating) {
  const bar = document.createElement("div");
  bar.className = "feedback-bar";
  for (const [value, label, title] of [[1, "👍", "Хороший ответ"], [-1, "👎", "Плохой ответ"]]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fb-btn";
    if (rating === value) btn.classList.add("active");
    btn.dataset.rating = String(value);
    btn.textContent = label;
    btn.title = title;
    bar.appendChild(btn);
  }
  return bar;
}

function messageNode(msg) {
  const div = document.createElement("div");
  div.className = `msg msg-${msg.role}`;
  if (msg.role === "assistant") {
    if (msg.id) div.dataset.messageId = String(msg.id);
    if (msg.reasoning) div.appendChild(reasoningBlock(msg.reasoning));
    for (const activity of msg.tool_activity || []) {
      div.appendChild(activity.status === "sources"
        ? sourcesBlock(activity.text || "") : toolChip(activity));
    }
    const body = document.createElement("div");
    body.className = "msg-body";
    body.innerHTML = renderMarkdown(msg.content);
    div.appendChild(body);
    if (msg.id) div.appendChild(feedbackBar(msg.feedback_rating));
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
  updatePromptButton();
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
  if ((!content && pendingAttachments.length === 0) || activeChatId === null
      || abortController !== null) return;

  const attachments = pendingAttachments;
  pendingAttachments = [];
  renderAttachments();

  els.input.value = "";
  const attachNames = attachments.map((a) =>
    `${a.images && a.images.length ? "🖼" : "📄"} ${a.filename}`).join("  ");
  const userText = [content, attachNames && `\n${attachNames}`].filter(Boolean).join("");
  els.messages.appendChild(messageNode({ role: "user", content: userText || attachNames }));
  scrollToBottom();

  // Живой контейнер ответа
  const live = document.createElement("div");
  live.className = "msg msg-assistant";
  const queueNote = document.createElement("div");
  queueNote.className = "msg-note queue-note";
  queueNote.hidden = true;
  const liveReasoning = reasoningBlock("", false);
  liveReasoning.hidden = true;
  const liveBody = document.createElement("div");
  liveBody.className = "msg-body";
  live.appendChild(queueNote);
  live.appendChild(liveReasoning);
  live.appendChild(liveBody);
  els.messages.appendChild(live);

  let reasoningText = "";
  let contentText = "";
  let messageId = null;
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
      body: JSON.stringify({
        content,
        use_tools: els.ctxTools.checked,
        use_rag: !els.ctxRagLabel.hidden && els.ctxRag.checked,
        attachments: attachments.map((a) => ({
          filename: a.filename, text: a.text || "", images: a.images || [],
        })),
      }),
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
        if (event === "queued") {
          queueNote.hidden = false;
          queueNote.textContent = `В очереди: ${data.position}…`;
        } else if (event === "queue_ready") {
          queueNote.hidden = true;
        } else if (event === "reasoning") reasoningText += data.text;
        else if (event === "content") contentText += data.text;
        else if (event === "tool") {
          live.insertBefore(toolChip({ label: data.label, status: data.error ? "error" : "ok" }), liveBody);
        } else if (event === "tool_confirm") {
          live.insertBefore(toolChip({ label: data.label, status: "confirm", token: data.token }), liveBody);
        } else if (event === "sources") {
          live.insertBefore(sourcesBlock(data.text), liveBody);
        } else if (event === "pii_masked") {
          const note = document.createElement("div");
          note.className = "msg-note pii-note";
          note.textContent = `🛡 Заменено элементов ПДн: ${data.count}`;
          live.insertBefore(note, liveBody);
        } else if (event === "rag_error" || event === "doc_warning") {
          const note = document.createElement("div");
          note.className = "msg-note msg-error";
          note.textContent = data.detail;
          live.insertBefore(note, liveBody);
        } else if (event === "error") throw new Error(data.detail);
        else if (event === "done") {
          if (data.title) renamed = data.title;
          if (data.message_id) messageId = data.message_id;
        }
      });
      render();
    }

    if (renamed) {
      const chat = chats.find((c) => c.id === chatId);
      if (chat) { chat.title = renamed; renderChatList(); }
    }
    // Панель оценки ответа (§15) — только если ответ сохранён
    if (messageId && (contentText || live.querySelector(".tool-chip"))) {
      live.dataset.messageId = String(messageId);
      live.appendChild(feedbackBar(null));
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
    // Если во время генерации переключались между чатами, живой контейнер был
    // удалён из DOM — ответ дописывался «в никуда». Перечитываем историю из БД.
    if (!live.isConnected && activeChatId === chatId) {
      await loadMessages().catch(() => {});
    }
    els.input.focus();
  }
}
