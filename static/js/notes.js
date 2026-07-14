// Раздел «Заметки»: список с поиском/фильтрами, просмотр, редактор с предпросмотром.
import { api } from "/static/js/api.js";
import { renderMarkdown } from "/static/js/markdown.js";

let notes = [];
let activeNoteId = null;
let editingNoteId = null; // null — новая заметка
let toast = () => {};

const els = {};

function $(id) { return document.getElementById(id); }

export function initNotes(toastFn) {
  toast = toastFn;
  Object.assign(els, {
    list: $("notes-list"),
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
    editor: $("note-editor"),
    editTitle: $("note-edit-title"),
    editTags: $("note-edit-tags"),
    editScope: $("note-edit-scope"),
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
  els.cancelBtn.addEventListener("click", closeEditor);
  els.editor.addEventListener("submit", saveNote);

  els.tabEdit.addEventListener("click", () => setPreview(false));
  els.tabPreview.addEventListener("click", () => setPreview(true));

  window.addEventListener("section-shown", (e) => {
    if (e.detail === "notes") refreshList();
  });
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

function renderList() {
  els.list.replaceChildren(...notes.map((note) => {
    const li = document.createElement("li");
    li.className = note.id === activeNoteId ? "note-item active" : "note-item";
    li.addEventListener("click", () => selectNote(note.id));

    const title = document.createElement("div");
    title.className = "note-item-title";
    title.textContent = note.title;
    li.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "note-item-meta";
    const scopeBadge = document.createElement("span");
    scopeBadge.className = note.scope === "shared" ? "badge shared" : "badge";
    scopeBadge.textContent = note.scope === "shared" ? "общая" : "личная";
    meta.appendChild(scopeBadge);
    if (note.tags.length) {
      const tags = document.createElement("span");
      tags.className = "note-item-tags";
      tags.textContent = note.tags.join(", ");
      meta.appendChild(tags);
    }
    li.appendChild(meta);
    return li;
  }));
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
  els.empty.hidden = Boolean(note) || editorOpen;
  els.view.hidden = !note || editorOpen;
  if (!note || editorOpen) return;

  els.viewTitle.textContent = note.title;

  const parts = [
    `${note.scope === "shared" ? "Общая" : "Личная"} заметка`,
    `автор: ${note.author_name}`,
    `создана: ${fmtDate(note.created_at)}`,
  ];
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
