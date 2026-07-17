// Рендер markdown (§4 ТЗ): marked (GFM — вложенные списки, чекбоксы,
// зачёркивание, ссылки, таблицы) + DOMPurify (санитайзер — HTML из ответов
// LLM и заметок не исполняется, §13). Обе библиотеки локальные (air-gap, §12).
// Формулы LaTeX извлекаются ДО парсера и рендерятся KaTeX'ом в самом конце.
import { Marked } from "/static/vendor/marked/marked.esm.js";
import DOMPurify from "/static/vendor/dompurify/purify.es.mjs";

export function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// --- Формулы LaTeX (KaTeX) ---
// Плейсхолдеры — символы Private Use Area: проходят через marked/DOMPurify
// нетронутыми (U+0000 CommonMark заменяет на U+FFFD, поэтому не он).

const MATH_PLACEHOLDER = /M(\d+)/g;

function extractMath(source, store) {
  const ph = (tex, display) => {
    store.push({ tex, display });
    return `M${store.length - 1}`;
  };
  // чётные индексы — вне fence-блоков, нечётные — внутри (не трогаем)
  return source.split(/(```[\s\S]*?(?:```|$))/).map((part, idx) => {
    if (idx % 2 === 1) return part;
    return part
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => ph(tex.trim(), true))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => ph(tex.trim(), true))
      .replace(/\\\((.+?)\\\)/g, (_, tex) => ph(tex.trim(), false))
      // инлайн $...$: без пробелов у краёв, без переносов, после — не цифра
      .replace(/(^|[^\\$\w])\$([^\s$](?:[^$\n]*[^\s$\\])?)\$(?![\d$])/g,
               (_, before, tex) => before + ph(tex, false));
  }).join("");
}

function renderMathPlaceholders(html, store) {
  return html.replace(MATH_PLACEHOLDER, (_, n) => {
    const item = store[Number(n)];
    if (!item) return "";
    const { tex, display } = item;
    if (typeof katex !== "undefined") {
      try {
        return katex.renderToString(tex, {
          displayMode: display,
          throwOnError: false,
          trust: false,       // \href и прочие «доверенные» команды запрещены
          maxExpand: 1000,    // защита от макро-бомб
        });
      } catch { /* падаем в текстовый фолбэк */ }
    }
    return escapeHtml(display ? `$$${tex}$$` : `$${tex}$`);
  });
}

// --- marked: GFM + подсветка ==текста== (как в Obsidian) ---

const highlightExtension = {
  name: "highlight",
  level: "inline",
  start(src) { return src.indexOf("=="); },
  tokenizer(src) {
    const m = /^==([^=\n](?:[^=\n]|=(?!=))*)==/.exec(src);
    if (m) {
      return { type: "highlight", raw: m[0], text: m[1],
               tokens: this.lexer.inlineTokens(m[1]) };
    }
    return undefined;
  },
  renderer(token) { return `<mark>${this.parser.parseInline(token.tokens)}</mark>`; },
};

const parser = new Marked({
  gfm: true,
  breaks: true,  // одиночный перенос строки → <br>, как в чатах и Obsidian
  walkTokens(token) {
    // h1/h2 ответа не конкурируют с заголовками UI: сдвигаем в h3..h6
    if (token.type === "heading") token.depth = Math.min(token.depth + 2, 6);
  },
});
parser.use({ extensions: [highlightExtension] });

// --- DOMPurify: белый список тегов/атрибутов ---

const SANITIZE_CONFIG = {
  ALLOWED_TAGS: ["p", "br", "hr", "strong", "b", "em", "i", "del", "s", "u",
    "code", "pre", "blockquote", "ul", "ol", "li", "input",
    "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td",
    "a", "img", "sup", "sub", "mark", "details", "summary"],
  ALLOWED_ATTR: ["href", "title", "class", "type", "checked", "disabled",
    "start", "align", "src", "alt", "colspan", "rowspan", "open"],
};

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  const tag = node.tagName;
  if (tag === "A") {
    // внешние ссылки — в новой вкладке и без утечки referrer
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  } else if (tag === "IMG") {
    // air-gap: только data-URI, внешние картинки не запрашиваем (и CSP запретит)
    const src = node.getAttribute("src") || "";
    if (!src.startsWith("data:image/")) node.remove();
  } else if (tag === "INPUT") {
    // единственный разрешённый input — чекбокс задачи, только для чтения
    if (node.getAttribute("type") !== "checkbox") { node.remove(); return; }
    node.setAttribute("disabled", "");
  }
  // классы из сырого HTML модели не пропускаем — только language-* у кода
  const cls = node.getAttribute && node.getAttribute("class");
  if (cls && !/^language-[\w+-]*$/.test(cls)) node.removeAttribute("class");
});

// Обёртка блоков кода: рамка + кнопка «Копировать» (обработчик в chat.js).
// Делается ПОСЛЕ санитайзера, поэтому кнопку нельзя подделать из текста модели.
function wrapCodeBlocks(html) {
  return html
    .replaceAll("<pre><code", '<div class="code-block">'
      + '<button type="button" class="code-copy">Копировать</button><pre><code')
    .replaceAll("</code></pre>", "</code></pre></div>");
}

// Таблицы — в скроллируемую обёртку (широкие не ломают страницу)
function wrapTables(html) {
  return html
    .replaceAll("<table>", '<div class="table-wrap"><table>')
    .replaceAll("</table>", "</table></div>");
}

// Возвращает безопасную HTML-строку.
export function renderMarkdown(source) {
  const mathStore = [];
  const prepared = extractMath(source.replaceAll("\r\n", "\n"), mathStore);
  const raw = parser.parse(prepared);
  const clean = DOMPurify.sanitize(raw, SANITIZE_CONFIG);
  return renderMathPlaceholders(wrapTables(wrapCodeBlocks(clean)), mathStore);
}
