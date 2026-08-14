// Модальные окна: ловушка фокуса, Escape и возврат фокуса на кнопку-источник.
//
// Правило одно на все окна, поэтому живёт здесь, а не повторяется в каждом
// разделе: забыть его в одном месте — значит получить окно, из которого
// клавиатура уходит на фон, и никто этого не заметит.
//
// Почему ловушка на Tab, а не inert на фоне: два окна лежат внутри .app
// (событие календаря, методика расчёта), два — снаружи. Общего контейнера,
// который можно погасить, не существует, а переносить разметку ради этого
// дороже, чем перехватить Tab. Скринридеру границу задаёт aria-modal.

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), '
  + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Открытые окна стопкой: Escape закрывает верхнее, а не все сразу
const open = [];

function focusableIn(root) {
  return [...root.querySelectorAll(FOCUSABLE)]
    .filter((el) => el.offsetParent !== null && !el.closest("[hidden]"));
}

/** Верхнее окно, которое сейчас действительно на экране.
 *
 * Раздел можно сменить, не закрыв окно, — кнопкой «назад» браузера или сменой
 * hash. Окно уезжает вместе с разделом, но остаётся в стопке: достижимых
 * элементов в нём больше нет, и ловушка гасила КАЖДЫЙ Tab — страница
 * переставала слушаться клавиатуры вовсе, а закрыть невидимое окно
 * пользователю нечем. Такие окна снимаем со стопки сами.
 */
function topModal() {
  while (open.length) {
    const top = open[open.length - 1];
    // Проверяем по атрибуту, а не по offsetParent: подложка position: fixed,
    // и offsetParent у неё null даже когда она на виду.
    if (!top.backdrop.hidden && !top.backdrop.closest("[hidden]")) return top;
    closeModal(top.backdrop);
  }
  return null;
}

function onKeydown(e) {
  const top = topModal();
  if (!top) return;

  if (e.key === "Escape") {
    e.preventDefault();
    // Escape принадлежит верхнему слою и дальше не идёт: под окном может
    // лежать своя шторка со своим Escape (параметры чата), и одно нажатие
    // закрывало бы оба слоя разом.
    e.stopPropagation();
    closeModal(top.backdrop);
    return;
  }
  if (e.key !== "Tab") return;

  // Ловушка: за краями списка перескакиваем на противоположный конец
  const items = focusableIn(top.backdrop);
  if (!items.length) {
    e.preventDefault();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (e.shiftKey && (active === first || !top.backdrop.contains(active))) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (active === last || !top.backdrop.contains(active))) {
    e.preventDefault();
    first.focus();
  }
}

/** Открыть окно.
 *
 * initial — куда поставить фокус (по умолчанию первый элемент окна).
 * onClose — уборка раздела: вызывается при ЛЮБОМ закрытии, в том числе по
 * Escape и щелчку по подложке. Без неё окно, закрытое с клавиатуры, оставляло
 * бы за собой состояние вроде «какое событие правим».
 */
export function openModal(backdrop, initial = null, onClose = null) {
  if (!backdrop.hidden) return;
  const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  const box = backdrop.querySelector(".modal") || backdrop;
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  // Заголовок окна — его же имя для скринридера
  const heading = box.querySelector("h3");
  if (heading) {
    if (!heading.id) heading.id = `${backdrop.id}-title`;
    box.setAttribute("aria-labelledby", heading.id);
  }

  backdrop.hidden = false;
  if (open.length === 0) document.addEventListener("keydown", onKeydown, true);
  open.push({ backdrop, trigger, onClose });

  (initial || focusableIn(backdrop)[0] || box).focus();
}

/** Закрыть окно и вернуть фокус туда, откуда его открыли. */
export function closeModal(backdrop) {
  const index = open.findIndex((m) => m.backdrop === backdrop);
  if (index === -1) {
    backdrop.hidden = true;   // закрыли окно, которое открыли в обход модуля
    return;
  }
  const { trigger, onClose } = open[index];
  open.splice(index, 1);
  backdrop.hidden = true;
  if (open.length === 0) document.removeEventListener("keydown", onKeydown, true);
  if (onClose) onClose();
  // Возврат фокуса — только если кнопка ещё на месте и видима: список мог
  // перерисоваться, пока окно было открыто.
  if (trigger && trigger.isConnected && trigger.offsetParent !== null) trigger.focus();
}

/** Закрытие щелчком по подложке — одинаково у всех окон. */
export function bindBackdropClose(backdrop) {
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) closeModal(backdrop);
  });
}
