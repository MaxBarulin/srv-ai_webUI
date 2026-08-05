const fs = require("fs");
const path = require("path");
const FONTS = __dirname + "/../../static/fonts";
const SHOTS = __dirname + "/shots";
const OUT = __dirname + "/../presentation.html";

const b64 = (p) => fs.readFileSync(p).toString("base64");
const font = (f) => `data:font/woff2;base64,${b64(path.join(FONTS, f))}`;
const img = (f) => `data:image/png;base64,${b64(path.join(SHOTS, f + ".png"))}`;

const features = [
  { n: "01", t: "Расчёт по методике отдела", img: "12_calc", side: "right", accent: true,
    lead: "Ключевая связка: методика лежит в общей заметке, в чате её просят применить, числа считает инструмент — не модель «в уме».",
    points: ["<b>Заметка.</b> Нормировщик один раз кладёт методику в общую заметку отдела — с формулами и допусками",
      "<b>Запрос.</b> «Возьми методику из заметок, фреза D=80, z=8 — проверь подачу на зуб»",
      "<b>Расчёт.</b> Модель находит заметку, читает её и передаёт числа инструменту расчётов",
      "<b>Ответ.</b> Таблица исходных данных, результат и ход расчёта: видно, из какой формулы получено каждое число",
      "Вычислитель работает на разборе выражения, без <code>eval</code>; пределы на степень, длину и глубину формулы отбивают «бомбы» вида <code>9**9**9</code>",
      "Вся цепочка — за один вызов: промежуточные значения не переписываются вручную, а значит не теряются по дороге"] },
  { n: "02", t: "Справочник методик предприятия", img: "14_methods", side: "left",
    lead: "Формулы задаёт администратор — модель только подставляет в них числа. Выбор формулы перестаёт быть на совести модели.",
    points: ["Методика — это параметры с единицами и шаги расчёта; проверка формул при сохранении",
      "Список методик уходит модели вместе с запросом: отдельный поиск стоил бы лишнего круга через модель",
      "Прогон на пробных числах прямо в администрировании — до того, как методикой начнут пользоваться",
      "Доступ настраивается: выключен / только администраторам / всем сотрудникам",
      "Если подходящей методики нет, модель посчитает по своей формуле, но обязана предупредить об этом"] },
  { n: "01", t: "Единый вход и роли", img: "01_login", side: "right",
    lead: "Локальные логин и пароль. Самостоятельной регистрации нет — учётные записи заводит администратор.",
    points: ["Сессии в httpOnly-cookie, TTL 12 часов, ограничение попыток входа по IP",
      "Две роли: администратор и пользователь", "Первый администратор создаётся CLI-командой",
      "На странице входа — памятка: гостайну не вводить, ответы модели проверять специалистом"] },
  { n: "02", t: "Чат с локальной моделью", img: "02_chat_answer", side: "left",
    lead: "Потоковый ответ из llama.cpp. История чатов приватна для каждого пользователя.",
    points: ["Режим «Размышления» — reasoning в сворачиваемом блоке, в следующий запрос не уходит; тумблер thinking-режима у каждого чата свой",
      "Markdown с таблицами и кодом, формулы LaTeX (KaTeX, локально), переключатель «рендер ↔ исходник», копирование ответа",
      "Живые индикаторы: фаза («размышляет» / «печатает»), курсор за последним символом, выписка последней мысли в заголовке «Размышлений»",
      "Статистика под ответом: токены, скорость и время — из счётчиков сервера; внизу экрана — объём всей переписки в % от контекста, чтобы видеть, когда пора начинать новый чат",
      "Действия под каждым сообщением: продолжить, перегенерировать (у ответа), редактировать, удалить (у запроса); sticky-скролл не мешает листать историю во время генерации"] },
  { n: "03", t: "Модель работает с заметками и календарём", img: "03_tools", side: "right",
    lead: "Tool calling на естественном языке: «создай заметку», «что у меня на неделе», «перенеси совещание на четверг».",
    points: ["Серверный агентный цикл, до 6 шагов за ответ",
      "Инструменты исполняются от имени пользователя — чужие личные данные недоступны",
      "Расчёты модель выполняет тем же механизмом: ей выдаётся инструмент вычислений и справочник методик",
      "Плашки «создана заметка …» по факту выполнения",
      "Подтверждения: удаление, перезапись текста заметки, смена области (личная ↔ общая) и любая правка события — в плашке написано, что именно изменится"] },
  { n: "04", t: "Фильтр персональных данных", img: "04_pii", side: "left", accent: true,
    lead: "Персональные данные маскируются ДО отправки в модель и ДО записи в историю чатов.",
    points: ["Регулярные выражения: телефоны, e-mail, СНИЛС, ИНН, паспорт, карты (проверка по Луну), счета",
      "ФИО — по правилам русского языка (отчества, инициалы, словарь имён), без внешних NER-моделей и без сети",
      "Белый список для нормативки — чтобы не маскировать подписантов документов",
      "В модель уходит уже очищенный текст; исходные значения в неё не попадают"] },
  { n: "07", t: "Отделы и бюро", img: "13_groups", side: "right",
    lead: "Одна система на предприятие — но общие записи не смешиваются между подразделениями.",
    points: ["Администратор заводит отделы и приписывает к ним сотрудников",
      "Общая заметка бюро нормирования не видна механосборочному цеху",
      "Сотрудник без отдела видит все общие записи — это роль методолога или руководителя",
      "Правило видимости одно на весь проект: и в интерфейсе, и в инструментах модели — обойти нельзя",
      "При удалении отдела его записи не пропадают, а становятся общими для всех"] },
  { n: "05", t: "Заметки", img: "05_notes", side: "right",
    lead: "Личные и общие заметки. Поиск по подстроке и тэгам, редактор с предпросмотром markdown.",
    points: ["Полный CRUD; тот же REST API используют инструменты модели",
      "В общих заметках фиксируется «изменено: кем и когда»",
      "Экспорт заметки в PDF со всеми формулами (KaTeX) — печать браузера, офлайн"] },
  { n: "06", t: "Календарь", img: "06_calendar", side: "left",
    lead: "Личные и общие события. Вид «Месяц» и «Список ближайших», создание и перенос в модальном окне.",
    points: ["Часовой пояс Europe/Moscow, хранение в ISO 8601",
      "Общие события визуально помечены", "Тот же REST API используют инструменты модели"] },
  { n: "07", t: "Документы в чат", img: "07_documents", side: "right",
    lead: "txt, md, csv, docx, xlsx, pdf, png, jpg — до 15 МБ. Разбор на сервере, файлы не хранятся.",
    points: ["Текст извлекается на бэкенде (таблицы csv/xlsx → markdown)",
      "PDF: тумблер в чате «как картинку» (все страницы через mmproj — модель видит вёрстку, чертежи, формулы) или «как текст» (дёшево)",
      "Картинки и PDF-сканы распознаёт сама мультимодальная модель (mmproj)",
      "В чате документ — сворачиваемый блок; извлечённый текст проходит фильтр ПДн, проверка MIME, защита от zip-бомб"] },
  { n: "08", t: "Режимы и свой системный промпт", img: "09_prompt", side: "left",
    lead: "Специализации (Механообработка, Сварка, Литьё…) наполняет администратор. Пользователь выбирает режим или пишет свой промпт.",
    points: ["Библиотека промптов под задачи отдела редактируется в администрировании",
      "Свой системный промпт действует в рамках конкретного чата и имеет приоритет над режимом"] },
  { n: "09", t: "Профиль и эргономика", img: "08_profile", side: "right",
    lead: "Смена пароля, размер шрифта в три ступени, кликабельные примеры на пустом экране.",
    points: ["Обратная связь 👍/👎 с комментарием у каждого ответа",
      "Выгрузка оценок в JSONL — задел под дообучение (LoRA)"] },
  { n: "10", t: "Администрирование", img: "10_admin_top", side: "left",
    lead: "Пользователи, специализации, примеры запросов, метрики и журнал аудита — в одном разделе для админа.",
    points: ["Метрики: число запросов, успех/неуспех, токены/с, срабатывания ПДн по типам — без содержания запросов",
      "Журнал аудита: входы (в т.ч. неудачные), действия с пользователями, вызовы инструментов, удаления",
      "Пользователи: создание, блокировка, сброс пароля, удаление с каскадом данных (с двойным подтверждением)",
      "Выгрузка обратной связи в JSONL"] },
  { n: "11", t: "Светлая и тёмная темы", img: "11_dark", side: "right",
    lead: "Переключатель в профиле: светлая, тёмная или «как в системе». Выбор хранится на пользователе и следует за аккаунтом на любом устройстве.",
    points: ["Тёмная палитра в фирменном стиле — графит и красный акцент",
      "Применяется до отрисовки, без «вспышки»; работает и на экране входа",
      "Формулы, таблицы, код, поля ввода — читаемы в обеих темах"] },
];

const perks = [
  ["Параллельные чаты", "Генерация не обрывается при переходе в другой чат; в списке — индикатор идущего инференса."],
  ["Параллельная работа", "Сколько запросов считается разом, решает сама llama.cpp (-np). Приложение это число не дублирует — менять железо можно без правок в нём."],
  ["Продолжение ответа", "Кнопка «Продолжить» дописывает оборванный длинный ответ в то же сообщение."],
  ["Скорость как в llama.cpp", "Токены/с берутся из счётчиков сервера (usage / timings), а не оцениваются приблизительно."],
  ["Расход контекста", "Внизу экрана — сколько токенов занимает переписка и какая это доля контекста; красным, когда пора начать новый чат."],
  ["Ответ не теряется", "Если модель закончила ход без текста, интерфейс объясняет причину, а «Продолжить» дописывает ответ по уже собранным данным."],
  ["Формулы LaTeX", "KaTeX локально (без CDN): $…$ и $$…$$ в ответах рендерятся, включая кириллические индексы."],
  ["Тумблеры на чат", "«Заметки/Календарь», «Расчёты» и «Размышления» — своё состояние у каждого чата, переживает перезагрузку."],
  ["Приватность истории", "Шифрование БД на диске (SQLCipher) и автоочистка сообщений старше N дней — по флагам в .env."],
  ["Тёмная тема", "Светлая / тёмная / как в системе; выбор на аккаунте, без «вспышки» при загрузке."],
  ["Время ответа", "Под ответом — сколько заняла генерация от «Отправить» до конца; живой таймер во время генерации."],
  ["Заметка в PDF", "Любую заметку — в PDF со всеми формулами, одной кнопкой, офлайн."],
  ["Честность модели", "Системный промпт: не выдумывать факты и термины, при незнании — прямо говорить."],
];

const docBlocks = [
  ["Архитектура", `<p>Один процесс Python (FastAPI + uvicorn) раздаёт статику и проксирует запросы. Фронтенд — vanilla ES-модули без этапа сборки. Хранилище — файл SQLite (WAL). Обращения наружу — только к модели и базе знаний.</p>
  <pre class="wire">[Браузер] ──HTTP──▶ [FastAPI :8001]
                       ├─ /api/chats ──SSE──▶ llama.cpp :8000 (/v1/chat/completions)
                       ├─ /api/rag   ───────▶ LightRAG (сейчас отключён)
                       ├─ инструменты: заметки, календарь, расчёты
                       ├─ SQLite (users, groups, chats, notes, events, calc_methods, …)
                       └─ статика (index.html, css, js, шрифты)</pre>`],
  ["Стек и зависимости", `<ul>
    <li><b>Backend:</b> Python 3.10+, FastAPI, uvicorn, httpx (SSE), aiosqlite, bcrypt</li>
    <li><b>Документы:</b> python-docx, openpyxl, pypdf, Pillow, pypdfium2 (растеризация PDF — без системных пакетов)</li>
    <li><b>Фильтр ПДн:</b> регулярные выражения и правила для ФИО в <code>app/pii.py</code> — без внешних NER-моделей</li>
    <li><b>Frontend:</b> статические HTML/CSS/JS без сборки; локальные шрифты и вендоры</li>
    <li>Минимум зависимостей — код обозрим для аудита ИБ</li></ul>`],
  ["Схема данных (SQLite)", `<div class="tw"><table>
    <tr><th>Таблица</th><th>Назначение</th></tr>
    <tr><td>users, sessions</td><td>учётные записи, роли, активные сессии</td></tr>
    <tr><td>groups</td><td>отделы и бюро: кто какие общие записи видит</td></tr>
    <tr><td>chats, messages</td><td>чаты и история (content, reasoning, вложения, статистика)</td></tr>
    <tr><td>notes, events</td><td>заметки и события — личные / общие</td></tr>
    <tr><td>specializations, chat_examples</td><td>режимы чата и примеры запросов</td></tr>
    <tr><td>calc_methods</td><td>справочник методик расчёта: параметры и шаги формул</td></tr>
    <tr><td>app_settings</td><td>настройки на лету — например, кому доступны расчёты</td></tr>
    <tr><td>feedback</td><td>оценки ответов (задел под датасет)</td></tr>
    <tr><td>audit_log</td><td>факты действий: кто, что, когда — без содержания</td></tr>
  </table></div>`],
  ["Производительность", `<p>Генерация идёт на процессоре, без видеокарты: модель MoE — из 35 млрд параметров активны 3 млрд. Сегодня это <b>около 20 токенов в секунду</b>.</p>
  <ul>
    <li>Расчёт по методике — ответ около минуты</li>
    <li>Развёрнутая справка по нормативу — две-три минуты</li>
    <li>Два слота (<code>-np 2</code>): два сотрудника считаются одновременно; очередь при большем числе держит сама llama.cpp</li>
    <li>Скорость измеряет сам сервер и показывает под каждым ответом — оценивать «на глаз» не нужно</li>
  </ul>
  <p>Перенос генерации на видеоускоритель — самое дешёвое ускорение из возможных: <b>ни строки в приложении менять не нужно</b>, это параметр запуска модели. Ответ на расчёт станет считаться секунды вместо минуты, а одновременных пользователей поместится больше.</p>`],
  ["Безопасность (ИБ)", `<ul>
    <li>Никакого исходящего трафика, кроме адресов модели и базы знаний</li>
    <li>Обязательная аутентификация; параметризованные SQL-запросы</li>
    <li>Экранирование HTML при рендере markdown (защита от XSS)</li>
    <li>CSRF: SameSite=Lax + проверка Origin на мутациях; заголовки X-Frame-Options=DENY, CSP default-src 'self'</li>
    <li>Фильтр ПДн; в аудит и логи содержание запросов и пароли не пишутся</li>
    <li>В целевой схеме модель закрывается на localhost — обратиться к ней в обход интерфейса, без пароля и аудита, невозможно</li>
    <li>Действия модели, которые меняют данные, требуют подтверждения человека; из размышлений модели могут воскрешаться только читающие инструменты</li>
    <li>Приватность истории: чаты изолированы по владельцу (у админа нет доступа к чужим); опционально — шифрование БД на диске (SQLCipher, ключ в .env) и автоочистка сообщений старше N дней</li></ul>`],
  ["Развёртывание", `<ul>
    <li>Онлайн и офлайн (air-gap): каталог <code>wheels/</code>, всё локально</li>
    <li>systemd-сервис (системный или пользовательский, без root), автозапуск</li>
    <li>Бэкап: снимок SQLite + .env; резервное копирование по расписанию</li>
    <li>Обновление: <code>git pull</code> и перезапуск службы; миграции базы применяются сами</li>
    <li>Пошаговые инструкции — в каталоге <code>docs/</code>; отдельный справочник описывает, что и в каком порядке уходит модели</li>
    <li>204 автоматических теста: права доступа, инструменты, расчёты, фильтр ПДн</li></ul>`],
  ["Приёмка", `<ul>
    <li>«Возьми методику из заметок и посчитай подачу на зуб» → модель находит заметку, считает инструментом и показывает ход расчёта</li>
    <li>Чужие личные заметки и события недоступны ни через UI, ни через API, ни через инструменты модели</li>
    <li>Общая заметка одного бюро не видна другому; сотрудник без отдела видит все общие записи</li>
    <li>Изменение события и публикация личной заметки на отдел — только после подтверждения в интерфейсе</li>
    <li>«Создай на завтра совещание и заметку с повесткой» → событие и заметка появляются без ручных действий</li>
    <li>«Что у меня на этой неделе?» → ответ по реальным данным календаря</li>
    <li>При выключенном интернете всё работает после офлайн-установки</li></ul>`],
];

const featureHtml = features.map((f, i) => `
  <section class="feature ${i % 2 ? "img-left" : ""} ${f.accent ? "is-accent" : ""}">
    <div class="feature-text">
      <div class="eyebrow"><span class="num">${String(i + 1).padStart(2, "0")}</span><span class="rule"></span></div>
      <h3>${f.t}</h3>
      <p class="lead">${f.lead}</p>
      <ul class="points">${f.points.map((p) => `<li>${p}</li>`).join("")}</ul>
    </div>
    <figure class="shot"><img src="${img(f.img)}" alt="${f.t}" loading="lazy"></figure>
  </section>`).join("");

const perksHtml = perks.map((p) => `
  <div class="perk"><span class="perk-mark"></span><div><h4>${p[0]}</h4><p>${p[1]}</p></div></div>`).join("");

const docHtml = docBlocks.map((d) => `
  <div class="doc-block"><h4>${d[0]}</h4>${d[1]}</div>`).join("");

const html = `<style>
@font-face{font-family:"Oswald";src:url(${font("oswald-cyrillic.woff2")}) format("woff2");font-weight:400 600;font-display:swap;unicode-range:U+0400-04FF,U+0300-036F}
@font-face{font-family:"Oswald";src:url(${font("oswald-latin.woff2")}) format("woff2");font-weight:400 600;font-display:swap}
@font-face{font-family:"PT Sans";src:url(${font("ptsans-400-cyrillic.woff2")}) format("woff2");font-weight:400;font-display:swap;unicode-range:U+0400-04FF,U+0300-036F}
@font-face{font-family:"PT Sans";src:url(${font("ptsans-700-cyrillic.woff2")}) format("woff2");font-weight:700;font-display:swap;unicode-range:U+0400-04FF,U+0300-036F}

:root{
  --accent:#BE1E2D; --accent-deep:#9E1826;
  --sea:#1E2A33; --sea-2:#162027;
  --ink:#1E2A33; --ink-soft:#5B6670;
  --bg:#EFF1F2; --surface:#FFFFFF; --line:#D9DDE0;
  --code-bg:#1E2A33; --code-ink:#E7EBEE;
}
@media (prefers-color-scheme:dark){:root{
  --accent:#E5384A; --accent-deep:#BE1E2D;
  --ink:#E7ECEF; --ink-soft:#93A0AB;
  --bg:#0E1419; --surface:#18222A; --line:#2A3742;
  --code-bg:#0A0F13; --code-ink:#DCE3E8;
}}
:root[data-theme="light"]{--accent:#BE1E2D;--accent-deep:#9E1826;--ink:#1E2A33;--ink-soft:#5B6670;--bg:#EFF1F2;--surface:#FFFFFF;--line:#D9DDE0;--code-bg:#1E2A33;--code-ink:#E7EBEE;}
:root[data-theme="dark"]{--accent:#E5384A;--accent-deep:#BE1E2D;--ink:#E7ECEF;--ink-soft:#93A0AB;--bg:#0E1419;--surface:#18222A;--line:#2A3742;--code-bg:#0A0F13;--code-ink:#DCE3E8;}

*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"PT Sans","Segoe UI",system-ui,sans-serif;line-height:1.6;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,.display,.eyebrow{font-family:"Oswald","PT Sans Narrow",sans-serif;text-transform:uppercase;letter-spacing:.06em;font-weight:600;text-wrap:balance}
img{max-width:100%;display:block}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px}

/* HERO */
.hero{background:var(--sea);color:#EEF1F3;position:relative;overflow:hidden;
  clip-path:polygon(0 0,100% 0,100% calc(100% - 46px),0 100%)}
.hero::before{content:"";position:absolute;inset:0;background:
  radial-gradient(120% 90% at 85% -10%,rgba(190,30,45,.32),transparent 55%),
  linear-gradient(180deg,rgba(255,255,255,.04),transparent 55%)}
.hero-inner{position:relative;max-width:1120px;margin:0 auto;padding:64px 24px 96px}
.brand{display:flex;align-items:center;gap:14px;margin-bottom:56px}
.brand .mark{width:44px;height:44px;background:var(--accent);display:grid;place-items:center;
  font-family:"Oswald";font-weight:600;font-size:19px;color:#fff;
  clip-path:polygon(0 0,100% 0,100% 74%,74% 100%,0 100%)}
.brand .bt{font-family:"Oswald";text-transform:uppercase}
.brand .bt b{font-size:17px;letter-spacing:.12em;display:block;line-height:1}
.brand .bt span{font-size:10px;letter-spacing:.22em;color:#9FB0BC}
.hero h1{font-size:clamp(38px,7vw,74px);line-height:.98;margin:0 0 18px;letter-spacing:.02em}
.hero h1 .em{color:var(--accent)}
.hero .sub{font-family:"PT Sans";text-transform:none;letter-spacing:0;font-size:clamp(16px,2.1vw,21px);
  color:#C4CFD6;max-width:60ch;margin:0}
.chips{display:flex;flex-wrap:wrap;gap:10px;margin-top:34px}
.chip{font-family:"Oswald";text-transform:uppercase;letter-spacing:.1em;font-size:12px;
  padding:7px 14px;border:1px solid rgba(255,255,255,.22);color:#D6DEE3;
  clip-path:polygon(0 0,100% 0,100% 70%,90% 100%,0 100%)}
.chip.red{background:var(--accent);border-color:var(--accent);color:#fff}

/* SECTION SHELL */
.band{padding:64px 0}
.sec-head{max-width:1120px;margin:0 auto;padding:0 24px}
.sec-head .kick{display:flex;align-items:center;gap:14px;color:var(--accent);
  font-family:"Oswald";text-transform:uppercase;letter-spacing:.14em;font-size:13px}
.sec-head .kick::after{content:"";height:2px;flex:1;background:var(--accent);opacity:.5}
.sec-head h2{font-size:clamp(26px,4vw,40px);margin:14px 0 0}
.sec-head p.intro{font-family:"PT Sans";text-transform:none;letter-spacing:0;color:var(--ink-soft);
  max-width:70ch;margin:14px 0 0;font-size:17px}

/* FEATURES */
.features{display:flex;flex-direction:column;gap:26px;margin-top:40px}
.feature{max-width:1120px;margin:0 auto;padding:0 24px;width:100%;
  display:grid;grid-template-columns:1fr 1.15fr;gap:44px;align-items:center}
.feature.img-left{grid-template-columns:1.15fr 1fr}
.feature.img-left .feature-text{order:2}
.feature.img-left .shot{order:1}
.eyebrow{display:flex;align-items:center;gap:14px;margin-bottom:10px}
.eyebrow .num{font-family:"Oswald";font-size:40px;line-height:1;color:var(--accent);font-weight:600}
.eyebrow .rule{height:2px;width:56px;background:var(--line)}
.feature h3{font-size:clamp(21px,2.6vw,28px);margin:0 0 12px;color:var(--ink)}
.feature .lead{font-size:18px;margin:0 0 16px;color:var(--ink)}
.points{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px}
.points li{position:relative;padding-left:20px;color:var(--ink-soft);font-size:15.5px}
.points li::before{content:"";position:absolute;left:0;top:9px;width:9px;height:9px;
  background:var(--accent);clip-path:polygon(0 0,100% 0,100% 60%,60% 100%,0 100%)}
.feature.is-accent .lead{color:var(--accent);font-weight:700}

.shot{margin:0;position:relative;border:1px solid var(--line);background:var(--surface);
  clip-path:polygon(0 0,100% 0,100% calc(100% - 18px),calc(100% - 18px) 100%,0 100%)}
.shot::before{content:"";position:absolute;left:0;top:0;width:5px;height:46px;background:var(--accent)}
.shot img{width:100%}

/* PERKS */
.perks-band{background:var(--sea);color:#E7ECEF;
  clip-path:polygon(0 40px,100% 0,100% 100%,0 100%)}
.perks-band .kick{color:#fff}
.perks-band .kick::after{background:rgba(255,255,255,.4)}
.perks-band h2{color:#fff}
.perks-band .intro{color:#B7C3CB}
.perks{max-width:1120px;margin:34px auto 0;padding:0 24px;
  display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.perk{display:flex;gap:14px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);
  padding:20px;clip-path:polygon(0 0,100% 0,100% calc(100% - 12px),calc(100% - 12px) 100%,0 100%)}
.perk-mark{flex:none;width:14px;height:14px;background:var(--accent);margin-top:5px;
  clip-path:polygon(0 0,100% 0,100% 60%,60% 100%,0 100%)}
.perk h4{margin:0 0 6px;font-size:15px;color:#fff;letter-spacing:.04em}
.perk p{margin:0;font-family:"PT Sans";text-transform:none;letter-spacing:0;color:#B7C3CB;font-size:14px}

/* INFRA placeholder */
.infra{max-width:1120px;margin:34px auto 0;padding:0 24px}
.infra-card{border:1px dashed var(--accent);background:var(--surface);padding:28px 30px;
  clip-path:polygon(0 0,100% 0,100% calc(100% - 16px),calc(100% - 16px) 100%,0 100%)}
.infra-card p{margin:0 0 10px;color:var(--ink-soft)}
.infra-card .tag{display:inline-block;font-family:"Oswald";text-transform:uppercase;letter-spacing:.1em;
  font-size:11px;color:var(--accent);border:1px solid var(--accent);padding:3px 9px;margin-bottom:12px}
.infra-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px 26px;margin-top:8px}
.infra-grid div{color:var(--ink-soft);font-size:14.5px;padding-left:18px;position:relative}
.infra-grid div::before{content:"—";position:absolute;left:0;color:var(--accent)}

/* DOC SHEET */
.doc{background:var(--surface);border-top:3px solid var(--accent);padding:64px 0}
.doc-grid{max-width:1120px;margin:34px auto 0;padding:0 24px;
  display:grid;grid-template-columns:repeat(2,1fr);gap:30px 44px}
.doc-block h4{font-size:16px;color:var(--accent);letter-spacing:.08em;margin:0 0 12px;
  padding-bottom:8px;border-bottom:1px solid var(--line)}
.doc-block p{margin:0 0 10px;font-size:14.5px;color:var(--ink-soft)}
.doc-block ul{margin:0;padding-left:18px;color:var(--ink-soft);font-size:14.5px}
.doc-block li{margin-bottom:6px}
.doc-block code{background:var(--bg);padding:1px 5px;font-size:13px;border:1px solid var(--line)}
pre.wire{background:var(--code-bg);color:var(--code-ink);padding:16px;overflow-x:auto;
  font-size:12.5px;line-height:1.5;font-family:Consolas,"Courier New",monospace;margin:0;text-transform:none;letter-spacing:0}
.tw{overflow-x:auto}
.doc-block table{border-collapse:collapse;width:100%;font-size:13.5px}
.doc-block th,.doc-block td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
.doc-block th{background:var(--bg);font-family:"Oswald";text-transform:uppercase;letter-spacing:.05em;font-size:12px}

/* FOOTER */
footer{background:var(--sea-2);color:#8FA0AB;text-align:center;padding:40px 24px;font-size:13px;
  clip-path:polygon(0 30px,100% 0,100% 100%,0 100%)}
footer b{color:#D6DEE3}

@media (max-width:820px){
  .feature,.feature.img-left{grid-template-columns:1fr}
  .feature.img-left .feature-text,.feature.img-left .shot{order:0}
  .perks{grid-template-columns:1fr 1fr}
  .doc-grid{grid-template-columns:1fr}
  .infra-grid{grid-template-columns:1fr}
}
@media (max-width:560px){.perks{grid-template-columns:1fr}}
@media (prefers-reduced-motion:no-preference){.shot img{transition:none}}

/* Печать / экспорт в PDF: не рвать блоки между страницами, убрать диагональные
   срезы у крупных полос (иначе клип попадает на разрыв страницы) */
@media print{
  html,body{background:#fff}
  .hero,.perks-band,footer{clip-path:none}
  .feature,.perk,.doc-block,.infra-card,.shot{break-inside:avoid;page-break-inside:avoid}
  .band{padding:34px 0}
}
</style>

<div class="hero">
  <div class="hero-inner">
    <div class="brand"><span class="mark">АВ</span><span class="bt"><b>ОСК</b><span>Адмиралтейские верфи</span></span></div>
    <h1>srv-ai <span class="em">·</span> рабочее место ИИ</h1>
    <p class="sub">Локальная языковая модель на рабочем месте нормировщика: методики хранятся в заметках, расчёты выполняет инструмент, ход вычислений виден и проверяем. Целиком в закрытом контуре предприятия.</p>
    <div class="chips">
      <span class="chip red">Точные расчёты</span>
      <span class="chip">Без интернета</span>
      <span class="chip">Отделы и роли</span>
      <span class="chip">Фильтр ПДн</span>
      <span class="chip">Полный аудит</span>
    </div>
  </div>
</div>

<div class="band">
  <div class="sec-head">
    <div class="kick">Зачем</div>
    <h2>Единое рабочее место отдела</h2>
    <p class="intro">Штатный веб-интерфейс llama.cpp не подходил по требованиям ИБ: нет аутентификации, разграничения пользователей и журналирования. Вместо него — собственный интерфейс, где сотрудники работают с моделью, а данные не покидают изолированный контур. Три инструмента по отдельности — просто удобства; вместе они закрывают рабочий цикл: методика лежит в общей заметке отдела, в чате её просят применить, а числа считает не модель «в уме», а инструмент — с показом каждого шага.</p>
  </div>
</div>

<div class="band" style="padding-top:0">
  <div class="features">${featureHtml}</div>
</div>

<div class="band perks-band">
  <div class="sec-head">
    <div class="kick">Эргономика</div>
    <h2>Мелочи, из которых складывается удобство</h2>
    <p class="intro">То, что делает ежедневную работу с моделью на CPU предсказуемой и приятной.</p>
  </div>
  <div class="perks">${perksHtml}</div>
</div>

<div class="band">
  <div class="sec-head">
    <div class="kick">Инфраструктура</div>
    <h2>llama.cpp + Qwen под капотом</h2>
    <p class="intro">Веб-интерфейс работает поверх развёрнутых на сервере сервисов. Сейчас это опытная эксплуатация: интерфейс и модель слушают сетевые адреса. В целевой схеме модель закрывается на localhost, и наружу остаётся единственная дверь — интерфейс.</p>
  </div>
  <div class="infra">
    <div class="infra-card">
      <span class="tag">Модель</span>
      <p><b>Qwen3.6-35B-A3B</b> (GGUF, квант UD-Q8_K_XL) на <b>llama.cpp</b> (CPU-сервер). Контекст 262 144 токена на два слота — по 131 072 на пользователя. OpenAI-совместимый API на порту <code>8000</code>: <code>--jinja</code> (шаблон чата с tool calling), <code>--mmproj</code> (vision: картинки и PDF-сканы), <code>--reasoning-format deepseek</code> (thinking-режим), <code>--no-webui</code> — штатный интерфейс отключён, работает только наш UI. Thinking отключается на лету через <code>chat_template_kwargs</code> — тумблер «Размышления» в чате. Тем же способом включается <code>preserve_thinking</code>: модель получает обратно своё размышление и не передумывает заново. Сэмплинг — по карточке модели: <code>temp 1.0</code>, <code>top-p 0.95</code>, <code>top-k 20</code>, <code>presence-penalty 1.5</code>.</p>
      <div class="infra-grid">
        <div><b>Сейчас:</b> UI — 0.0.0.0:8001, llama.cpp — :8000; оба слушают сеть, удобно для отладки</div>
        <div><b>Целевая схема:</b> модель на 127.0.0.1, наружу только UI за обратным прокси с HTTPS</div>
        <div>Формат вызова инструментов у Qwen3.6 — XML внутри <code>&lt;tool_call&gt;</code>; приложение разбирает три формы записи, включая случай, когда модель выписала вызов текстом</div>
        <div>Базa знаний (LightRAG + эмбеддинги bge-m3) реализована, но <b>сейчас отключена</b>: <code>RAG_ENABLED=false</code>. Включается одной строкой в .env, без правки кода</div>
        <div>Счётчики usage/timings сервера — честные токены/с, время ответа и % контекста (лимит LLM_CONTEXT_SIZE)</div>
        <div>systemd --user + linger: автозапуск после ребута без root</div>
      </div>
    </div>
  </div>
</div>

<div class="doc">
  <div class="sec-head">
    <div class="kick">Документация</div>
    <h2>Как это устроено</h2>
    <p class="intro">Без пафоса: архитектура, стек, данные, безопасность и развёртывание — коротко и по делу.</p>
  </div>
  <div class="doc-grid">${docHtml}</div>
</div>

<footer>
  <b>srv-ai webUI</b> — АО «Адмиралтейские верфи», ОСК &nbsp;·&nbsp; локальная LLM в закрытом контуре<br>
  Полные инструкции по развёртыванию и тестированию — в каталоге <b>docs/</b> репозитория.
</footer>`;

// Полноценный самодостаточный документ: DOCTYPE + charset + head/body.
// Без <meta charset="utf-8"> локальный браузер угадывает кодировку неверно —
// отсюда были «кракозябры» вместо русского при открытии файла с диска.
const doc = `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>srv-ai · Рабочее место ИИ — презентация</title>
</head>
<body>
${html}
</body>
</html>`;

fs.writeFileSync(OUT, doc);
console.log("written:", OUT, (Buffer.byteLength(html) / 1048576).toFixed(2), "MB");
