// Общая основа обеих колод: палитра, мастера слайдов и типовые слайды.
//
// Колод две — полная и короткая на пять минут. Держать вёрстку в двух местах
// нельзя: подправишь отступ в одной, и на одном экране рядом окажутся два
// разных фирменных стиля. Поэтому оформление живёт здесь, а файлы сборки
// содержат только текст слайдов.

const pptxgen = require("pptxgenjs");

const SHOTS = __dirname + "/shots";
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

/** Новая колода со здешним оформлением и набором типовых слайдов. */
function deck() {
  const p = new pptxgen();
  p.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
  p.defineSlideMaster({ title: "LIGHT", background: { color: WHITE } });
  p.defineSlideMaster({ title: "DARK", background: { color: SEA } });



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

  return { p, shot, brandMark, eyebrow, heading, bullets, screenshot, feature, divider, cards };
}

module.exports = {
  deck, shot,
  SEA, CARD_D, ACCENT, ACCENT_HI, INK, INK_SOFT,
  WHITE, SOFT, LINE, LT, MUT, MUT2,
  HEAD, BODY, MONO, IMG_AR, IMG_AR_OLD,
};
