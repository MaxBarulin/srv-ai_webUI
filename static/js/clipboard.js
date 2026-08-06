// Копирование в буфер обмена и кнопка «Копировать» в блоках кода.
//
// Живёт отдельным модулем, потому что кнопку рисует общий markdown-рендер
// (markdown.js), а пользуются им и чат, и заметки. Раньше обработчик лежал
// только в chat.js — в заметках кнопка исправно появлялась и молча ничего
// не делала.

// navigator.clipboard доступен только в secure context (HTTPS или localhost).
// У нас развёртывание по http://<ip> внутри заводской сети — там его нет
// вовсе, поэтому фолбэк через скрытую textarea + execCommand("copy").
export async function copyText(text) {
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
export function copyWithFeedback(btn, text, label = "Копировать") {
  copyText(text).then((ok) => {
    btn.textContent = ok ? "Скопировано" : "Не удалось";
    setTimeout(() => { btn.textContent = label; }, 1500);
  });
}

// Кнопки «Копировать» в блоках кода. Делегирование на контейнер: разметка
// перерисовывается целиком (стрим ответа, переключение заметки), и вешать
// обработчик на каждую кнопку заново пришлось бы после каждой перерисовки.
// Инлайн-обработчики запрещены CSP, так что это единственный путь.
//
// Вызывать один раз на контейнер — там, где показывается markdown.
export function bindCodeCopy(container) {
  if (!container || container.dataset.codeCopyBound) return;
  container.dataset.codeCopyBound = "1";
  container.addEventListener("click", (e) => {
    const btn = e.target.closest(".code-copy");
    if (!btn) return;
    const code = btn.parentElement.querySelector("code")?.textContent ?? "";
    copyWithFeedback(btn, code);
  });
}
