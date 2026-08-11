// Раздел «Заметки»: список с поиском/фильтрами, просмотр, редактор с предпросмотром.
import { api } from "/static/js/api.js";
import { bindCodeCopy } from "/static/js/clipboard.js";
import { renderMarkdown } from "/static/js/markdown.js";

let notes = [];
let activeNoteId = null;
let editingNoteId = null; // null — новая заметка
let toast = () => {};
// Адресата общей заметки выбирают администратор и сотрудник без группы;
// состоящий в группе публикует только в свою (см. app/scoping.py).
let me = null;
let canChooseGroup = false;

// Сколько тегов помещается на плитку до счётчика «+N»
const TILE_TAGS = 3;

const els = {};

function $(id) { return document.getElementById(id); }

export function initNotes(toastFn, currentUser) {
  toast = toastFn;
  me = currentUser;
  canChooseGroup = currentUser.role === "admin" ||
    currentUser.group_id === null || currentUser.group_id === undefined;
  Object.assign(els, {
    grid: $("notes-grid"),
    gridWrap: $("notes-grid-wrap"),
    backBtn: $("note-back-btn"),
    search: $("notes-search"),
    tags: $("notes-tags"),
    scope: $("notes-scope"),
    newBtn: $("note-new-btn"),
    empty: $("note-empty"),
    view: $("note-view"),
    viewTitle: $("note-view-title"),
    viewMeta: $("note-view-meta"),
    viewTags: $("note-view-tags"),
    viewBody: $("note-view-body"),
    editBtn: $("note-edit-btn"),
    deleteBtn: $("note-delete-btn"),
    pdfBtn: $("note-pdf-btn"),
    editor: $("note-editor"),
    editTitle: $("note-edit-title"),
    editTags: $("note-edit-tags"),
    editScope: $("note-edit-scope"),
    editGroup: $("note-edit-group"),
    editBody: $("note-edit-body"),
    preview: $("note-edit-preview"),
    tabEdit: $("note-tab-edit"),
    tabPreview: $("note-tab-preview"),
    cancelBtn: $("note-cancel-btn"),
  });

  let searchTimer = null;
  const debouncedRefresh = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refreshList, 250);
  };
  els.search.addEventListener("input", debouncedRefresh);
  els.tags.addEventListener("input", debouncedRefresh);
  els.scope.addEventListener("change", refreshList);

  els.newBtn.addEventListener("click", () => openEditor(null));
  els.editBtn.addEventListener("click", () => {
    const note = notes.find((n) => n.id === activeNoteId);
    if (note) openEditor(note);
  });
  els.deleteBtn.addEventListener("click", deleteActiveNote);
  els.pdfBtn.addEventListener("click", exportActiveNotePdf);
  els.cancelBtn.addEventListener("click", closeEditor);
  els.editor.addEventListener("submit", saveNote);

  els.editScope.addEventListener("change", syncGroupPicker);
  if (canChooseGroup) loadGroupOptions();

  els.tabEdit.addEventListener("click", () => setPreview(false));
  els.tabPreview.addEventListener("click", () => setPreview(true));
  els.backBtn.addEventListener("click", backToGrid);
  initTileDrag();

  // Кнопку «Копировать» в блоках кода рисует общий markdown-рендер, а работать
  // она обязана и здесь, а не только в чате.
  bindCodeCopy(els.viewBody);
  bindCodeCopy(els.preview);

  window.addEventListener("section-shown", (e) => {
    if (e.detail === "notes") refreshList();
  });
}

// --- Группа заметки ---

function isOwn(noteId) {
  const note = notes.find((n) => n.id === noteId);
  return Boolean(note) && note.owner_id === me.id;
}

function scopeLabel(note) {
  if (note.scope !== "shared") return "Личная заметка";
  return note.group_name ? `Общая заметка · ${note.group_name}` : "Общая заметка (всем)";
}

// Сменить адресата чужой заметки может только администратор — остальным поле
// не показываем, иначе оно молча ничего не делало бы при сохранении.
function mayTargetGroup(noteId) {
  if (!canChooseGroup) return false;
  return noteId === null || me.role === "admin" || isOwn(noteId);
}

// Выбор адресата виден только у общей заметки и только тому, кто вправе менять
function syncGroupPicker() {
  els.editGroup.hidden =
    els.editScope.value !== "shared" || !mayTargetGroup(editingNoteId);
}

async function loadGroupOptions() {
  let groups = [];
  try {
    groups = await api("/api/groups");
  } catch { return; }  // без справочника остаётся вариант «Всем»
  for (const g of groups) {
    const opt = document.createElement("option");
    opt.value = String(g.id);
    opt.textContent = g.name;
    els.editGroup.appendChild(opt);
  }
}

// --- Список ---

async function refreshList() {
  const params = new URLSearchParams();
  if (els.search.value.trim()) params.set("query", els.search.value.trim());
  if (els.tags.value.trim()) params.set("tags", els.tags.value.trim());
  if (els.scope.value !== "all") params.set("scope", els.scope.value);
  notes = await api(`/api/notes?${params}`);
  if (activeNoteId !== null && !notes.some((n) => n.id === activeNoteId)) {
    activeNoteId = null;
  }
  renderList();
  renderMain();
}

// Плитки заметок в главной области. Закреплённые — первыми и в порядке,
// заданном перетаскиванием (порядок личный, см. note_prefs).
function renderList() {
  const items = notes.map(noteTile);
  els.grid.replaceChildren(...items);
  els.empty.hidden = notes.length > 0;
}

function noteTile(note) {
  const tile = document.createElement("article");
  tile.className = note.pinned ? "note-tile pinned" : "note-tile";
  tile.dataset.noteId = String(note.id);
  if (note.pinned) {
    // Перетаскиванием переставляются только закреплённые: у остальных
    // порядок по дате, и ручной там быстро превратился бы в свалку.
    tile.draggable = true;
  }
  tile.addEventListener("click", (e) => {
    if (e.target.closest(".note-pin")) return;
    selectNote(note.id);
  });

  const head = document.createElement("div");
  head.className = "note-tile-head";
  const title = document.createElement("h4");
  title.className = "note-tile-title";
  title.textContent = note.title;
  head.appendChild(title);

  const pin = document.createElement("button");
  pin.type = "button";
  pin.className = note.pinned ? "note-pin on" : "note-pin";
  pin.textContent = "📌";
  pin.title = note.pinned ? "Открепить" : "Закрепить наверху";
  pin.addEventListener("click", () => togglePin(note));
  head.appendChild(pin);
  tile.appendChild(head);

  const snippet = document.createElement("p");
  snippet.className = "note-tile-body";
  const text = plainSnippet(note.body);
  snippet.textContent = text || "Без текста";
  if (!text) snippet.classList.add("empty");
  tile.appendChild(snippet);

  const meta = document.createElement("div");
  meta.className = "note-tile-meta";
  // Метку «личная» не рисуем: личных большинство, и подписывать ими каждую
  // плитку — шум. Новость — как раз общая заметка, её и помечаем.
  if (note.scope === "shared") {
    const scopeBadge = document.createElement("span");
    scopeBadge.className = "badge shared";
    scopeBadge.textContent = note.group_name || "общая";
    scopeBadge.title = note.group_name ? `Общая заметка · ${note.group_name}` : "Общая заметка";
    meta.appendChild(scopeBadge);
  }
  // Тегов показываем три: одна заметка с восемью растягивала весь ряд сетки,
  // и у соседних плиток появлялась дыра в полкарточки.
  for (const t of note.tags.slice(0, TILE_TAGS)) {
    const tag = document.createElement("span");
    tag.className = "badge tag";
    tag.textContent = t;
    meta.appendChild(tag);
  }
  if (note.tags.length > TILE_TAGS) {
    const rest = document.createElement("span");
    rest.className = "badge tag more";
    rest.textContent = `+${note.tags.length - TILE_TAGS}`;
    rest.title = note.tags.slice(TILE_TAGS).join(", ");
    meta.appendChild(rest);
  }
  const when = document.createElement("span");
  when.className = "note-tile-date";
  when.textContent = fmtDate(note.updated_at);
  meta.appendChild(when);
  tile.appendChild(meta);
  return tile;
}

// Первые строки заметки без разметки — на плитке нужен смысл, а не решётки.
// Блоки кода и таблицы выбрасываем целиком: из таблицы получалась каша вроде
// «Заголовок а б 1 2», по которой заметку не узнать.
function plainSnippet(body) {
  return (body || "")
    .replace(/```[\s\S]*?```/g, " ")
    .split("\n")
    .filter((line) => !/^\s*\|/.test(line) && !/^\s*[-|: ]+$/.test(line))
    .join(" ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")   // ссылки — оставляем подпись
    .replace(/[#>*_`~]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 220);
}

async function togglePin(note) {
  try {
    await api(`/api/notes/${note.id}/pin`, { method: "PUT",
                                             body: { pinned: !note.pinned } });
    await refreshList();
  } catch (e) {
    toast(e.detail || "Не удалось закрепить заметку", true);
  }
}

function backToGrid() {
  activeNoteId = null;
  closeEditor();
  renderList();
  renderMain();
}

// Перетаскивание закреплённых плиток. Делегирование на сетку: плитки
// перерисовываются целиком после каждого обновления списка.
function initTileDrag() {
  let dragged = null;
  els.grid.addEventListener("dragstart", (e) => {
    dragged = e.target.closest(".note-tile.pinned");
    if (!dragged) return;
    dragged.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    // Firefox не начинает перетаскивание, пока в dataTransfer пусто
    e.dataTransfer.setData("text/plain", dragged.dataset.noteId);
  });
  els.grid.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
  });
  els.grid.addEventListener("dragover", (e) => {
    const over = e.target.closest(".note-tile.pinned");
    if (!dragged || !over || over === dragged) return;
    e.preventDefault();
    // Меняем местами прямо в разметке — порядок читаем из неё же при отпускании
    const tiles = [...els.grid.children];
    const from = tiles.indexOf(dragged);
    const to = tiles.indexOf(over);
    over.insertAdjacentElement(from < to ? "afterend" : "beforebegin", dragged);
  });
  els.grid.addEventListener("drop", async (e) => {
    e.preventDefault();
    const ids = [...els.grid.querySelectorAll(".note-tile.pinned")]
      .map((t) => Number(t.dataset.noteId));
    if (!ids.length) return;
    try {
      await api("/api/notes/order", { method: "PUT", body: { ids } });
      await refreshList();
    } catch (err) {
      toast(err.detail || "Не удалось сохранить порядок", true);
      await refreshList();   // вернуть как было
    }
  });
}

function selectNote(id) {
  activeNoteId = id;
  closeEditor();
  renderList();
  renderMain();
}

// --- Просмотр ---

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.includes("+") || iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric",
                                     hour: "2-digit", minute: "2-digit" });
}

function renderMain() {
  const editorOpen = !els.editor.hidden;
  const note = notes.find((n) => n.id === activeNoteId);
  // Три состояния раздела: сетка плиток, просмотр заметки, редактор. Хранятся
  // в модульных переменных, поэтому уход в чат и возврат ничего не сбрасывает:
  // ушли из редактора — вернулись в редактор.
  els.gridWrap.hidden = Boolean(note) || editorOpen;
  els.view.hidden = !note || editorOpen;
  if (!note || editorOpen) return;

  els.viewTitle.textContent = note.title;

  const parts = [scopeLabel(note), `автор: ${note.author_name}`,
                 `создана: ${fmtDate(note.created_at)}`];
  if (note.updated_at !== note.created_at && note.updated_by_name) {
    parts.push(`изменено: ${note.updated_by_name}, ${fmtDate(note.updated_at)}`);
  }
  els.viewMeta.textContent = parts.join(" · ");

  els.viewTags.replaceChildren(...note.tags.map((t) => {
    const span = document.createElement("span");
    span.className = "badge";
    span.textContent = t;
    return span;
  }));

  els.viewBody.innerHTML = renderMarkdown(note.body || "*Пустая заметка*");
}

// Экспорт заметки в PDF через диалог печати браузера (offline, KaTeX уже
// загружен — формулы печатаются как отрендерены; «Сохранить как PDF»).
function exportActiveNotePdf() {
  const note = notes.find((n) => n.id === activeNoteId);
  if (!note) return;

  let area = document.getElementById("pdf-print");
  if (!area) {
    area = document.createElement("div");
    area.id = "pdf-print";
    document.body.appendChild(area);
  }
  const meta = [scopeLabel(note), `автор: ${note.author_name}`,
                `создана: ${fmtDate(note.created_at)}`];
  if (note.updated_at !== note.created_at && note.updated_by_name) {
    meta.push(`изменено: ${note.updated_by_name}, ${fmtDate(note.updated_at)}`);
  }
  // Заголовок и мета — как текст (экранируются через textContent), тело —
  // уже безопасный HTML из нашего рендера (renderMarkdown санитизирует ввод).
  area.replaceChildren();
  const h = document.createElement("h1");
  h.className = "pdf-title";
  h.textContent = note.title;
  const m = document.createElement("div");
  m.className = "pdf-meta";
  m.textContent = meta.join(" · ");
  const tags = document.createElement("div");
  tags.className = "pdf-tags";
  tags.textContent = note.tags.length ? "Теги: " + note.tags.join(", ") : "";
  const body = document.createElement("div");
  body.className = "msg-body";
  body.innerHTML = renderMarkdown(note.body || "");
  area.append(h, m, tags, body);

  document.body.classList.add("printing-note");
  const cleanup = () => {
    document.body.classList.remove("printing-note");
    window.removeEventListener("afterprint", cleanup);
  };
  window.addEventListener("afterprint", cleanup);
  window.print();
}

async function deleteActiveNote() {
  const note = notes.find((n) => n.id === activeNoteId);
  if (!note) return;
  if (!confirm(`Удалить заметку «${note.title}»?`)) return;
  try {
    await api(`/api/notes/${note.id}`, { method: "DELETE" });
    toast(`Заметка «${note.title}» удалена`);
    activeNoteId = null;
    await refreshList();
  } catch (e) {
    toast(e.detail, true);
  }
}

// --- Редактор ---

function openEditor(note) {
  editingNoteId = note ? note.id : null;
  els.editTitle.value = note ? note.title : "";
  els.editTags.value = note ? note.tags.join(", ") : "";
  els.editScope.value = note ? note.scope : "personal";
  els.editGroup.value = note && note.group_id ? String(note.group_id) : "";
  syncGroupPicker();
  els.editBody.value = note ? note.body : "";
  setPreview(false);
  els.editor.hidden = false;
  els.view.hidden = true;
  els.empty.hidden = true;
  els.editTitle.focus();
}

function closeEditor() {
  els.editor.hidden = true;
  renderMain();
}

function setPreview(showPreview) {
  els.tabEdit.classList.toggle("active", !showPreview);
  els.tabPreview.classList.toggle("active", showPreview);
  els.editBody.hidden = showPreview;
  els.preview.hidden = !showPreview;
  if (showPreview) {
    els.preview.innerHTML = renderMarkdown(els.editBody.value || "*Пусто*");
  }
}

async function saveNote(e) {
  e.preventDefault();
  const body = {
    title: els.editTitle.value.trim(),
    body: els.editBody.value,
    tags: els.editTags.value.split(",").map((t) => t.trim()).filter(Boolean),
    scope: els.editScope.value,
  };
  // group_id отправляем ровно тогда, когда поле было показано: сервер
  // считает переданное поле явной сменой адресата и проверяет права.
  if (mayTargetGroup(editingNoteId)) {
    body.group_id = Number(els.editGroup.value) || null;
  }
  if (!body.title) {
    toast("Заголовок не может быть пустым", true);
    return;
  }
  try {
    const saved = editingNoteId === null
      ? await api("/api/notes", { method: "POST", body })
      : await api(`/api/notes/${editingNoteId}`, { method: "PUT", body });
    activeNoteId = saved.id;
    els.editor.hidden = true;
    await refreshList();
    toast("Заметка сохранена");
  } catch (err) {
    toast(err.detail, true);
  }
}
