// Раздел «Чат»: список чатов, история, SSE-стриминг, «Размышления», стоп.
import { api } from "/static/js/api.js";
import { escapeHtml, renderMarkdown } from "/static/js/markdown.js";

let chats = [];
let activeChatId = null;
// Состояние стриминга — по одному на чат. Инференс в чате продолжается, даже
// если пользователь ушёл в другой чат; кнопки/панель относятся к активному чату.
// chatId -> { ac, live, liveBody, liveReasoning, queueNote, liveStats,
//             reasoningText, contentText, messageId, genStart }
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
  els.options = $("chat-options");
  els.optionsBtn = $("chat-options-btn");
  els.resizeHandle = $("chat-resize-handle");
  els.ctxTools = $("ctx-tools");
  els.ctxRag = $("ctx-rag");
  els.ctxRagLabel = $("ctx-rag-label");
  els.ctxThink = $("ctx-think");
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

  // Окно «Параметры чата» (поповер под шестерёнкой)
  els.optionsBtn.addEventListener("click", () => {
    els.options.hidden = !els.options.hidden;
  });
  $("chat-options-close").addEventListener("click", () => { els.options.hidden = true; });
  // клик вне окна закрывает его
  document.addEventListener("click", (e) => {
    if (els.options.hidden) return;
    if (e.target.closest("#chat-options") || e.target.closest("#chat-options-btn")) return;
    els.options.hidden = true;
  });

  initInputResize();

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

  // Действия под сообщениями: продолжить / перегенерировать (ответ модели),
  // редактировать / удалить (сообщение пользователя) — делегирование.
  els.messages.addEventListener("click", (e) => {
    const btn = e.target.closest(".msg-op");
    if (!btn) return;
    const op = btn.dataset.op;
    const msgEl = btn.closest(".msg");
    const msgId = Number(msgEl.dataset.messageId);
    if (op === "continue") continueGeneration();
    else if (op === "regenerate") regenerateAnswer();
    else if (op === "delete") deleteTurn(msgId);
    else if (op === "edit") startEditUserMessage(msgEl, msgId);
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

// Кэш специализаций — используется только в модалке «Промпт»: новый чат
// создаётся без промпта, режим при необходимости выбирают уже в самом чате.
async function loadSpecializations() {
  try {
    specializations = await api("/api/specializations");
  } catch {
    specializations = [];
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
  applyChatToggles();
  updateInputState();
  scrollToBottom(true);  // при переключении чата — принудительно вниз
}

async function createChat() {
  // Без промпта по умолчанию — режим выбирается в чате через ⚙ → Промпт
  const chat = await api("/api/chats", { method: "POST", body: {} });
  activeChatId = chat.id;
  els.messages.replaceChildren(); // новый чат пуст — убрать сообщения предыдущего
  messagesFollow = true;          // сброс sticky для нового пустого чата
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
    if (activeChatId === chat.id) activeChatId = null;
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

// Компактная строка статистики генерации (счётчики сервера, как в llama.cpp)
function statsText(stats) {
  if (!stats || !stats.completion_tokens) return "";
  const parts = [`${stats.completion_tokens} ток.`];
  if (stats.tokens_per_second) parts.push(`${stats.tokens_per_second} ток/с`);
  if (stats.context_percent !== null && stats.context_percent !== undefined) {
    parts.push(`контекст ${stats.context_percent}%`);
  }
  return parts.join(" · ");
}

function opButton(op, label, title) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "msg-op";
  btn.dataset.op = op;
  btn.textContent = label;
  btn.title = title;
  return btn;
}

// Панель под ответом модели: 👍/👎, «Копировать», «Markdown», статистика,
// а для ПОСЛЕДНЕГО ответа — «Продолжить» и «Перегенерировать» (как в llama.cpp).
function answerActions(msg, getRaw, bodyEl, isLast) {
  const bar = document.createElement("div");
  bar.className = "feedback-bar";
  const rating = msg ? msg.feedback_rating : null;
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

  if (isLast) {
    bar.appendChild(opButton("continue", "⏵ Продолжить",
      "Дописать оборванный ответ в это же сообщение"));
    bar.appendChild(opButton("regenerate", "↻ Перегенерировать",
      "Сгенерировать ответ на этот запрос заново"));
  }

  const stats = statsText(msg && msg.stats);
  if (stats) {
    const s = document.createElement("span");
    s.className = "msg-stats";
    s.textContent = stats;
    bar.appendChild(s);
  }
  return bar;
}

// Панель под сообщением пользователя: «Редактировать» и «Удалить».
function userActions() {
  const bar = document.createElement("div");
  bar.className = "feedback-bar user-bar";
  bar.appendChild(opButton("edit", "✎ Редактировать",
    "Изменить запрос и сгенерировать ответ заново"));
  bar.appendChild(opButton("delete", "🗑 Удалить",
    "Удалить этот запрос и ответ на него"));
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

function messageNode(msg, isLastAssistant = false) {
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
    if (msg.id) div.appendChild(answerActions(msg, () => msg.content, body, isLastAssistant));
  } else {
    if (msg.id) div.dataset.messageId = String(msg.id);
    if (msg.content) {
      const body = document.createElement("div");
      body.className = "msg-body";
      body.textContent = msg.content;
      div.appendChild(body);
    }
    for (const att of msg.attachments || []) div.appendChild(attachmentBlock(att));
    // Действия «Редактировать/Удалить» — только у сохранённых сообщений с текстом
    if (msg.id && msg.content) div.appendChild(userActions());
  }
  return div;
}

async function loadMessages() {
  if (activeChatId === null) return;
  const messages = await api(`/api/chats/${activeChatId}/messages`);
  // id последнего ответа модели — только у него кнопки «Продолжить/Перегенерировать»
  let lastAssistantId = null;
  for (const m of messages) if (m.role === "assistant") lastAssistantId = m.id;
  els.messages.replaceChildren(...messages.map((m) =>
    messageNode(m, m.role === "assistant" && m.id === lastAssistantId)));
  scrollToBottom(true);  // после перерисовки истории — принудительно вниз
}

// --- Высота поля ввода: авторост по содержимому + ручка (тянуть вверх) ---
// Стартовая высота (rows=3) — минимум; ручная высота ручкой — «липкая».
const INPUT_MAX_VH = 0.55; // максимум — чуть больше половины окна

let inputMinHeight = 0;    // измеряется лениво: при init форма ещё скрыта
let inputManualHeight = 0; // 0 — пользователь ручку не трогал

function inputMaxHeight() {
  return Math.round(window.innerHeight * INPUT_MAX_VH);
}

function measureInputMin() {
  if (!inputMinHeight && els.input.offsetHeight) inputMinHeight = els.input.offsetHeight;
}

function autogrowInput() {
  measureInputMin();
  const input = els.input;
  const prev = input.style.height;
  input.style.height = "auto";
  const wanted = Math.max(input.scrollHeight + 2, inputMinHeight, inputManualHeight);
  const h = Math.min(wanted, inputMaxHeight());
  input.style.height = h ? h + "px" : prev;
}

function initInputResize() {
  els.input.addEventListener("input", autogrowInput);

  els.resizeHandle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    measureInputMin();
    const startY = e.clientY;
    const startH = els.input.offsetHeight;
    const onMove = (ev) => {
      const h = Math.min(Math.max(startH + (startY - ev.clientY), inputMinHeight),
                         inputMaxHeight());
      inputManualHeight = h;
      els.input.style.height = h + "px";
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      // вернули к минимуму — считаем, что ручная высота сброшена
      if (inputManualHeight <= inputMinHeight) inputManualHeight = 0;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });
}

function updateInputState() {
  // Состояние строки ввода относится к активному чату (у каждого чата — своё)
  const streaming = isStreaming(activeChatId);
  const hasChat = activeChatId !== null;
  els.empty.hidden = hasChat;
  els.form.hidden = !hasChat;
  els.optionsBtn.hidden = !hasChat; // шестерёнка и шторка — только при открытом чате
  if (!hasChat) els.options.hidden = true;
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
  updateLiveStats(st);
  if (scroll && st.chatId === activeChatId) scrollToBottom();
}

// Живой счётчик токенов во время генерации: оценка по объёму текста
// (≈4 символа/токен), скорость — от начала генерации. Точные счётчики
// сервера приходят в конце (событие stats) и сохраняются в сообщении,
// после перечитывания истории показываются под ответом.
function updateLiveStats(st) {
  if (!st.liveStats) return;
  const chars = (st.reasoningText || "").length + (st.contentText || "").length;
  if (!chars || !st.genStart) { st.liveStats.textContent = ""; return; }
  const toks = Math.max(1, Math.round(chars / 4));
  const secs = (performance.now() - st.genStart) / 1000;
  const tps = secs > 0 ? toks / secs : 0;
  st.liveStats.textContent = `~${toks} ток · ${tps.toFixed(1)} ток/с`;
}

// Снять индикаторы стрима с живого контейнера (конец генерации/ошибка)
function finishLive(st) {
  st.live.classList.remove("streaming");
  st.liveReasoning.classList.remove("thinking");
  const summary = st.liveReasoning.querySelector("summary");
  if (summary) summary.textContent = "Размышления";
}

// Собрать живой контейнер ответа (общий для отправки и «Продолжить»)
function buildLive(chatId) {
  const live = document.createElement("div");
  live.className = "msg msg-assistant";
  const queueNote = document.createElement("div");
  queueNote.className = "msg-note queue-note";
  queueNote.hidden = true;
  const liveReasoning = reasoningBlock("", false);
  liveReasoning.hidden = true;
  const liveBody = document.createElement("div");
  liveBody.className = "msg-body";
  const liveStats = document.createElement("div");
  liveStats.className = "msg-live-stats";
  live.append(queueNote, liveReasoning, liveBody, liveStats);
  els.messages.appendChild(live);

  const st = {
    chatId, ac: new AbortController(), live, liveBody, liveReasoning, queueNote,
    liveStats, reasoningText: "", contentText: "", messageId: null,
    genStart: 0, reasoningFollow: true,
  };
  liveReasoning.querySelector(".reasoning-body").addEventListener("scroll", (e) => {
    st.reasoningFollow = isAtBottom(e.currentTarget);
  });
  return st;
}

// --- «Продолжить», «Перегенерировать», «Удалить», «Редактировать» ---

async function continueGeneration() {
  if (activeChatId === null || isStreaming(activeChatId)) return;
  const chatId = activeChatId;
  const st = buildLive(chatId);
  const { queueNote } = st;
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
        } else if (event === "reasoning") {
          if (!st.genStart) st.genStart = performance.now();
          st.reasoningText += data.text;
        } else if (event === "content") {
          if (!st.genStart) st.genStart = performance.now();
          st.contentText += data.text;
        } else if (event === "error") throw new Error(data.detail);
      });
      renderLive(st, true);
    }
  } catch (e) {
    if (e.name !== "AbortError") els.toast(e.message || "Не удалось продолжить", true);
  } finally {
    streams.delete(chatId);
    finishLive(st);
    st.live.remove();
    renderChatList();
    // Продолжение дописано к сообщению в БД — перечитываем историю целиком
    if (chatId === activeChatId) {
      updateInputState();
      await loadMessages().catch(() => {});
      els.input.focus();
    }
  }
}

// Перегенерировать последний ответ: удалить его вместе с породившим запросом
// пользователя и отправить этот запрос заново (вложения не переносятся).
async function regenerateAnswer() {
  if (activeChatId === null || isStreaming(activeChatId)) return;
  const chatId = activeChatId;
  const messages = await api(`/api/chats/${chatId}/messages`);
  let ai = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") { ai = i; break; }
  }
  if (ai < 0) return;
  // ближайший запрос пользователя перед этим ответом
  let ui = -1;
  for (let i = ai - 1; i >= 0; i--) {
    if (messages[i].role === "user") { ui = i; break; }
  }
  if (ui < 0) { els.toast("Не найден запрос для перегенерации", true); return; }
  const userContent = messages[ui].content;
  try {
    // удаляем всё, начиная с запроса пользователя (запрос + ответ и всё после)
    for (let i = messages.length - 1; i >= ui; i--) {
      await api(`/api/chats/${chatId}/messages/${messages[i].id}`, { method: "DELETE" });
    }
    if (chatId === activeChatId) await loadMessages();
    await submitTurn(chatId, userContent, []);
  } catch (e) {
    els.toast(e.detail || "Не удалось перегенерировать", true);
  }
}

// Удалить «ход» — сообщение пользователя и следующий за ним ответ модели.
async function deleteTurn(userMsgId) {
  if (activeChatId === null || isStreaming(activeChatId)) return;
  if (!confirm("Удалить этот запрос и ответ на него?")) return;
  const chatId = activeChatId;
  try {
    const messages = await api(`/api/chats/${chatId}/messages`);
    const idx = messages.findIndex((m) => m.id === userMsgId);
    if (idx < 0) return;
    await api(`/api/chats/${chatId}/messages/${userMsgId}`, { method: "DELETE" });
    const next = messages[idx + 1];
    if (next && next.role === "assistant") {
      await api(`/api/chats/${chatId}/messages/${next.id}`, { method: "DELETE" });
    }
    await loadMessages();
  } catch (e) {
    els.toast(e.detail || "Не удалось удалить", true);
  }
}

// Инлайн-редактор запроса пользователя: правка → удаление этого хода и всего
// после него → повторная отправка изменённого запроса.
function startEditUserMessage(msgEl, msgId) {
  if (isStreaming(activeChatId)) return;
  const bodyEl = msgEl.querySelector(".msg-body");
  const bar = msgEl.querySelector(".user-bar");
  if (!bodyEl || msgEl.querySelector(".edit-box")) return;
  const original = bodyEl.textContent;

  const box = document.createElement("div");
  box.className = "edit-box";
  const ta = document.createElement("textarea");
  ta.className = "edit-input";
  ta.value = original;
  const actions = document.createElement("div");
  actions.className = "edit-actions";
  const save = document.createElement("button");
  save.type = "button"; save.className = "btn btn-small btn-primary"; save.textContent = "Отправить";
  const cancel = document.createElement("button");
  cancel.type = "button"; cancel.className = "btn btn-small"; cancel.textContent = "Отмена";
  actions.append(save, cancel);
  box.append(ta, actions);

  bodyEl.hidden = true;
  if (bar) bar.hidden = true;
  msgEl.appendChild(box);
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);

  const close = () => { box.remove(); bodyEl.hidden = false; if (bar) bar.hidden = false; };
  cancel.addEventListener("click", close);
  save.addEventListener("click", async () => {
    const text = ta.value.trim();
    if (!text) { close(); return; }
    const chatId = activeChatId;
    try {
      const messages = await api(`/api/chats/${chatId}/messages`);
      const idx = messages.findIndex((m) => m.id === msgId);
      if (idx < 0) { close(); return; }
      // удалить этот запрос и всё, что после него
      for (let i = messages.length - 1; i >= idx; i--) {
        await api(`/api/chats/${chatId}/messages/${messages[i].id}`, { method: "DELETE" });
      }
      if (chatId === activeChatId) await loadMessages();
      await submitTurn(chatId, text, []);
    } catch (e) {
      els.toast(e.detail || "Не удалось изменить запрос", true);
      close();
    }
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save.click(); }
    else if (e.key === "Escape") close();
  });
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
  autogrowInput(); // поле обратно к минимуму (или к ручной высоте)
  await submitTurn(chatId, content, attachments);
}

// Ядро одной генерации: рисует запрос пользователя, живой ответ и стрим.
// Используется отправкой, «Перегенерировать» и «Редактировать».
async function submitTurn(chatId, content, attachments) {
  els.messages.appendChild(messageNode({
    role: "user",
    content,
    attachments: attachments.map((a) => (a.images && a.images.length
      ? { filename: a.filename, image: true }
      : { filename: a.filename, text: a.text || "" })),
  }));
  // Пользователь отправил сообщение — прыгаем вниз и снова следим за стримом.
  scrollToBottom(true);

  const st = buildLive(chatId);
  const { live, liveBody, queueNote } = st;
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
        } else if (event === "reasoning") {
          if (!st.genStart) st.genStart = performance.now();
          st.reasoningText += data.text;
        } else if (event === "content") {
          if (!st.genStart) st.genStart = performance.now();
          st.contentText += data.text;
        } else if (event === "tool") {
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
          if (data.message_id) st.messageId = data.message_id;
        }
      });
      renderLive(st, true);
    }

    if (renamed) {
      const chat = chats.find((c) => c.id === chatId);
      if (chat) chat.title = renamed;
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
    // из БД (чистая версия + панель действий + сохранённая статистика).
    if (chatId === activeChatId) {
      updateInputState();
      await loadMessages().catch(() => {});
      els.input.focus();
    }
  }
}
