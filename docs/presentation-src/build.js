const pptxgen = require("pptxgenjs");
const SHOTS = __dirname + "/shots";
const OUT = __dirname + "/../presentation.pptx";
const shot = (f) => `${SHOTS}/${f}.png`;

// ── Палитра: графит + фирменный красный ──
const SEA = "1E2A33", CARD_D = "24313B";
const ACCENT = "BE1E2D", ACCENT_HI = "E5384A";
const INK = "1E2A33", INK_SOFT = "5B6670";
const WHITE = "FFFFFF", SOFT = "F4F6F7", LINE = "D9DDE0";
const LT = "EEF1F3", MUT = "9FB0BC", MUT2 = "B7C3CB";
const HEAD = "Arial", BODY = "Calibri", MONO = "Consolas";
const IMG_AR = 1960 / 1000;      // новые кадры
const IMG_AR_OLD = 1960 / 1232;  // кадры прошлой съёмки

const p = new pptxgen();
p.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
p.defineSlideMaster({ title: "LIGHT", background: { color: WHITE } });
p.defineSlideMaster({ title: "DARK", background: { color: SEA } });

// ────────────────────────────  helpers  ────────────────────────────

function brandMark(s, x, y, dark) {
  s.addShape("rect", { x, y, w: 0.42, h: 0.42, fill: { color: ACCENT } });
  s.addText("АВ", { x, y, w: 0.42, h: 0.42, align: "center", valign: "middle",
    fontFace: HEAD, bold: true, fontSize: 15, color: WHITE });
  s.addText([
    { text: "ОСК", options: { fontFace: HEAD, bold: true, fontSize: 12, color: dark ? WHITE : INK, charSpacing: 2, breakLine: true } },
    { text: "АДМИРАЛТЕЙСКИЕ ВЕРФИ", options: { fontFace: HEAD, fontSize: 7.5, color: dark ? MUT : INK_SOFT, charSpacing: 3 } },
  ], { x: x + 0.52, y: y - 0.04, w: 3, h: 0.5, valign: "middle", margin: 0, lineSpacingMultiple: 0.9 });
}

// Заголовок раздела: крупный красный номер + название
function eyebrow(s, num, title, dark) {
  s.addText(num, { x: 0.6, y: 0.42, w: 1.15, h: 0.8, fontFace: HEAD, bold: true,
    fontSize: 44, color: dark ? ACCENT_HI : ACCENT, align: "left", valign: "middle", margin: 0 });
  s.addText(title.toUpperCase(), { x: 1.75, y: 0.42, w: 10.9, h: 0.8, fontFace: HEAD,
    bold: true, fontSize: 26, color: dark ? WHITE : INK, charSpacing: 1, align: "left", valign: "middle", margin: 0 });
}

// Заголовок без номера
function heading(s, kicker, title, dark) {
  s.addText(kicker, { x: 0.6, y: 0.5, w: 6, h: 0.3, fontFace: HEAD, bold: true,
    fontSize: 13, color: dark ? ACCENT_HI : ACCENT, charSpacing: 3, margin: 0 });
  s.addText(title.toUpperCase(), { x: 0.6, y: 0.82, w: 12.1, h: 0.72, fontFace: HEAD,
    bold: true, fontSize: 28, color: dark ? WHITE : INK, charSpacing: 1, margin: 0 });
}

function bullets(s, items, x, y, w, opts = {}) {
  s.addText(items.map((t, i) => ({
    text: t, options: { bullet: { code: "25AA", indent: 16 }, breakLine: i < items.length - 1 },
  })), { x, y, w, h: opts.h || 3.6, fontFace: BODY, fontSize: opts.fs || 14.5,
    color: opts.color || INK_SOFT, align: "left", valign: "top", margin: 0,
    paraSpaceAfter: opts.gap || 9, lineSpacingMultiple: 1.0 });
}

function screenshot(s, file, x, y, w, ar) {
  const h = w / (ar || IMG_AR);
  s.addShape("rect", { x: x - 0.04, y: y - 0.04, w: w + 0.08, h: h + 0.08,
    fill: { color: WHITE }, line: { color: LINE, width: 1 },
    shadow: { type: "outer", color: "1E2A33", blur: 9, offset: 4, angle: 90, opacity: 0.28 } });
  s.addImage({ path: shot(file), x, y, w, h, sizing: { type: "contain", w, h } });
  return h;
}

// Слайд «текст слева — кадр справа»
function feature(num, title, lead, pts, file, ar, accentLead) {
  const s = p.addSlide({ masterName: "LIGHT" });
  eyebrow(s, num, title);
  s.addText(lead, { x: 0.6, y: 1.5, w: 5.75, h: 0.95, fontFace: BODY, fontSize: 15.5,
    bold: !!accentLead, color: accentLead ? ACCENT : INK, valign: "top", margin: 0, lineSpacingMultiple: 1.05 });
  bullets(s, pts, 0.6, 2.62, 5.75, { h: 4.3 });
  screenshot(s, file, 6.62, 1.62, 6.15, ar);
  return s;
}

// Разделитель блока
function divider(kicker, title, sub, items) {
  const s = p.addSlide({ masterName: "DARK" });
  s.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.14, fill: { color: ACCENT } });
  s.addText(kicker, { x: 0.65, y: 1.85, w: 8, h: 0.4, fontFace: HEAD, bold: true,
    fontSize: 14, color: ACCENT_HI, charSpacing: 4, margin: 0 });
  s.addText(title.toUpperCase(), { x: 0.62, y: 2.3, w: 12, h: 1.1, fontFace: HEAD, bold: true,
    fontSize: 42, color: LT, charSpacing: 1, valign: "middle", margin: 0 });
  s.addText(sub, { x: 0.65, y: 3.5, w: 9.4, h: 0.8, fontFace: BODY, fontSize: 17,
    color: MUT2, valign: "top", margin: 0, lineSpacingMultiple: 1.15 });
  items.forEach((t, i) => {
    const y = 4.55 + i * 0.5;
    s.addShape("rect", { x: 0.68, y: y + 0.1, w: 0.14, h: 0.14, fill: { color: ACCENT } });
    s.addText(t, { x: 1.05, y, w: 11.5, h: 0.42, fontFace: BODY, fontSize: 14,
      color: MUT2, valign: "middle", margin: 0 });
  });
  return s;
}

// Карточки-плитки на тёмном
function cards(s, list, y0, cols, cw, ch) {
  const gx = 0.19, gy = 0.24, x0 = 0.6;
  list.forEach((pk, i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const x = x0 + col * (cw + gx), y = y0 + row * (ch + gy);
    s.addShape("rect", { x, y, w: cw, h: ch, fill: { color: CARD_D }, line: { color: "34424D", width: 1 } });
    s.addShape("rect", { x: x + 0.28, y: y + 0.3, w: 0.16, h: 0.16, fill: { color: ACCENT } });
    s.addText(pk[0], { x: x + 0.28, y: y + 0.56, w: cw - 0.56, h: 0.55, fontFace: HEAD,
      bold: true, fontSize: 14, color: WHITE, valign: "top", margin: 0, lineSpacingMultiple: 0.95 });
    s.addText(pk[1], { x: x + 0.28, y: y + 1.12, w: cw - 0.5, h: ch - 1.25, fontFace: BODY,
      fontSize: 11.5, color: MUT2, valign: "top", margin: 0, lineSpacingMultiple: 1.03 });
  });
}

// ═════════════════════════════  ТИТУЛ  ═════════════════════════════
{
  const s = p.addSlide({ masterName: "DARK" });
  s.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.14, fill: { color: ACCENT } });
  brandMark(s, 0.65, 0.6, true);
  s.addText([
    { text: "SRV-AI ", options: { color: LT } },
    { text: "·", options: { color: ACCENT_HI } },
    { text: " РАБОЧЕЕ МЕСТО ИИ", options: { color: LT } },
  ], { x: 0.62, y: 1.9, w: 12, h: 1.4, fontFace: HEAD, bold: true, fontSize: 52,
    charSpacing: 1, align: "left", valign: "middle", margin: 0 });
  s.addText("Локальная языковая модель на рабочем месте нормировщика: методики хранятся в заметках, расчёты выполняет инструмент, ход вычислений виден и проверяем. Целиком в закрытом контуре предприятия.",
    { x: 0.65, y: 3.35, w: 10.2, h: 1.2, fontFace: BODY, fontSize: 17, color: MUT2,
      valign: "top", margin: 0, lineSpacingMultiple: 1.15 });
  const chips = ["Без интернета", "Точные расчёты", "Отделы и роли", "Фильтр ПДн", "Полный аудит"];
  let cx = 0.65;
  chips.forEach((c, i) => {
    const w = 0.42 + c.length * 0.108;
    const on = i === 1;
    s.addShape("rect", { x: cx, y: 4.9, w, h: 0.44, fill: on ? { color: ACCENT } : { color: SEA },
      line: { color: on ? ACCENT : "3A4650", width: 1 } });
    s.addText(c.toUpperCase(), { x: cx, y: 4.9, w, h: 0.44, align: "center", valign: "middle",
      fontFace: HEAD, fontSize: 9, color: on ? WHITE : MUT2, charSpacing: 0.5, margin: 0 });
    cx += w + 0.16;
  });
  s.addText("Презентация разделена на два блока: для руководства и для технических специалистов.",
    { x: 0.65, y: 5.75, w: 11, h: 0.4, fontFace: BODY, italic: true, fontSize: 13.5, color: MUT, margin: 0 });
  s.addText("АО «Адмиралтейские верфи» · ОСК   |   август 2026",
    { x: 0.65, y: 6.72, w: 12, h: 0.38, fontFace: BODY, fontSize: 12, color: MUT, align: "left", margin: 0 });
}

// ═══════════════════  БЛОК I — РАЗДЕЛИТЕЛЬ  ═══════════════════
divider("БЛОК I", "Для руководства",
  "Зачем это отделу, как меняется работа нормировщика и что предприятие получает уже сегодня.",
  ["Рабочий процесс: методика → запрос → расчёт", "Выигрыши и разграничение по отделам",
   "Скорость сегодня и что даст ускоритель", "Что уже проверено на приёмке"]);

// ─────────────  I.1  ЗАЧЕМ: БЫЛО / СТАЛО  ─────────────
{
  const s = p.addSlide({ masterName: "LIGHT" });
  heading(s, "ЗАЧЕМ", "От «спросил у модели» к рабочему инструменту");
  const col = (x, tag, tagColor, title, items, tint) => {
    s.addShape("rect", { x, y: 1.85, w: 5.85, h: 4.75, fill: { color: tint }, line: { color: LINE, width: 1 } });
    s.addText(tag, { x: x + 0.35, y: 2.1, w: 3, h: 0.35, fontFace: HEAD, bold: true, fontSize: 12, color: tagColor, charSpacing: 2, margin: 0 });
    s.addText(title, { x: x + 0.35, y: 2.45, w: 5.15, h: 0.7, fontFace: HEAD, bold: true, fontSize: 18, color: INK, margin: 0, lineSpacingMultiple: 1.0 });
    s.addText(items.map((t, i) => ({ text: t, options: { bullet: { code: "25AA", indent: 16 }, breakLine: i < items.length - 1 } })),
      { x: x + 0.35, y: 3.3, w: 5.15, h: 3.1, fontFace: BODY, fontSize: 14, color: INK_SOFT, valign: "top", margin: 0, paraSpaceAfter: 9 });
  };
  col(0.6, "БЫЛО", INK_SOFT, "Штатный веб-интерфейс llama.cpp",
    ["Нет входа по паролю и разграничения людей",
     "Нет журнала — кто что спрашивал, неизвестно",
     "Методики живут в личных файлах и в головах",
     "Числа модель считает «в уме» и ошибается",
     "Не отвечает требованиям информационной безопасности"], SOFT);
  col(6.9, "СТАЛО", ACCENT, "Рабочее место отдела",
    ["Вход по паролю, роли, разграничение по отделам",
     "Методики отдела — в общих заметках, одни для всех",
     "Расчёт выполняет инструмент по вашей формуле",
     "Виден ход вычислений: из чего получено каждое число",
     "Полный аудит действий, фильтр персональных данных"], "FBEDEE");
}

// ─────────────  I.2  РАБОЧИЙ ПРОЦЕСС (ключевой)  ─────────────
{
  const s = p.addSlide({ masterName: "LIGHT" });
  eyebrow(s, "01", "Как это складывается в работу");
  s.addText("Три инструмента по отдельности — просто удобства. Вместе они закрывают рабочий цикл нормировщика.",
    { x: 0.6, y: 1.45, w: 5.85, h: 0.7, fontFace: BODY, fontSize: 15.5, bold: true,
      color: ACCENT, valign: "top", margin: 0, lineSpacingMultiple: 1.05 });
  const steps = [
    ["ЗАМЕТКА", "Нормировщик один раз кладёт методику в общую заметку отдела — с формулами и допусками."],
    ["ЗАПРОС", "В чате: «возьми методику из заметок, фреза D=80, z=8 — проверь подачу на зуб»."],
    ["РАСЧЁТ", "Модель находит заметку, читает её и передаёт числа инструменту расчётов."],
    ["ОТВЕТ", "Таблица исходных данных, результат и ход расчёта — видно, из какой формулы получено число."],
  ];
  steps.forEach((st, i) => {
    const y = 2.35 + i * 1.06;
    s.addShape("rect", { x: 0.6, y, w: 0.66, h: 0.66, fill: { color: ACCENT } });
    s.addText(String(i + 1), { x: 0.6, y, w: 0.66, h: 0.66, align: "center", valign: "middle",
      fontFace: HEAD, bold: true, fontSize: 22, color: WHITE, margin: 0 });
    s.addText(st[0], { x: 1.45, y: y - 0.04, w: 4.9, h: 0.32, fontFace: HEAD, bold: true,
      fontSize: 12.5, color: INK, charSpacing: 2, margin: 0 });
    s.addText(st[1], { x: 1.45, y: y + 0.26, w: 4.9, h: 0.75, fontFace: BODY, fontSize: 12.5,
      color: INK_SOFT, valign: "top", margin: 0, lineSpacingMultiple: 1.02 });
  });
  screenshot(s, "12_calc", 6.62, 1.55, 6.15);
  s.addText("Ответ модели: слева плашка с ходом расчёта, ниже — таблица исходных данных и результат",
    { x: 6.62, y: 4.85, w: 6.15, h: 0.5, fontFace: BODY, italic: true, fontSize: 11.5,
      color: INK_SOFT, align: "center", valign: "top", margin: 0 });
}

// ─────────────  I.3  ЧТО ЭТО ДАЁТ  ─────────────
{
  const s = p.addSlide({ masterName: "DARK" });
  heading(s, "ВЫИГРЫШ", "Что предприятие получает", true);
  const wins = [
    ["Методика перестаёт быть личной", "Формулы отдела лежат в общей заметке. Уходит человек — методика остаётся."],
    ["Число не на совести модели", "Формулу задаёт нормировщик, считает инструмент. Модель только подставляет значения."],
    ["Расчёт можно защитить", "В ответе виден каждый шаг: формула, значение, единица. Есть что показать проверяющему."],
    ["Новый сотрудник с первого дня", "Работает по тем же методикам, что и весь отдел, без наставника за плечом."],
    ["Одно окно вместо трёх", "Заметки и календарь ведутся из чата обычной фразой — без переключения программ."],
    ["Данные не покидают контур", "Ни один запрос не уходит в интернет. Переписка каждого видна только ему."],
  ];
  cards(s, wins, 1.95, 3, 3.91, 2.18);
}

// ─────────────  I.4  ЧАТ (демо)  ─────────────
feature("02", "Чат с локальной моделью",
  "Потоковый ответ, как в привычных сервисах, — только модель стоит в серверной предприятия.",
  ["Размышления модели — в сворачиваемом блоке, видно ход мысли",
   "Markdown с таблицами и кодом, формулы LaTeX — как в нормативной документации",
   "Под ответом: токены, скорость, время; внизу — объём переписки в % контекста",
   "Действия: продолжить, перегенерировать, отредактировать вопрос, удалить"],
  "02_chat_answer", IMG_AR_OLD);

// ─────────────  I.5  ЗАМЕТКИ И КАЛЕНДАРЬ  ─────────────
{
  const s = p.addSlide({ masterName: "LIGHT" });
  eyebrow(s, "03", "Заметки и календарь");
  s.addText("Личные и общие записи отдела. Модель ведёт их по обычной фразе: «создай заметку с методикой», «что у меня на неделе», «перенеси совещание на четверг». Изменение и удаление — только по подтверждению, и в плашке написано, что именно меняется.",
    { x: 0.6, y: 1.48, w: 12.1, h: 0.85, fontFace: BODY, fontSize: 14.5, color: INK, valign: "top", margin: 0, lineSpacingMultiple: 1.05 });
  const capH = screenshot(s, "05_notes", 0.6, 2.5, 5.85, IMG_AR_OLD);
  screenshot(s, "06_calendar", 6.88, 2.5, 5.85, IMG_AR_OLD);
  s.addText("ЗАМЕТКИ · методики отдела, поиск по тэгам, экспорт в PDF",
    { x: 0.6, y: 2.6 + capH, w: 5.85, h: 0.35, fontFace: HEAD, fontSize: 10.5, color: INK_SOFT, charSpacing: 1, align: "center", margin: 0 });
  s.addText("КАЛЕНДАРЬ · общие события отдела помечены",
    { x: 6.88, y: 2.6 + capH, w: 5.85, h: 0.35, fontFace: HEAD, fontSize: 10.5, color: INK_SOFT, charSpacing: 1, align: "center", margin: 0 });
}

// ─────────────  I.6  ОТДЕЛЫ  ─────────────
feature("04", "Отделы и бюро",
  "Одна система на предприятие — но общие записи не смешиваются между подразделениями.",
  ["Администратор заводит отделы и приписывает к ним сотрудников",
   "Общая заметка бюро нормирования не видна механосборочному цеху",
   "Сотрудник без отдела видит все общие записи — это роль методолога или руководителя",
   "Правило действует и в интерфейсе, и в инструментах модели — обойти нельзя"],
  "13_groups", IMG_AR);

// ─────────────  I.7  ПДн  ─────────────
feature("05", "Персональные данные не уходят в модель",
  "Маскирование срабатывает ДО отправки в модель и ДО записи в историю чатов.",
  ["Телефоны, e-mail, СНИЛС, ИНН, паспорт, номера карт и счетов",
   "ФИО — по правилам русского языка, без внешних сервисов и без интернета",
   "Белый список для нормативки: подписанты документов не маскируются",
   "В модель уходит уже очищенный текст — исходные значения в неё не попадают"],
  "04_pii", IMG_AR_OLD, true);

// ─────────────  I.8  СКОРОСТЬ  ─────────────
{
  const s = p.addSlide({ masterName: "LIGHT" });
  heading(s, "ПРОИЗВОДИТЕЛЬНОСТЬ", "Скорость сегодня и запас на завтра");
  // Левая колонка — сегодня
  s.addShape("rect", { x: 0.6, y: 1.85, w: 5.85, h: 4.75, fill: { color: SOFT }, line: { color: LINE, width: 1 } });
  s.addText("СЕГОДНЯ · НА ПРОЦЕССОРЕ", { x: 0.95, y: 2.1, w: 5.15, h: 0.35, fontFace: HEAD, bold: true, fontSize: 12, color: INK_SOFT, charSpacing: 2, margin: 0 });
  s.addText([{ text: "≈20", options: { fontSize: 54, bold: true, color: INK } },
             { text: "  токенов/с", options: { fontSize: 17, color: INK_SOFT } }],
    { x: 0.95, y: 2.48, w: 5.15, h: 1.08, fontFace: HEAD, valign: "middle", margin: 0 });
  bullets(s, [
    "Расчёт по методике — ответ около минуты",
    "Развёрнутая справка по нормативу — две-три минуты",
    "Работает без видеокарты: модель MoE, из 35 млрд параметров активны 3 млрд",
    "Параллельные запросы разводит сама llama.cpp: число слотов задаётся её параметром -np",
  ], 0.95, 3.6, 5.15, { fs: 13.5, h: 2.8 });
  // Правая колонка — завтра
  s.addShape("rect", { x: 6.9, y: 1.85, w: 5.83, h: 4.75, fill: { color: "FBEDEE" }, line: { color: ACCENT, width: 1 } });
  s.addText("ЗАВТРА · С УСКОРИТЕЛЕМ", { x: 7.25, y: 2.1, w: 5.15, h: 0.35, fontFace: HEAD, bold: true, fontSize: 12, color: ACCENT, charSpacing: 2, margin: 0 });
  s.addText([{ text: "секунды", options: { fontSize: 44, bold: true, color: ACCENT } },
             { text: "  вместо минут", options: { fontSize: 15, color: INK_SOFT } }],
    { x: 7.25, y: 2.48, w: 5.13, h: 1.08, fontFace: HEAD, valign: "middle", margin: 0 });
  bullets(s, [
    "Перенос генерации на видеоускоритель — самое дешёвое ускорение из возможных",
    "Ни строки в приложении менять не нужно: это параметр запуска модели",
    "Ответ на расчёт — секунды вместо минуты; больше одновременных пользователей",
    "Возможен и запас по контексту: длинные нормативные документы целиком",
  ], 7.25, 3.6, 5.13, { fs: 13.5, h: 2.8 });
  s.addText("Скорость измеряется самим сервером и видна под каждым ответом — оценивать «на глаз» не нужно.",
    { x: 0.6, y: 6.75, w: 12.1, h: 0.4, fontFace: BODY, italic: true, fontSize: 12.5, color: INK_SOFT, margin: 0 });
}

// ─────────────  I.9  ПРИЁМКА  ─────────────
{
  const s = p.addSlide({ masterName: "DARK" });
  heading(s, "ПРОВЕРЕНО", "Что уже работает на приёмке", true);
  const checks = [
    "«Возьми методику из заметок и посчитай подачу на зуб» → модель находит заметку, считает инструментом, показывает ход расчёта",
    "Чужие личные заметки и события недоступны ни через интерфейс, ни через API, ни через инструменты модели",
    "Общая заметка одного бюро не видна другому; сотрудник без отдела видит все общие записи",
    "«Создай на завтра совещание и заметку с повесткой» → событие и заметка появляются без ручных действий",
    "Изменение события и публикация личной заметки на отдел — только после подтверждения в интерфейсе",
    "При выключенном интернете всё работает после офлайн-установки",
  ];
  checks.forEach((c, i) => {
    const y = 1.95 + i * 0.83;
    s.addShape("rect", { x: 0.65, y: y + 0.06, w: 0.28, h: 0.28, fill: { color: ACCENT } });
    s.addText("✓", { x: 0.65, y: y + 0.06, w: 0.28, h: 0.28, align: "center", valign: "middle",
      fontFace: HEAD, bold: true, fontSize: 15, color: WHITE, margin: 0 });
    s.addText(c, { x: 1.12, y, w: 11.6, h: 0.75, fontFace: BODY, fontSize: 14,
      color: MUT2, valign: "middle", margin: 0, lineSpacingMultiple: 1.02 });
  });
}

// ═══════════════════  БЛОК II — РАЗДЕЛИТЕЛЬ  ═══════════════════
divider("БЛОК II", "Для технических специалистов",
  "Как всё устроено, на чём работает и что настраивается. Дальше — только техника.",
  ["Схема развёртывания и порты", "Модель, сэмплинг и вызов инструментов",
   "Инструмент расчётов и справочник методик", "Безопасность, эксплуатация, документация"]);

// ─────────────  II.1  СХЕМА ПРОЕКТА  ─────────────
{
  const s = p.addSlide({ masterName: "LIGHT" });
  heading(s, "РАЗВЁРТЫВАНИЕ", "Схема проекта: сейчас и целевая");
  const box = (x, y, w, h, title, lines, tint, brd) => {
    s.addShape("rect", { x, y, w, h, fill: { color: tint }, line: { color: brd, width: 1 } });
    s.addText(title, { x: x + 0.22, y: y + 0.16, w: w - 0.44, h: 0.32, fontFace: HEAD,
      bold: true, fontSize: 11.5, color: INK, charSpacing: 1, margin: 0 });
    s.addText(lines, { x: x + 0.22, y: y + 0.5, w: w - 0.44, h: h - 0.68, fontFace: MONO,
      fontSize: 10.5, color: INK_SOFT, valign: "top", margin: 0, lineSpacingMultiple: 1.15 });
  };
  // Сейчас
  s.addText("СЕЙЧАС · ОПЫТНАЯ ЭКСПЛУАТАЦИЯ", { x: 0.6, y: 1.8, w: 5.9, h: 0.3, fontFace: HEAD, bold: true, fontSize: 12, color: INK_SOFT, charSpacing: 2, margin: 0 });
  box(0.6, 2.2, 5.9, 1.02, "БРАУЗЕР СОТРУДНИКА", "по IP-адресу сервера\nв сети предприятия", SOFT, LINE);
  s.addText("▼   HTTP :8001", { x: 0.6, y: 3.28, w: 5.9, h: 0.3, fontFace: MONO, fontSize: 11, color: ACCENT, align: "center", margin: 0 });
  box(0.6, 3.62, 5.9, 1.02, "SRV-AI WEBUI  ·  0.0.0.0:8001", "FastAPI + uvicorn, SQLite\nвход по паролю, роли, аудит", WHITE, ACCENT);
  s.addText("▼   HTTP :8000", { x: 0.6, y: 4.7, w: 5.9, h: 0.3, fontFace: MONO, fontSize: 11, color: ACCENT, align: "center", margin: 0 });
  box(0.6, 5.04, 5.9, 1.02, "LLAMA.CPP  ·  :8000", "Qwen3.6-35B-A3B, CPU\nOpenAI-совместимый API", SOFT, LINE);
  s.addText("Оба порта пока слушают сетевой интерфейс — удобно для отладки.",
    { x: 0.6, y: 6.2, w: 5.9, h: 0.5, fontFace: BODY, italic: true, fontSize: 12, color: INK_SOFT, valign: "top", margin: 0 });
  // Целевая
  s.addText("ЦЕЛЕВАЯ · ПРОМЫШЛЕННАЯ", { x: 6.9, y: 1.8, w: 5.83, h: 0.3, fontFace: HEAD, bold: true, fontSize: 12, color: ACCENT, charSpacing: 2, margin: 0 });
  box(6.9, 2.2, 5.83, 1.02, "БРАУЗЕР СОТРУДНИКА", "по доменному имени,\nHTTPS через обратный прокси", SOFT, LINE);
  s.addText("▼   единственная дверь", { x: 6.9, y: 3.28, w: 5.83, h: 0.3, fontFace: MONO, fontSize: 11, color: ACCENT, align: "center", margin: 0 });
  box(6.9, 3.62, 5.83, 1.02, "SRV-AI WEBUI", "единственный порт наружу\nвсё остальное — за ним", WHITE, ACCENT);
  s.addText("▼   127.0.0.1 только локально", { x: 6.9, y: 4.7, w: 5.83, h: 0.3, fontFace: MONO, fontSize: 11, color: ACCENT, align: "center", margin: 0 });
  box(6.9, 5.04, 5.83, 1.02, "LLAMA.CPP + БАЗА ЗНАНИЙ", "127.0.0.1 — из сети недоступны\nобращение только через UI", SOFT, LINE);
  s.addText("Модель закрывается на localhost: обратиться к ней в обход интерфейса, без пароля и аудита, станет невозможно.",
    { x: 6.9, y: 6.2, w: 5.83, h: 0.6, fontFace: BODY, italic: true, fontSize: 12, color: ACCENT, valign: "top", margin: 0, lineSpacingMultiple: 1.05 });
}

// ─────────────  II.2  МОДЕЛЬ И ЕЁ НАСТРОЙКА  ─────────────
{
  const s = p.addSlide({ masterName: "DARK" });
  heading(s, "МОДЕЛЬ", "llama.cpp + Qwen3.6 под капотом", true);
  s.addShape("rect", { x: 0.6, y: 1.8, w: 12.13, h: 1.32, fill: { color: CARD_D }, line: { color: ACCENT, width: 1, dashType: "dash" } });
  s.addText([
    { text: "Qwen3.6-35B-A3B", options: { bold: true, color: WHITE } },
    { text: "  ·  GGUF, квант UD-Q8_K_XL  ·  контекст 262 144 токена на два слота — по 131 072 на пользователя  ·  ", options: { color: MUT2 } },
    { text: "мультимодальная", options: { bold: true, color: WHITE } },
    { text: " (mmproj): читает чертежи и сканы. Работает на процессоре, без видеокарты.", options: { color: MUT2 } },
  ], { x: 0.85, y: 2.05, w: 11.6, h: 0.85, fontFace: BODY, fontSize: 13.5, valign: "middle", margin: 0, lineSpacingMultiple: 1.08 });
  const left = [
    "--jinja — шаблон чата берётся из самого GGUF; в нём же зашит формат вызова инструментов",
    "--reasoning-format deepseek — размышления приходят отдельным полем, а не внутри ответа",
    "--mmproj — зрение: чертежи, сканы, PDF как картинка",
    "--no-webui — штатный интерфейс llama.cpp выключен, работает только наш",
  ];
  const right = [
    "Сэмплинг — по карточке модели: temp 1.0, top-p 0.95, top-k 20, presence-penalty 1.5",
    "preserve_thinking — модель получает обратно своё размышление и не передумывает заново",
    "-np 2 — два слота: два сотрудника считаются одновременно",
    "Приложение разбирает вызов инструмента в трёх формах — страховка от срывов формата",
  ];
  const half = (arr, x) => s.addText(arr.map((t, i) => ({ text: t, options: { bullet: { code: "2014", indent: 18 }, breakLine: i < arr.length - 1 } })),
    { x, y: 3.45, w: 5.9, h: 3.6, fontFace: BODY, fontSize: 13, color: MUT2, valign: "top", margin: 0, paraSpaceAfter: 12 });
  half(left, 0.6);
  half(right, 6.83);
}

// ─────────────  II.3  ИНСТРУМЕНТ РАСЧЁТОВ  ─────────────
feature("06", "Инструмент расчётов изнутри",
  "Модель не считает сама: она передаёт формулу и числа, вычисляет приложение.",
  ["Вычислитель на разборе выражения — без eval, из песочницы не выйти",
   "Пределы на степень, длину и глубину формулы: «бомба» вида 9**9**9 отбивается",
   "Справочник методик: формулы задаёт администратор, модель только подставляет числа",
   "Вся цепочка — за один вызов: промежуточные числа не переписываются вручную",
   "Доступ настраивается: выключен / только администраторам / всем"],
  "14_methods", IMG_AR);

// ─────────────  II.4  БАЗА ЗНАНИЙ  ─────────────
{
  const s = p.addSlide({ masterName: "LIGHT" });
  heading(s, "БАЗА ЗНАНИЙ", "Векторный поиск: готов, сейчас отключён");
  s.addShape("rect", { x: 0.6, y: 1.8, w: 12.13, h: 0.85, fill: { color: "FBEDEE" }, line: { color: ACCENT, width: 1 } });
  s.addText([
    { text: "Статус: ", options: { bold: true, color: INK } },
    { text: "механизм реализован и протестирован, но в текущей конфигурации ", options: { color: INK_SOFT } },
    { text: "выключен", options: { bold: true, color: ACCENT } },
    { text: " (RAG_ENABLED=false). Включается одной строкой в настройке, без правки кода.", options: { color: INK_SOFT } },
  ], { x: 0.9, y: 1.95, w: 11.5, h: 0.6, fontFace: BODY, fontSize: 14, valign: "middle", margin: 0 });
  s.addText("КАК УСТРОЕНО", { x: 0.6, y: 2.95, w: 5.9, h: 0.3, fontFace: HEAD, bold: true, fontSize: 12.5, color: ACCENT, charSpacing: 2, margin: 0 });
  bullets(s, [
    "LightRAG + эмбеддинги bge-m3, оба на этом же сервере",
    "Тумблер «База знаний» в параметрах чата у каждого пользователя",
    "Найденный контекст подставляется к вопросу и показывается плашкой «Источники»",
    "Недоступность базы не роняет чат — обычный режим продолжает работать",
  ], 0.6, 3.35, 5.9, { fs: 13.5, h: 3.1 });
  s.addText("ЧТО ДАСТ ВКЛЮЧЕНИЕ", { x: 6.9, y: 2.95, w: 5.83, h: 0.3, fontFace: HEAD, bold: true, fontSize: 12.5, color: ACCENT, charSpacing: 2, margin: 0 });
  bullets(s, [
    "Ответы со ссылкой на нормативную базу предприятия, а не по общим знаниям модели",
    "Поиск по смыслу, а не по точному слову: найдёт нужный пункт СТО без цитаты",
    "Заметки останутся для методик отдела, база знаний — для нормативки предприятия",
    "Требует наполнения: документы надо загрузить и проиндексировать — это отдельная работа",
  ], 6.9, 3.35, 5.83, { fs: 13.5, h: 3.1 });
}

// ─────────────  II.5  БЕЗОПАСНОСТЬ  ─────────────
{
  const s = p.addSlide({ masterName: "DARK" });
  heading(s, "ИНФОРМАЦИОННАЯ БЕЗОПАСНОСТЬ", "Что заложено в конструкцию", true);
  const sec = [
    ["Закрытый контур", "Исходящего трафика нет: только адреса модели и базы знаний внутри сети."],
    ["Обязательный вход", "Самостоятельной регистрации нет, учётные записи заводит администратор."],
    ["Разграничение", "Чужие личные данные недоступны ни через API, ни через инструменты модели."],
    ["Аудит без содержания", "Пишется кто и что сделал; тексты запросов и пароли в журнал не попадают."],
    ["Защита интерфейса", "Экранирование HTML, CSRF, X-Frame-Options, CSP; параметризованные запросы."],
    ["Обозримый код", "Фронтенд без сборки, минимум зависимостей — код можно прочитать глазами."],
    ["Шифрование базы", "SQLCipher по флагу; автоочистка старых чатов по сроку хранения."],
    ["Подтверждение действий", "Удаление и правка через модель требуют явного согласия человека."],
  ];
  cards(s, sec, 1.9, 4, 2.93, 2.2);
}

// ─────────────  II.6  АДМИНИСТРИРОВАНИЕ  ─────────────
feature("07", "Администрирование",
  "Шесть вкладок: пользователи, отделы, чат, расчёты, аналитика, аудит.",
  ["Пользователи: создание, отдел, блокировка, сброс пароля, удаление с каскадом",
   "Чат: специализации (Механообработка, Сварка, Литьё…) и примеры запросов",
   "Расчёты: кому доступен инструмент и справочник методик предприятия",
   "Аналитика: запросы, успех/неуспех, токены/с, срабатывания фильтра ПДн по типам",
   "Аудит: входы, действия с пользователями, вызовы инструментов, удаления"],
  "10_admin_top", IMG_AR_OLD);

// ─────────────  II.7  ЭКСПЛУАТАЦИЯ И ДОКУМЕНТАЦИЯ  ─────────────
{
  const s = p.addSlide({ masterName: "LIGHT" });
  heading(s, "ЭКСПЛУАТАЦИЯ", "Стек, обновление, документация");
  s.addText("АРХИТЕКТУРА", { x: 0.6, y: 1.8, w: 5.9, h: 0.3, fontFace: HEAD, bold: true, fontSize: 12.5, color: ACCENT, charSpacing: 2, margin: 0 });
  s.addShape("rect", { x: 0.6, y: 2.18, w: 5.9, h: 1.95, fill: { color: SEA } });
  s.addText(
    "[Браузер] ──HTTP──▶ [FastAPI :8001]\n" +
    "        ├─ /api/chats ─SSE─▶ llama.cpp :8000\n" +
    "        ├─ /api/rag  ──────▶ LightRAG (выкл.)\n" +
    "        ├─ SQLite (люди, чаты, заметки, …)\n" +
    "        └─ статика: html, css, js, шрифты",
    { x: 0.78, y: 2.34, w: 5.6, h: 1.65, fontFace: MONO, fontSize: 10.5, color: LT, valign: "top", margin: 0, lineSpacingMultiple: 1.14 });
  s.addText("СТЕК", { x: 0.6, y: 4.32, w: 5.9, h: 0.3, fontFace: HEAD, bold: true, fontSize: 12.5, color: ACCENT, charSpacing: 2, margin: 0 });
  bullets(s, [
    "Python 3.10+, FastAPI, uvicorn, httpx, aiosqlite, bcrypt",
    "Документы: python-docx, openpyxl, pypdf, Pillow, pypdfium2",
    "Фронтенд — статические HTML/CSS/JS без сборки, для аудита ИБ",
    "Один процесс, одна база-файл: разворачивается без контейнеров",
  ], 0.6, 4.7, 5.9, { fs: 13, h: 2.3 });
  s.addText("ЭКСПЛУАТАЦИЯ", { x: 6.9, y: 1.8, w: 5.83, h: 0.3, fontFace: HEAD, bold: true, fontSize: 12.5, color: ACCENT, charSpacing: 2, margin: 0 });
  bullets(s, [
    "systemd --user + linger — автозапуск после перезагрузки, root не нужен",
    "Обновление: git pull и перезапуск службы; миграции базы идут сами",
    "Настройка — один файл .env: адреса моделей, лимиты, фильтр ПДн, шифрование",
    "Бэкап — копия одного файла базы (скрипт в deploy/)",
    "Документация в репозитории: развёртывание, эксплуатация, приватность",
    "Отдельный справочник — что и в каком порядке уходит модели",
    "204 автоматических теста: права доступа, инструменты, расчёты, ПДн",
  ], 6.9, 2.18, 5.83, { fs: 13, h: 4.6, gap: 8 });
}

// ═════════════════════════════  ФИНАЛ  ═════════════════════════════
{
  const s = p.addSlide({ masterName: "DARK" });
  s.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.14, fill: { color: ACCENT } });
  brandMark(s, 0.65, 0.6, true);
  s.addText("ЧТО ДАЛЬШЕ", { x: 0.62, y: 1.7, w: 12, h: 0.9, fontFace: HEAD, bold: true,
    fontSize: 38, color: LT, charSpacing: 1, margin: 0 });
  const next = [
    ["Опытная эксплуатация в бюро нормирования", "Наполнить справочник методик реальными формулами, собрать замечания от нормировщиков."],
    ["Наполнение базы знаний", "Загрузить нормативную документацию предприятия и включить векторный поиск."],
    ["Ускоритель для модели", "Перенос генерации на видеокарту — ответы за секунды и больше одновременных пользователей."],
    ["Закрытие контура", "Модель на localhost, интерфейс за обратным прокси с HTTPS и доменным именем."],
  ];
  next.forEach((n, i) => {
    const y = 2.85 + i * 0.95;
    s.addShape("rect", { x: 0.65, y: y + 0.05, w: 0.3, h: 0.3, fill: { color: ACCENT } });
    s.addText(String(i + 1), { x: 0.65, y: y + 0.05, w: 0.3, h: 0.3, align: "center", valign: "middle",
      fontFace: HEAD, bold: true, fontSize: 14, color: WHITE, margin: 0 });
    s.addText(n[0], { x: 1.15, y, w: 11.5, h: 0.34, fontFace: HEAD, bold: true, fontSize: 15, color: WHITE, margin: 0 });
    s.addText(n[1], { x: 1.15, y: y + 0.36, w: 11.5, h: 0.5, fontFace: BODY, fontSize: 13, color: MUT2, valign: "top", margin: 0 });
  });
  s.addText("srv-ai webUI · АО «Адмиралтейские верфи», ОСК   —   локальная LLM в закрытом контуре предприятия",
    { x: 0.65, y: 6.76, w: 12, h: 0.38, fontFace: BODY, fontSize: 12, color: MUT, margin: 0 });
}

p.writeFile({ fileName: OUT }).then((f) => console.log("written:", f));
