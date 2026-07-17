// Раздел «Чат»: список чатов, история, SSE-стриминг, «Размышления», стоп.
import { api } from "/static/js/api.js";
import { escapeHtml, renderMarkdown } from "/static/js/markdown.js";

let chats = [];
let activeChatId = null;
// Состояние стриминга — по одному на чат. Инференс в чате продолжается, даже
// если пользователь ушёл в другой чат; кнопки/панель относятся к активному чату.
// chatId -> { ac, live, liveBody, liveReasoning, queueNote, reasoningText,
//             contentText, messageId, statsData }
const streams = new Map();
let pendingAttachments = []; // разобранные вложения для следующего сообщения
let specializations = [];    // кэш активных специализаций (для селектов)

// Sticky-скролл: следуем за новой строкой генерации, только если пользователь
// сам не поднял бегунок вверх — тогда фиксируем позицию и не «дёргаем» его.
const STICKY_THRESHOLD = 40;
let messagesFollow = true;
function isAtBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= STICKY_THRESHOLD;
}
function stickToBottom(el) {
  el.scrollTop = el.scrollHeight;
}

function isStreaming(chatId) { return chatId !== null && streams.has(chatId); }

// Копирование в буфер. navigator.clipboard доступен только в secure context
// (HTTPS или localhost) — при доступе по http://<ip> его нет, поэтому фолбэк
// через скрытую textarea + execCommand("copy").
async function copyText(text) {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* пробуем фолбэк */ }
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { /* не судьба */ }
  ta.remove();
  return ok;
}

// Общая обратная связь на кнопке: «Скопировано» или «Ошибка», затем возврат
function copyWithFeedback(btn, text, label = "Копировать") {
  copyText(text).then((ok) => {
    btn.textContent = ok ? "Скопировано" : "Не удалось";
    setTimeout(() => { btn.textContent = label; }, 1500);
  });
}

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
  els.ctxThink = $("ctx-think");
  els.spec = $("chat-spec");
  els.examples = $("chat-examples");
  els.attachBtn = $("chat-attach-btn");
  els.fileInput = $("chat-file");
  els.attachments = $("chat-attachments");
  els.promptBtn = $("chat-prompt-btn");
  els.promptModal = $("prompt-modal");
  els.promptSpec = $("prompt-spec");
  els.promptCustom = $("prompt-custom");
  els.status = $("chat-status");
  els.stats = $("chat-stats");
  els.continueBtn = $("chat-continue-btn");
  els.delLastBtn = $("chat-del-last-btn");
  els.toast = toast;

  els.continueBtn.addEventListener("click", continueGeneration);
  els.delLastBtn.addEventListener("click", deleteLastMessage);

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
  els.stopBtn.addEventListener("click", () => streams.get(activeChatId)?.ac.abort());

  // Тумблеры «Заметки/Календарь» и «Размышления» — свои у каждого чата
  els.ctxTools.addEventListener("change",
    () => saveChatToggle("use_tools", els.ctxTools.checked));
  els.ctxThink.addEventListener("change",
    () => saveChatToggle("enable_thinking", els.ctxThink.checked));

  // Отслеживаем, у нижней ли границы бегунок. Если пользователь поднял его —
  // отключаем автоскролл, пока сам не вернётся вниз.
  els.messages.addEventListener("scroll", () => {
    messagesFollow = isAtBottom(els.messages);
  });

  // Кнопки «Копировать» в блоках кода (инлайн-обработчики запрещены CSP)
  els.messages.addEventListener("click", (e) => {
    const btn = e.target.closest(".code-copy");
    if (!btn) return;
    const code = btn.parentElement.querySelector("code")?.textContent ?? "";
    copyWithFeedback(btn, code);
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

// Пер-чатовые тумблеры: состояние хранится в чате (chats.use_tools /
// chats.enable_thinking) и применяется при каждом переключении чата.
function applyChatToggles() {
  const chat = activeChat();
  if (!chat) return;
  els.ctxTools.checked = Boolean(chat.use_tools);
  els.ctxThink.checked = Boolean(chat.enable_thinking);
}

async function saveChatToggle(field, value) {
  const chat = activeChat();
  if (!chat) return;
  chat[field] = value ? 1 : 0; // оптимистично, чтобы UI не мигал
  try {
    const updated = await api(`/api/chats/${chat.id}`, { method: "PUT",
      body: { [field]: value } });
    Object.assign(chat, updated);
  } catch (e) {
    els.toast(e.detail || "Не удалось сохранить настройку чата", true);
  }
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
  applyChatToggles();
  updateInputState();
}

function renderChatList() {
  els.list.replaceChildren(...chats.map((chat) => {
    const li = document.createElement("li");
    li.className = chat.id === activeChatId ? "chat-item active" : "chat-item";

    const titleBtn = document.createElement("button");
    titleBtn.type = "button";
    titleBtn.className = "chat-title";
    if (isStreaming(chat.id)) {
      const spin = document.createElement("span");
      spin.className = "chat-spinner";
      spin.title = "Идёт генерация";
      titleBtn.appendChild(spin);
    }
    titleBtn.appendChild(document.createTextNode(chat.title));
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
  // Если в этом чате идёт генерация — вернуть на экран её живой ответ и статистику
  const st = streams.get(id);
  if (st) {
    els.messages.appendChild(st.live);
    renderLive(st);
  }
  showStats(st ? st.statsData : null);
  applyChatToggles();
  updateInputState();
  scrollToBottom(true);  // при переключении чата — принудительно вниз
}

async function createChat() {
  const body = {};
  if (!els.spec.hidden && els.spec.value) body.specialization_id = Number(els.spec.value);
  const chat = await api("/api/chats", { method: "POST", body });
  activeChatId = chat.id;
  els.messages.replaceChildren(); // новый чат пуст — убрать сообщения предыдущего
  messagesFollow = true;          // сброс sticky для нового пустого чата
  showStats(null);
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
    streams.get(chat.id)?.ac.abort(); // прервать генерацию удаляемого чата
    streams.delete(chat.id);
    await api(`/api/chats/${chat.id}`, { method: "DELETE" });
    if (activeChatId === chat.id) { activeChatId = null; showStats(null); }
    await refreshChats();
    if (activeChatId === null) els.messages.replaceChildren();
  } catch (e) {
    els.toast(e.detail, true);
  }
}

// --- Сообщения ---

// force=true (загрузка чата, отправка нового сообщения) — принудительно вниз
// и сбрасываем sticky-состояние; force=false (тик стрима) — только если следим.
function scrollToBottom(force = false) {
  if (force) messagesFollow = true;
  if (messagesFollow) stickToBottom(els.messages);
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

// Панель под ответом: 👍/👎, «Копировать» (исходный markdown) и
// переключатель «рендер ↔ Markdown» для тела ответа.
function answerActions(rating, getRaw, bodyEl) {
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

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "msg-action";
  copyBtn.textContent = "Копировать";
  copyBtn.title = "Скопировать весь ответ (markdown)";
  copyBtn.addEventListener("click", () => copyWithFeedback(copyBtn, getRaw()));
  bar.appendChild(copyBtn);

  const rawBtn = document.createElement("button");
  rawBtn.type = "button";
  rawBtn.className = "msg-action";
  rawBtn.textContent = "Markdown";
  rawBtn.title = "Показать ответ как исходный markdown";
  rawBtn.addEventListener("click", () => {
    const showRaw = bodyEl.classList.toggle("raw-view");
    rawBtn.classList.toggle("active", showRaw);
    rawBtn.textContent = showRaw ? "Рендер" : "Markdown";
    if (showRaw) bodyEl.textContent = getRaw();
    else bodyEl.innerHTML = renderMarkdown(getRaw());
  });
  bar.appendChild(rawBtn);

  return bar;
}

// Сворачиваемый блок вложения в сообщении пользователя (§16):
// спарсенный документ не «висит стеной», а открывается по клику.
function attachmentBlock(att) {
  if (att.image) {
    const chip = document.createElement("div");
    chip.className = "attach-msg-chip";
    chip.textContent = `🖼 ${att.filename}`;
    return chip;
  }
  const details = document.createElement("details");
  details.className = "attach-doc";
  const summary = document.createElement("summary");
  summary.textContent = `📄 ${att.filename}`;
  details.appendChild(summary);
  const body = document.createElement("div");
  body.className = "attach-doc-body";
  body.textContent = att.text || "";
  details.appendChild(body);
  return details;
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
    if (msg.id) div.appendChild(answerActions(msg.feedback_rating, () => msg.content, body));
  } else {
    if (msg.content) {
      const body = document.createElement("div");
      body.className = "msg-body";
      body.textContent = msg.content;
      div.appendChild(body);
    }
    for (const att of msg.attachments || []) div.appendChild(attachmentBlock(att));
  }
  return div;
}

async function loadMessages() {
  if (activeChatId === null) return;
  const messages = await api(`/api/chats/${activeChatId}/messages`);
  els.messages.replaceChildren(...messages.map(messageNode));
  scrollToBottom(true);  // после перерисовки истории — принудительно вниз
}

function updateInputState() {
  // Состояние строки ввода относится к активному чату (у каждого чата — своё)
  const streaming = isStreaming(activeChatId);
  const hasChat = activeChatId !== null;
  els.empty.hidden = hasChat;
  els.form.hidden = !hasChat;
  els.context.hidden = !hasChat;
  els.status.hidden = !hasChat;
  els.continueBtn.disabled = streaming;
  els.delLastBtn.disabled = streaming;
  updatePromptButton();
  els.input.disabled = streaming;
  els.sendBtn.hidden = streaming;
  els.stopBtn.hidden = !streaming;
}

// Перерисовать живой ответ стрима в его DOM-узел (узел может быть откреплён,
// если пользователь в другом чате — тогда просто обновляем в памяти).
function renderLive(st, scroll = false) {
  // Пока идёт стрим — контейнер помечен .streaming (мигающий курсор у
  // последней строки, спиннер в блоке «Размышления»).
  st.live.classList.add("streaming");
  if (st.reasoningText) {
    st.liveReasoning.hidden = false;
    const rb = st.liveReasoning.querySelector(".reasoning-body");
    rb.textContent = st.reasoningText;
    // Фаза размышлений (контент ещё не пошёл): шиммер по заголовку +
    // «живая выписка» — последняя строчка размышлений прямо в summary.
    const summary = st.liveReasoning.querySelector("summary");
    if (!st.contentText) {
      st.liveReasoning.classList.add("thinking");
      const lines = st.reasoningText.trimEnd().split("\n");
      const tail = (lines[lines.length - 1] || "").trim().slice(-90);
      summary.textContent = tail ? `Размышления · ${tail}` : "Размышления";
    } else {
      st.liveReasoning.classList.remove("thinking");
      summary.textContent = "Размышления";
    }
    // Sticky-скролл для окна «Размышлений» тоже per-stream: пока пользователь
    // не поднял бегунок внутри блока — тянемся за новой строкой.
    if (st.reasoningFollow) stickToBottom(rb);
  }
  st.liveBody.innerHTML = renderMarkdown(st.contentText);
  if (scroll && st.chatId === activeChatId) scrollToBottom();
}

// Снять индикаторы стрима с живого контейнера (конец генерации/ошибка)
function finishLive(st) {
  st.live.classList.remove("streaming");
  st.liveReasoning.classList.remove("thinking");
  const summary = st.liveReasoning.querySelector("summary");
  if (summary) summary.textContent = "Размышления";
}

// Панель статистики над строкой ввода (счётчики сервера, как в llama.cpp)
function showStats(stats) {
  if (!stats || !stats.completion_tokens) { els.stats.textContent = ""; return; }
  const parts = [`${stats.completion_tokens} ток.`];
  if (stats.tokens_per_second) parts.push(`${stats.tokens_per_second} ток/с`);
  if (stats.context_percent !== null && stats.context_percent !== undefined) {
    parts.push(`контекст: ${stats.context_percent}% из ${stats.context_size}`);
  } else if (stats.context_used) {
    parts.push(`контекст: ${stats.context_used} ток.`);
  }
  els.stats.textContent = parts.join(" · ");
}

// --- «Продолжить» и «Удалить последнее» (как в web UI llama.cpp) ---

async function continueGeneration() {
  if (activeChatId === null || isStreaming(activeChatId)) return;
  const chatId = activeChatId;

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

  const st = {
    chatId, ac: new AbortController(), live, liveBody, liveReasoning, queueNote,
    reasoningText: "", contentText: "", messageId: null, statsData: null,
    reasoningFollow: true,
  };
  liveReasoning.querySelector(".reasoning-body").addEventListener("scroll", (e) => {
    st.reasoningFollow = isAtBottom(e.currentTarget);
  });
  streams.set(chatId, st);
  updateInputState();
  renderChatList();

  try {
    const r = await fetch(`/api/chats/${chatId}/continue`, {
      method: "POST",
      signal: st.ac.signal,
    });
    if (r.status === 401) { location.href = "/login"; return; }
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || `Ошибка ${r.status}`);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer = parseSseBuffer(buffer + decoder.decode(value, { stream: true }), (event, data) => {
        if (event === "queued") {
          queueNote.hidden = false;
          queueNote.textContent = `В очереди: ${data.position}…`;
        } else if (event === "queue_ready") {
          queueNote.hidden = true;
        } else if (event === "reasoning") st.reasoningText += data.text;
        else if (event === "content") st.contentText += data.text;
        else if (event === "stats") {
          st.statsData = data;
          if (chatId === activeChatId) showStats(data);
        } else if (event === "error") throw new Error(data.detail);
      });
      renderLive(st, true);
    }
  } catch (e) {
    if (e.name !== "AbortError") els.toast(e.message || "Не удалось продолжить", true);
  } finally {
    streams.delete(chatId);
    finishLive(st);
    live.remove();
    renderChatList();
    // Продолжение дописано к сообщению в БД — перечитываем историю целиком
    if (chatId === activeChatId) {
      updateInputState();
      await loadMessages().catch(() => {});
      els.input.focus();
    }
  }
}

async function deleteLastMessage() {
  if (activeChatId === null || isStreaming(activeChatId)) return;
  const messages = await api(`/api/chats/${activeChatId}/messages`);
  if (!messages.length) { els.toast("В чате нет сообщений"); return; }
  const last = messages[messages.length - 1];
  const kind = last.role === "assistant" ? "ответ модели" : "сообщение";
  if (!confirm(`Удалить последнее ${kind} из чата и истории?`)) return;
  try {
    await api(`/api/chats/${activeChatId}/messages/${last.id}`, { method: "DELETE" });
    await loadMessages();
    els.toast("Сообщение удалено");
  } catch (e) {
    els.toast(e.detail, true);
  }
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
  // Отправлять можно, только если в ЭТОМ чате сейчас нет генерации.
  // В другом чате генерация может идти параллельно — бэкенд поставит в очередь.
  if ((!content && pendingAttachments.length === 0) || activeChatId === null
      || isStreaming(activeChatId)) return;

  const chatId = activeChatId;
  const attachments = pendingAttachments;
  pendingAttachments = [];
  renderAttachments();

  els.input.value = "";
  els.messages.appendChild(messageNode({
    role: "user",
    content,
    attachments: attachments.map((a) => (a.images && a.images.length
      ? { filename: a.filename, image: true }
      : { filename: a.filename, text: a.text || "" })),
  }));
  // Пользователь отправил сообщение — прыгаем вниз и снова следим за стримом.
  scrollToBottom(true);

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

  const st = {
    chatId, ac: new AbortController(), live, liveBody, liveReasoning, queueNote,
    reasoningText: "", contentText: "", messageId: null, statsData: null,
    reasoningFollow: true,
  };
  liveReasoning.querySelector(".reasoning-body").addEventListener("scroll", (e) => {
    st.reasoningFollow = isAtBottom(e.currentTarget);
  });
  streams.set(chatId, st);
  updateInputState();
  renderChatList(); // показать индикатор генерации в списке

  let renamed = null;
  try {
    const r = await fetch(`/api/chats/${chatId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content,
        use_tools: els.ctxTools.checked,
        use_rag: !els.ctxRagLabel.hidden && els.ctxRag.checked,
        enable_thinking: els.ctxThink.checked,
        attachments: attachments.map((a) => ({
          filename: a.filename, text: a.text || "", images: a.images || [],
        })),
      }),
      signal: st.ac.signal,
    });
    if (r.status === 401) { location.href = "/login"; return; }
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || `Ошибка ${r.status}`);
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer = parseSseBuffer(buffer + decoder.decode(value, { stream: true }), (event, data) => {
        if (event === "queued") {
          queueNote.hidden = false;
          queueNote.textContent = `В очереди: ${data.position}…`;
        } else if (event === "queue_ready") {
          queueNote.hidden = true;
        } else if (event === "reasoning") st.reasoningText += data.text;
        else if (event === "content") st.contentText += data.text;
        else if (event === "tool") {
          live.insertBefore(toolChip({ label: data.label, status: data.error ? "error" : "ok" }), liveBody);
        } else if (event === "tool_confirm") {
          live.insertBefore(toolChip({ label: data.label, status: "confirm", token: data.token }), liveBody);
        } else if (event === "stats") {
          st.statsData = data;
          if (chatId === activeChatId) showStats(data);
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
          if (data.message_id) st.messageId = data.message_id;
        }
      });
      renderLive(st, true);
    }

    if (renamed) {
      const chat = chats.find((c) => c.id === chatId);
      if (chat) chat.title = renamed;
    }
    // Панель действий под ответом (§15) — только если ответ сохранён
    if (st.messageId && (st.contentText || live.querySelector(".tool-chip"))) {
      live.dataset.messageId = String(st.messageId);
      live.appendChild(answerActions(null, () => st.contentText, liveBody));
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
    streams.delete(chatId);
    finishLive(st);
    const empty = !st.reasoningText && !st.contentText
      && !live.querySelector(".msg-note") && !live.querySelector(".tool-chip");
    if (empty) live.remove();
    renderChatList(); // убрать индикатор генерации
    // Если этот чат сейчас открыт — снять блокировку ввода и перечитать историю
    // из БД (чистая версия + панель оценки). Иначе ничего не трогаем.
    if (chatId === activeChatId) {
      updateInputState();
      await loadMessages().catch(() => {});
      els.input.focus();
    }
  }
}
