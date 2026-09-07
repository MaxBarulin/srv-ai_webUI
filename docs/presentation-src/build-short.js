// Короткая колода на пять минут: «что это за зверь и с чем его едят».
//
// Полная презентация отвечает на вопрос «как устроено» и идёт полчаса. Эта
// отвечает на вопрос «зачем оно предприятию» и укладывается в пять минут:
// восемь слайдов, по сорок секунд на каждый. Оформление общее с полной
// колодой — обе собираются из deck.js.
//
// Цифры согласованы с паспортом проекта ПС «Создание защищённого рабочего
// места специалиста с искусственным интеллектом в замкнутом контуре
// Общества» (01.10.2026 – 30.03.2027). Меняются цифры в паспорте — меняются
// и здесь, иначе на защите разойдутся.

const {
  deck, ACCENT, ACCENT_HI, SEA, CARD_D, INK, INK_SOFT,
  WHITE, SOFT, LINE, LT, MUT, MUT2, HEAD, BODY, MONO, IMG_AR,
} = require("./deck");

const OUT = __dirname + "/../presentation-short.pptx";
const { p, brandMark, eyebrow, heading, bullets, screenshot, cards } = deck();

// ═════════════════════════════  1. ТИТУЛ  ═════════════════════════════
{
  const s = p.addSlide({ masterName: "DARK" });
  s.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.14, fill: { color: ACCENT } });
  brandMark(s, 0.65, 0.6, true);
  s.addText("КОРОТКО · ПЯТЬ МИНУТ", { x: 0.65, y: 1.85, w: 8, h: 0.35, fontFace: HEAD,
    bold: true, fontSize: 13, color: ACCENT_HI, charSpacing: 4, margin: 0 });
  s.addText("РАБОЧЕЕ МЕСТО ИИ", { x: 0.62, y: 2.25, w: 12, h: 1.0, fontFace: HEAD,
    bold: true, fontSize: 50, color: LT, charSpacing: 1, align: "left", valign: "middle", margin: 0 });
  s.addText("Создание защищённого рабочего места специалиста с искусственным интеллектом в замкнутом контуре Общества",
    { x: 0.65, y: 3.4, w: 10.6, h: 0.9, fontFace: BODY, fontSize: 18, color: MUT2,
      valign: "top", margin: 0, lineSpacingMultiple: 1.15 });
  s.addText("Проект производственной системы · 01.10.2026 – 30.03.2027 · УТПП, УИТ",
    { x: 0.65, y: 4.4, w: 10.6, h: 0.4, fontFace: BODY, fontSize: 14, color: MUT, margin: 0 });
  const chips = ["Что это", "Как работает", "Почему не просто чат", "Что даёт"];
  let cx = 0.65;
  chips.forEach((c) => {
    const w = 0.42 + c.length * 0.115;
    s.addShape("rect", { x: cx, y: 5.3, w, h: 0.44, fill: { color: SEA }, line: { color: "3A4650", width: 1 } });
    s.addText(c.toUpperCase(), { x: cx, y: 5.3, w, h: 0.44, align: "center", valign: "middle",
      fontFace: HEAD, fontSize: 9, color: MUT2, charSpacing: 0.5, margin: 0 });
    cx += w + 0.16;
  });
  s.addText("АО «Адмиралтейские верфи» · ОСК   |   сентябрь 2026",
    { x: 0.65, y: 6.72, w: 12, h: 0.38, fontFace: BODY, fontSize: 12, color: MUT, margin: 0 });
  s.addNotes("~20 с. Короткая версия большой презентации. Три вопроса: что это, как этим пользуются, что предприятие с этого имеет.");
}

// ═════════════════════════  2. ЧТО ЭТО ЗА ЗВЕРЬ  ═════════════════════════
{
  const s = p.addSlide({ masterName: "DARK" });
  heading(s, "ЧТО ЭТО", "Три вещи, и всё станет понятно", true);
  cards(s, [
    ["Модель стоит у нас в серверной",
     "Языковая модель работает на нашем оборудовании. Ни один запрос не уходит в интернет: ни чертёж, ни норматив, ни фамилия. Интернет для работы не нужен вовсе — проверено при физически отключённой сети."],
    ["Это рабочее место, а не чат",
     "Вход по паролю, отделы и роли, общие заметки бюро, календарь, точные расчёты, работа с документами, журнал действий. Чат — только одно из окон, и за каждым действием стоит запись в журнале."],
    ["Модель — сменная деталь",
     "Приложение разговаривает с моделью на общепринятом языке. Придёт модель лучше — встанет на то же место: адрес и название меняются в настройках, приложение остаётся прежним."],
  ], 2.35, 3, 3.91, 3.0);
  s.addText("Штатный интерфейс языковой модели такого не умеет: там нет ни входа по паролю, ни разграничения людей, ни журнала.",
    { x: 0.6, y: 5.75, w: 12.13, h: 0.5, fontFace: BODY, italic: true, fontSize: 14,
      color: MUT, valign: "top", margin: 0 });
  s.addNotes("~40 с. Главное: модель у нас, вокруг неё рабочее место, и модель заменяема. Дальше — как этим пользуются.");
}

// ═══════════════════  3. С ЧЕМ ЕГО ЕДЯТ — РАБОЧИЙ ЦИКЛ  ═══════════════════
{
  const s = p.addSlide({ masterName: "LIGHT" });
  eyebrow(s, "01", "С чем его едят: один рабочий цикл");
  s.addText("Нормировщик один раз кладёт методику в общую заметку отдела. Дальше считает по ней весь отдел.",
    { x: 0.6, y: 1.45, w: 5.85, h: 0.7, fontFace: BODY, fontSize: 15.5, bold: true,
      color: ACCENT, valign: "top", margin: 0, lineSpacingMultiple: 1.05 });
  const steps = [
    ["ЗАМЕТКА", "Методика с формулами и допусками лежит в общей заметке бюро — одна на всех."],
    ["ЗАПРОС", "В чате обычной фразой: «возьми методику из заметок, фреза D=80, z=8 — проверь подачу на зуб»."],
    ["РАСЧЁТ", "Модель находит заметку, читает её и передаёт числа расчётному инструменту. Сама она не считает."],
    ["ОТВЕТ", "Таблица исходных данных, результат и ход расчёта: видно, из какой формулы получено каждое число."],
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
  screenshot(s, "12_calc", 6.62, 1.55, 6.15, IMG_AR);
  s.addText("Ответ модели: слева ход расчёта, ниже — исходные данные и результат",
    { x: 6.62, y: 4.85, w: 6.15, h: 0.5, fontFace: BODY, italic: true, fontSize: 11.5,
      color: INK_SOFT, align: "center", valign: "top", margin: 0 });
  s.addText("Так же ведутся заметки и календарь: «создай на завтра совещание и заметку с повесткой» — без переключения программ.",
    { x: 0.6, y: 6.72, w: 12.13, h: 0.42, fontFace: BODY, italic: true, fontSize: 12.5, color: INK_SOFT, margin: 0 });
  s.addNotes("~60 с. Ключевой слайд. Показать на кадре плашку с ходом расчёта: число не на совести модели, его можно защитить перед проверяющим.");
}

// ═══════════════  4. ПОЧЕМУ ЭТО НЕ ПРОСТО ЧАТ — ОБВЯЗКА  ═══════════════
{
  const s = p.addSlide({ masterName: "LIGHT" });
  heading(s, "УСТРОЙСТВО", "Что здесь главное — и что заменяемо");

  const box = (x, y, w, h, title, sub, fill, brd, titleColor) => {
    s.addShape("rect", { x, y, w, h, fill: { color: fill }, line: { color: brd, width: 1 } });
    s.addText(title, { x: x + 0.2, y: y + 0.22, w: w - 0.4, h: 0.36, fontFace: HEAD, bold: true,
      fontSize: 13, color: titleColor || INK, charSpacing: 1, align: "center", margin: 0 });
    s.addText(sub, { x: x + 0.2, y: y + 0.62, w: w - 0.4, h: h - 0.8, fontFace: BODY, fontSize: 11.5,
      color: INK_SOFT, align: "center", valign: "top", margin: 0, lineSpacingMultiple: 1.05 });
  };

  box(0.6, 2.35, 2.25, 1.5, "СОТРУДНИК", "браузер,\nвход по паролю", SOFT, LINE);
  s.addText("▶", { x: 2.9, y: 2.85, w: 0.5, h: 0.5, fontFace: HEAD, fontSize: 18, color: ACCENT, align: "center", valign: "middle", margin: 0 });

  // Центр — обвязка
  s.addShape("rect", { x: 3.45, y: 1.85, w: 6.3, h: 3.6, fill: { color: WHITE }, line: { color: ACCENT, width: 2 } });
  s.addText("ОБВЯЗКА", { x: 3.45, y: 2.02, w: 6.3, h: 0.36, fontFace: HEAD, bold: true,
    fontSize: 14, color: ACCENT, charSpacing: 2, align: "center", margin: 0 });
  s.addText("то, что мы построили и что остаётся навсегда",
    { x: 3.45, y: 2.36, w: 6.3, h: 0.3, fontFace: BODY, italic: true, fontSize: 11.5,
      color: INK_SOFT, align: "center", margin: 0 });
  const parts = [
    "Права и отделы", "Общие заметки бюро", "Точный расчёт",
    "Фильтр персональных данных", "Работа с документами", "Журнал действий",
  ];
  parts.forEach((t, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 3.68 + col * 2.97, y = 2.85 + row * 0.79;
    s.addShape("rect", { x, y, w: 2.84, h: 0.62, fill: { color: SOFT }, line: { color: LINE, width: 1 } });
    s.addText(t, { x, y, w: 2.84, h: 0.62, align: "center", valign: "middle",
      fontFace: BODY, fontSize: 12, color: INK, margin: 0 });
  });

  s.addText("▶", { x: 9.82, y: 2.85, w: 0.5, h: 0.5, fontFace: HEAD, fontSize: 18, color: ACCENT, align: "center", valign: "middle", margin: 0 });
  s.addShape("rect", { x: 10.4, y: 2.35, w: 2.33, h: 1.5, fill: { color: SOFT }, line: { color: INK_SOFT, width: 1, dashType: "dash" } });
  s.addText("МОДЕЛЬ", { x: 10.4, y: 2.57, w: 2.33, h: 0.36, fontFace: HEAD, bold: true,
    fontSize: 13, color: INK, charSpacing: 1, align: "center", margin: 0 });
  s.addText("сменная деталь", { x: 10.4, y: 2.95, w: 2.33, h: 0.4, fontFace: BODY, italic: true,
    fontSize: 11.5, color: ACCENT, align: "center", margin: 0 });
  s.addText("общепринятый протокол", { x: 10.4, y: 3.9, w: 2.33, h: 0.3, fontFace: MONO,
    fontSize: 9.5, color: INK_SOFT, align: "center", margin: 0 });

  s.addShape("rect", { x: 0.6, y: 5.75, w: 12.13, h: 1.05, fill: { color: SOFT }, line: { color: LINE, width: 1 } });
  s.addText([
    { text: "Работа была не над моделью, а над обвязкой. ", options: { bold: true, color: INK } },
    { text: "Модель — общедоступная и бесплатная, её через год сменит следующая. Права доступа, методики отдела, проверяемый расчёт, фильтр персональных данных и журнал останутся теми же — их менять не придётся.", options: { color: INK_SOFT } },
  ], { x: 0.9, y: 5.95, w: 11.5, h: 0.7, fontFace: BODY, fontSize: 13.5, valign: "middle", margin: 0, lineSpacingMultiple: 1.05 });
  s.addNotes("~60 с. Самый важный слайд. Модель — расходная деталь, обвязка — актив. Именно поэтому это не сайт вокруг чата.");
}

// ═════════════════  5. ЗАПАС: ЧТО БУДЕТ, КОГДА МОДЕЛЬ ЛУЧШЕ  ═════════════════
{
  const s = p.addSlide({ masterName: "LIGHT" });
  heading(s, "ЗАПАС", "Растёт модель — растёт и рабочее место");
  s.addShape("rect", { x: 0.6, y: 1.85, w: 5.85, h: 4.4, fill: { color: SOFT }, line: { color: LINE, width: 1 } });
  s.addText("СЕГОДНЯ", { x: 0.95, y: 2.1, w: 5.15, h: 0.35, fontFace: HEAD, bold: true,
    fontSize: 12, color: INK_SOFT, charSpacing: 2, margin: 0 });
  s.addText([{ text: "≈20", options: { fontSize: 50, bold: true, color: INK } },
             { text: "  токенов/с", options: { fontSize: 16, color: INK_SOFT } }],
    { x: 0.95, y: 2.45, w: 5.15, h: 1.0, fontFace: HEAD, valign: "middle", margin: 0 });
  bullets(s, [
    "Qwen3.6-35B-A3B: из 35 млрд параметров в работе одновременно 3 млрд — поэтому хватает процессора, без видеокарты",
    "Расчёт по методике — около минуты, развёрнутая справка — две-три",
    "Модель общедоступная и бесплатная, лицензионных платежей нет",
    "Контекст 131 тыс. токенов на пользователя — норматив помещается в запрос целиком",
  ], 0.95, 3.5, 5.15, { fs: 13, h: 2.7 });
  s.addShape("rect", { x: 6.9, y: 1.85, w: 5.83, h: 4.4, fill: { color: "FBEDEE" }, line: { color: ACCENT, width: 1 } });
  s.addText("ЗАВТРА", { x: 7.25, y: 2.1, w: 5.15, h: 0.35, fontFace: HEAD, bold: true,
    fontSize: 12, color: ACCENT, charSpacing: 2, margin: 0 });
  s.addText("ни строки кода", { x: 7.25, y: 2.4, w: 5.13, h: 0.7, fontFace: HEAD,
    bold: true, fontSize: 36, color: ACCENT, valign: "middle", margin: 0 });
  s.addText("менять не придётся", { x: 7.25, y: 3.06, w: 5.13, h: 0.35, fontFace: BODY,
    fontSize: 15, color: INK_SOFT, valign: "middle", margin: 0 });
  bullets(s, [
    "Новая модель ставится сменой адреса и названия в настройках — правка кода не нужна",
    "Видеоускоритель: ответ за секунды вместо минуты; это параметр запуска модели, не доработка",
    "345 автоматических тестов проверяют права, расчёты и фильтр ПДн — после смены модели поведение проверяется тем же прогоном",
    "Сменить сервер модели можно прямо из админки, без перезапуска — это уже работает",
  ], 7.25, 3.5, 5.13, { fs: 13, h: 2.7 });
  s.addText("Вложение — в обвязку, а она от смены модели не устаревает.",
    { x: 0.6, y: 6.45, w: 12.13, h: 0.42, fontFace: BODY, italic: true, fontSize: 13, color: INK_SOFT, margin: 0 });
  s.addNotes("~45 с. Мысль: улучшение модели достаётся нам целиком, платить за него переделкой не нужно. Осторожно с обещаниями: во сколько раз станет лучше — не измеряли.");
}

// ═════════════════════════  6. ЧТО УЖЕ ПРОВЕРЕНО  ═════════════════════════
{
  const s = p.addSlide({ masterName: "DARK" });
  heading(s, "ПРОВЕРЕНО", "Это уже работает, а не в планах", true);
  const checks = [
    "«Возьми методику из заметок и посчитай подачу на зуб» → модель находит заметку, считает инструментом и показывает ход расчёта",
    "Чужие личные заметки и события недоступны: ни через интерфейс, ни в обход его",
    "Общая заметка одного бюро не видна другому",
    "Изменение и удаление — только после подтверждения, в плашке написано, что именно меняется",
    "Персональные данные закрываются до отправки в модель",
    "При выключенном интернете всё работает: установка тоже офлайн",
  ];
  checks.forEach((c, i) => {
    const y = 2.15 + i * 0.8;
    s.addShape("rect", { x: 0.65, y: y + 0.06, w: 0.28, h: 0.28, fill: { color: ACCENT } });
    s.addText("✓", { x: 0.65, y: y + 0.06, w: 0.28, h: 0.28, align: "center", valign: "middle",
      fontFace: HEAD, bold: true, fontSize: 15, color: WHITE, margin: 0 });
    s.addText(c, { x: 1.12, y, w: 11.6, h: 0.72, fontFace: BODY, fontSize: 14,
      color: MUT2, valign: "middle", margin: 0, lineSpacingMultiple: 1.02 });
  });
  s.addNotes("~30 с. Не зачитывать все шесть — назвать два-три и сказать, что остальное на приёмке проверено тем же порядком.");
}

// ═══════════════════  7. ЦЕЛИ И ЭФФЕКТ ПО ПАСПОРТУ  ═══════════════════
{
  const s = p.addSlide({ masterName: "LIGHT" });
  heading(s, "ПАСПОРТ ПРОЕКТА", "Цели и экономический эффект");
  const head = ["Целевой показатель", "Сейчас", "Цель", "Улучшение"];
  const rows = [
    ["Средства искусственного интеллекта в замкнутом контуре, %", "0", "100", "100 %"],
    ["Затраты времени на работу с документацией и типовые документы, ч. в год", "3 644", "1 822", "50 %"],
    ["Расчёты по методикам с проверяемым ходом вычислений, %", "0", "100", "100 %"],
  ];
  const colX = [0.6, 7.5, 9.05, 10.6];
  const colW = [6.7, 1.4, 1.4, 2.13];
  head.forEach((h, i) => {
    s.addShape("rect", { x: colX[i], y: 1.85, w: colW[i], h: 0.5, fill: { color: SEA } });
    s.addText(h, { x: colX[i] + 0.15, y: 1.85, w: colW[i] - 0.3, h: 0.5, fontFace: HEAD, bold: true,
      fontSize: 11, color: WHITE, charSpacing: 1, valign: "middle", align: i ? "center" : "left", margin: 0 });
  });
  rows.forEach((r, ri) => {
    const y = 2.42 + ri * 0.82;
    r.forEach((v, i) => {
      s.addShape("rect", { x: colX[i], y, w: colW[i], h: 0.72,
        fill: { color: i === 3 ? "FBEDEE" : SOFT }, line: { color: LINE, width: 1 } });
      s.addText(v, { x: colX[i] + 0.15, y, w: colW[i] - 0.3, h: 0.72, fontFace: BODY,
        fontSize: i ? 14 : 12.5, bold: i === 3, color: i === 3 ? ACCENT : INK,
        valign: "middle", align: i ? "center" : "left", margin: 0, lineSpacingMultiple: 1.0 });
    });
  });
  const tile = (x, w, label, value, sub, accent, vfs) => {
    s.addShape("rect", { x, y: 5.15, w, h: 1.35, fill: { color: accent ? ACCENT : SOFT },
      line: { color: accent ? ACCENT : LINE, width: 1 } });
    s.addText(label, { x: x + 0.25, y: 5.32, w: w - 0.5, h: 0.3, fontFace: HEAD, bold: true,
      fontSize: 10.5, color: accent ? "F6D8DB" : INK_SOFT, charSpacing: 2, margin: 0 });
    s.addText(value, { x: x + 0.25, y: 5.6, w: w - 0.5, h: 0.5, fontFace: HEAD, bold: true,
      fontSize: vfs || 22, color: accent ? WHITE : INK, margin: 0 });
    s.addText(sub, { x: x + 0.25, y: 6.08, w: w - 0.5, h: 0.32, fontFace: BODY,
      fontSize: 11, color: accent ? "F6D8DB" : INK_SOFT, margin: 0 });
  };
  tile(0.6, 4.2, "ЭКОНОМИЧЕСКИЙ ЭФФЕКТ", "1 365,6 тыс. руб.", "в год, Формула № 15 методики ОСК", true);
  tile(5.0, 3.85, "ЗАТРАТЫ НА ПРОЕКТ", "0", "имеющиеся мощности, свободное ПО");
  tile(9.05, 3.68, "СРОКИ", "01.10.2026 – 30.03.2027", "УТПП, УИТ · 5 задач, 15 мероприятий", false, 17);
  s.addNotes("~45 с. Цифры взять ровно из паспорта проекта, иначе на защите разойдутся. Эффект посчитан по работам, не пересекающимся с проектом по нормированию, — двойного счёта нет.");
}

// ═════════════════════════  8. ЧТО ДАЛЬШЕ  ═════════════════════════
{
  const s = p.addSlide({ masterName: "DARK" });
  s.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.14, fill: { color: ACCENT } });
  brandMark(s, 0.65, 0.6, true);
  s.addText("ЧТО ДАЛЬШЕ", { x: 0.62, y: 1.7, w: 12, h: 0.9, fontFace: HEAD, bold: true,
    fontSize: 38, color: LT, charSpacing: 1, margin: 0 });
  const next = [
    ["Опытная эксплуатация в бюро нормирования", "Наполнить справочник методик реальными формулами и собрать замечания от нормировщиков."],
    ["Нормативная документация предприятия", "Загрузить действующие сборники и нормативы, чтобы выборки делались по ним, а не по памяти модели."],
    ["Ускоритель для модели", "Перенос генерации на видеокарту: ответы за секунды и больше одновременных пользователей."],
    ["Закрытие контура", "Модель доступна только через интерфейс, обращение в обход — без пароля и журнала — становится невозможным."],
  ];
  next.forEach((n, i) => {
    const y = 2.85 + i * 0.95;
    s.addShape("rect", { x: 0.65, y: y + 0.05, w: 0.3, h: 0.3, fill: { color: ACCENT } });
    s.addText(String(i + 1), { x: 0.65, y: y + 0.05, w: 0.3, h: 0.3, align: "center", valign: "middle",
      fontFace: HEAD, bold: true, fontSize: 14, color: WHITE, margin: 0 });
    s.addText(n[0], { x: 1.15, y, w: 11.5, h: 0.34, fontFace: HEAD, bold: true, fontSize: 15, color: WHITE, margin: 0 });
    s.addText(n[1], { x: 1.15, y: y + 0.36, w: 11.5, h: 0.5, fontFace: BODY, fontSize: 13, color: MUT2, valign: "top", margin: 0 });
  });
  s.addText("Подробности — в полной презентации и паспорте проекта.",
    { x: 0.65, y: 6.76, w: 12, h: 0.38, fontFace: BODY, fontSize: 12, color: MUT, margin: 0 });
  s.addNotes("~30 с. Закончить на том, что нужно от руководства: люди в опытную эксплуатацию, доступ к нормативной документации, видеоускоритель.");
}

p.writeFile({ fileName: OUT }).then((f) => console.log("written:", f));
