// Мини-рендерер markdown (§4 ТЗ). Безопасность: весь входной текст
// экранируется ДО разметки — HTML из ответов LLM и заметок не исполняется (§13).
// Поддержка: заголовки, жирный/курсив, списки, таблицы, цитаты, hr,
// `код`, ```блоки кода``` (с кнопкой «Копировать» — обработчик в chat.js).

export function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
}

function flushList(state, html) {
  if (state.list) {
    html.push(state.list === "ul" ? "</ul>" : "</ol>");
    state.list = null;
  }
}

function flushPara(state, html) {
  if (state.para.length) {
    html.push(`<p>${state.para.join("<br>")}</p>`);
    state.para = [];
  }
}

function tableRow(line, cellTag) {
  const cells = line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|");
  return "<tr>" + cells.map((c) => `<${cellTag}>${inline(c.trim())}</${cellTag}>`).join("") + "</tr>";
}

const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}.*\|.*$/;

// Возвращает HTML-строку; вход экранируется целиком до разбора.
export function renderMarkdown(source) {
  const lines = escapeHtml(source.replaceAll("\r\n", "\n")).split("\n");
  const html = [];
  const state = { list: null, para: [] };
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Блок кода
    const fence = line.match(/^```(\S*)\s*$/);
    if (fence) {
      flushPara(state, html);
      flushList(state, html);
      const code = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        code.push(lines[i]);
        i++;
      }
      i++; // закрывающий ```
      const lang = fence[1] ? ` data-lang="${fence[1]}"` : "";
      html.push(
        `<div class="code-block"><button type="button" class="code-copy">Копировать</button>` +
        `<pre><code${lang}>${code.join("\n")}</code></pre></div>`);
      continue;
    }

    // Таблица (строка с | и разделитель под ней)
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
      flushPara(state, html);
      flushList(state, html);
      const rows = [tableRow(line, "th")];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(tableRow(lines[i], "td"));
        i++;
      }
      html.push(
        `<div class="table-wrap"><button type="button" class="table-excel">` +
        `Копировать для Excel</button><table>${rows.join("")}</table></div>`);
      continue;
    }

    const trimmed = line.trim();

    if (trimmed === "") {
      flushPara(state, html);
      flushList(state, html);
      i++;
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushPara(state, html);
      flushList(state, html);
      const level = heading[1].length + 2; // h3..h6 — не конкурируем с заголовками UI
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
      flushPara(state, html);
      flushList(state, html);
      html.push("<hr>");
      i++;
      continue;
    }

    if (trimmed.startsWith("&gt;")) {
      flushPara(state, html);
      flushList(state, html);
      html.push(`<blockquote>${inline(trimmed.slice(4).trim())}</blockquote>`);
      i++;
      continue;
    }

    const ulItem = trimmed.match(/^[-*+]\s+(.*)$/);
    const olItem = trimmed.match(/^\d+[.)]\s+(.*)$/);
    if (ulItem || olItem) {
      flushPara(state, html);
      const tag = ulItem ? "ul" : "ol";
      if (state.list !== tag) {
        flushList(state, html);
        html.push(tag === "ul" ? "<ul>" : "<ol>");
        state.list = tag;
      }
      html.push(`<li>${inline((ulItem || olItem)[1])}</li>`);
      i++;
      continue;
    }

    flushList(state, html);
    state.para.push(inline(trimmed));
    i++;
  }

  flushPara(state, html);
  flushList(state, html);
  return html.join("\n");
}
