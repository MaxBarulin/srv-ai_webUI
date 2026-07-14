// Раздел «Календарь»: вид «Месяц», «Список ближайших», модальное окно события.
import { api } from "/static/js/api.js";

const LIST_DAYS = 60; // горизонт «Списка ближайших»

let view = "month";           // month | list
let cursor = startOfMonth(new Date()); // первый день отображаемого месяца
let events = [];
let editingEventId = null;    // null — новое событие
let editingEvent = null;
let toast = () => {};

const els = {};

function $(id) { return document.getElementById(id); }

export function initCalendar(toastFn) {
  toast = toastFn;
  Object.assign(els, {
    title: $("cal-title"),
    prev: $("cal-prev"),
    next: $("cal-next"),
    today: $("cal-today"),
    scope: $("cal-scope"),
    tabMonth: $("cal-tab-month"),
    tabList: $("cal-tab-list"),
    month: $("cal-month"),
    grid: $("cal-grid"),
    list: $("cal-list"),
    newBtn: $("event-new-btn"),
    modal: $("event-modal"),
    form: $("event-form"),
    modalTitle: $("event-modal-title"),
    modalMeta: $("event-modal-meta"),
    fTitle: $("event-title"),
    fDescription: $("event-description"),
    fLocation: $("event-location"),
    fScope: $("event-scope"),
    fAllDay: $("event-allday"),
    fStartDate: $("event-start-date"),
    fStartTime: $("event-start-time"),
    fEndDate: $("event-end-date"),
    fEndTime: $("event-end-time"),
    cancelBtn: $("event-cancel-btn"),
    deleteBtn: $("event-delete-btn"),
  });

  els.prev.addEventListener("click", () => shiftMonth(-1));
  els.next.addEventListener("click", () => shiftMonth(1));
  els.today.addEventListener("click", () => { cursor = startOfMonth(new Date()); refresh(); });
  els.scope.addEventListener("change", refresh);
  els.tabMonth.addEventListener("click", () => setView("month"));
  els.tabList.addEventListener("click", () => setView("list"));

  els.newBtn.addEventListener("click", () => openModal(null, new Date()));
  els.cancelBtn.addEventListener("click", closeModal);
  els.deleteBtn.addEventListener("click", deleteEditingEvent);
  els.form.addEventListener("submit", saveEvent);
  els.fAllDay.addEventListener("change", syncAllDay);
  els.modal.addEventListener("click", (e) => { if (e.target === els.modal) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !els.modal.hidden) closeModal();
  });

  window.addEventListener("section-shown", (e) => {
    if (e.detail === "calendar") refresh();
  });
}

// --- Даты ---

function pad(n) { return String(n).padStart(2, "0"); }

function startOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }

// Понедельник недели, содержащей дату
function startOfWeek(d) {
  const res = new Date(d);
  res.setDate(res.getDate() - ((res.getDay() + 6) % 7));
  return res;
}

function addDays(d, n) {
  const res = new Date(d);
  res.setDate(res.getDate() + n);
  return res;
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function dateKey(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }

// Локальное время → ISO 8601 со смещением часового пояса (§6)
function toISO(d) {
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const abs = Math.abs(off);
  return `${dateKey(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

function parseISO(s) { return new Date(s); }

function fmtTime(d) { return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }

const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь",
                "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

function fmtDayHeading(d) {
  const weekday = d.toLocaleDateString("ru-RU", { weekday: "long" });
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${weekday}`;
}

// --- Загрузка и переключение ---

function setView(name) {
  view = name;
  els.tabMonth.classList.toggle("active", name === "month");
  els.tabList.classList.toggle("active", name === "list");
  refresh();
}

function shiftMonth(delta) {
  cursor = new Date(cursor.getFullYear(), cursor.getMonth() + delta, 1);
  refresh();
}

function visibleRange() {
  if (view === "list") {
    const from = new Date();
    from.setHours(0, 0, 0, 0);
    return [from, addDays(from, LIST_DAYS)];
  }
  const gridStart = startOfWeek(cursor);
  return [gridStart, addDays(gridStart, 42)];
}

async function refresh() {
  const [from, to] = visibleRange();
  const params = new URLSearchParams({ date_from: toISO(from), date_to: toISO(to) });
  if (els.scope.value !== "all") params.set("scope", els.scope.value);
  try {
    events = await api(`/api/events?${params}`);
  } catch (e) {
    toast(e.detail || "Не удалось загрузить события", true);
    return;
  }
  els.month.hidden = view !== "month";
  els.list.hidden = view !== "list";
  if (view === "month") {
    els.title.textContent = `${MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}`;
    renderMonth();
  } else {
    els.title.textContent = `ближайшие ${LIST_DAYS} дней`;
    renderList();
  }
}

function eventsOfDay(day) {
  return events.filter((ev) => {
    const start = parseISO(ev.starts_at);
    const end = parseISO(ev.ends_at);
    const dayEnd = addDays(day, 1);
    return start < dayEnd && end >= day;
  });
}

// --- Вид «Месяц» ---

function eventChip(ev, day) {
  const chip = document.createElement("div");
  chip.className = ev.scope === "shared" ? "cal-chip shared" : "cal-chip";
  const start = parseISO(ev.starts_at);
  const showTime = !ev.all_day && sameDay(start, day);
  chip.textContent = showTime ? `${fmtTime(start)} ${ev.title}` : ev.title;
  chip.title = ev.title + (ev.location ? ` — ${ev.location}` : "");
  chip.addEventListener("click", (e) => {
    e.stopPropagation();
    openModal(ev);
  });
  return chip;
}

function renderMonth() {
  const today = new Date();
  const gridStart = startOfWeek(cursor);
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const day = addDays(gridStart, i);
    const cell = document.createElement("div");
    cell.className = "cal-cell";
    if (day.getMonth() !== cursor.getMonth()) cell.classList.add("other");
    if (sameDay(day, today)) cell.classList.add("today");

    const num = document.createElement("div");
    num.className = "cal-day-num";
    num.textContent = day.getDate();
    cell.appendChild(num);

    for (const ev of eventsOfDay(day)) cell.appendChild(eventChip(ev, day));

    // клик по пустому месту ячейки — новое событие на этот день
    cell.addEventListener("click", () => openModal(null, day));
    cells.push(cell);
  }
  els.grid.replaceChildren(...cells);
}

// --- Вид «Список ближайших» ---

function renderList() {
  const [from] = visibleRange();
  const groups = [];
  for (let i = 0; i <= LIST_DAYS; i++) {
    const day = addDays(from, i);
    const dayEvents = eventsOfDay(day);
    if (dayEvents.length) groups.push([day, dayEvents]);
  }

  if (!groups.length) {
    const empty = document.createElement("div");
    empty.className = "cal-list-empty";
    empty.textContent = "Ближайших событий нет";
    els.list.replaceChildren(empty);
    return;
  }

  els.list.replaceChildren(...groups.map(([day, dayEvents]) => {
    const block = document.createElement("div");
    block.className = "cal-day-group";

    const h = document.createElement("div");
    h.className = "cal-day-heading";
    h.textContent = fmtDayHeading(day);
    block.appendChild(h);

    for (const ev of dayEvents) {
      const row = document.createElement("div");
      row.className = "cal-row";
      row.addEventListener("click", () => openModal(ev));

      const time = document.createElement("div");
      time.className = "cal-row-time";
      if (ev.all_day) {
        time.textContent = "весь день";
      } else {
        const start = parseISO(ev.starts_at);
        const end = parseISO(ev.ends_at);
        time.textContent = sameDay(start, end)
          ? `${fmtTime(start)}–${fmtTime(end)}`
          : `${sameDay(start, day) ? fmtTime(start) : "…"}–${sameDay(end, day) ? fmtTime(end) : "…"}`;
      }
      row.appendChild(time);

      const main = document.createElement("div");
      main.className = "cal-row-main";
      const title = document.createElement("div");
      title.className = "cal-row-title";
      title.textContent = ev.title;
      main.appendChild(title);
      const metaParts = [];
      if (ev.location) metaParts.push(ev.location);
      metaParts.push(`автор: ${ev.author_name}`);
      const meta = document.createElement("div");
      meta.className = "cal-row-meta";
      meta.textContent = metaParts.join(" · ");
      main.appendChild(meta);
      row.appendChild(main);

      const badge = document.createElement("span");
      badge.className = ev.scope === "shared" ? "badge shared" : "badge";
      badge.textContent = ev.scope === "shared" ? "общее" : "личное";
      row.appendChild(badge);

      block.appendChild(row);
    }
    return block;
  }));
}

// --- Модальное окно ---

function syncAllDay() {
  const allDay = els.fAllDay.checked;
  els.fStartTime.hidden = allDay;
  els.fEndTime.hidden = allDay;
  els.fStartTime.required = !allDay;
  els.fEndTime.required = !allDay;
}

function openModal(ev, presetDay) {
  editingEventId = ev ? ev.id : null;
  editingEvent = ev;
  els.modalTitle.textContent = ev ? "Событие" : "Новое событие";
  els.fTitle.value = ev ? ev.title : "";
  els.fDescription.value = ev ? ev.description : "";
  els.fLocation.value = ev ? ev.location : "";
  els.fScope.value = ev ? ev.scope : "personal";
  els.fAllDay.checked = ev ? Boolean(ev.all_day) : false;

  if (ev) {
    const start = parseISO(ev.starts_at);
    const end = parseISO(ev.ends_at);
    els.fStartDate.value = dateKey(start);
    els.fStartTime.value = fmtTime(start);
    els.fEndDate.value = dateKey(end);
    els.fEndTime.value = fmtTime(end);
  } else {
    const day = presetDay || new Date();
    els.fStartDate.value = dateKey(day);
    els.fStartTime.value = "10:00";
    els.fEndDate.value = dateKey(day);
    els.fEndTime.value = "11:00";
  }

  if (ev) {
    const parts = [`автор: ${ev.author_name}`];
    if (ev.updated_at !== ev.created_at && ev.updated_by_name) {
      parts.push(`изменено: ${ev.updated_by_name}`);
    }
    els.modalMeta.textContent = parts.join(" · ");
  } else {
    els.modalMeta.textContent = "";
  }

  els.deleteBtn.hidden = !ev;
  syncAllDay();
  els.modal.hidden = false;
  els.fTitle.focus();
}

function closeModal() {
  els.modal.hidden = true;
  editingEventId = null;
  editingEvent = null;
}

function fieldsToRange() {
  const allDay = els.fAllDay.checked;
  const startTime = allDay ? "00:00" : els.fStartTime.value;
  const endTime = allDay ? "23:59" : els.fEndTime.value;
  const start = new Date(`${els.fStartDate.value}T${startTime}:00`);
  const end = new Date(`${els.fEndDate.value}T${endTime}:00`);
  return [start, end];
}

async function saveEvent(e) {
  e.preventDefault();
  const [start, end] = fieldsToRange();
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    toast("Заполните дату и время", true);
    return;
  }
  if (end < start) {
    toast("Окончание раньше начала", true);
    return;
  }
  const body = {
    title: els.fTitle.value.trim(),
    description: els.fDescription.value,
    location: els.fLocation.value.trim(),
    starts_at: toISO(start),
    ends_at: toISO(end),
    all_day: els.fAllDay.checked,
    scope: els.fScope.value,
  };
  if (!body.title) {
    toast("Название не может быть пустым", true);
    return;
  }
  try {
    if (editingEventId === null) {
      await api("/api/events", { method: "POST", body });
    } else {
      await api(`/api/events/${editingEventId}`, { method: "PUT", body });
    }
    closeModal();
    toast("Событие сохранено");
    await refresh();
  } catch (err) {
    toast(err.detail, true);
  }
}

async function deleteEditingEvent() {
  if (editingEventId === null) return;
  const title = editingEvent ? editingEvent.title : "";
  if (!confirm(`Удалить событие «${title}»?`)) return;
  try {
    await api(`/api/events/${editingEventId}`, { method: "DELETE" });
    closeModal();
    toast(`Событие «${title}» удалено`);
    await refresh();
  } catch (e) {
    toast(e.detail, true);
  }
}
