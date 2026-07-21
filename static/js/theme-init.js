// Раннее применение темы — ДО отрисовки, чтобы не было «вспышки» светлой
// темы при загрузке. Подключается обычным (блокирующим) тегом в <head>.
// Отдельный файл, а не инлайн-скрипт: CSP страницы (default-src 'self')
// запрещает инлайн-скрипты, но разрешает свои со своего origin.
(function () {
  try {
    var t = localStorage.getItem("theme") || "light";
    var dark = t === "dark" ||
      (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (dark) document.documentElement.setAttribute("data-theme", "dark");
  } catch (e) { /* localStorage может быть недоступен (приватный режим) */ }
})();
