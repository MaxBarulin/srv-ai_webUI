"""Фильтр персональных данных (§13, реализация п. 4.5 Положения).

Двухуровневая предобработка без дополнительного LLM-инференса и без внешних
моделей:
  1. Регулярные выражения для структурированных ПДн (телефоны, e-mail, СНИЛС,
     ИНН, паспорт, карты с проверкой по Луну, счета) → типизированные плейсхолдеры.
  2. Имена (ФИО) — детерминированные правила на морфологии русских ФИО:
     отчества (уникальные окончания -ович/-овна/…), инициалы «Фамилия И.О.»,
     словарь имён рядом с фамилиеподобным словом. В отличие от статистического
     NER, «любое слово с большой буквы» именем НЕ считается — поэтому технические
     термины (марки сталей, ГОСТы, названия узлов, заголовки) не маскируются.

Маскирование выполняется ДО отправки в LLM и ДО записи в историю (§13).
Белый список (PII_WHITELIST_FILE) исключает термины/фамилии из маскирования.
Изображения фильтром не обрабатываются (известное исключение vision-пути).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import settings

# --- Регулярные выражения структурированных ПДн ---

# Телефон РФ: +7/8 и 10 цифр с любыми разделителями
_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# СНИЛС: 11 цифр в формате XXX-XXX-XXX YY
_SNILS = re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[\s\-]\d{2}(?!\d)")
# Паспорт РФ: серия 2+2 цифры + номер 6 цифр, с разделителями (иначе неотличим
# от 10-значного ИНН — контигуальные 10 цифр относим к ИНН)
_PASSPORT = re.compile(r"(?<!\d)\d{2}\s\d{2}\s\d{6}(?!\d)")
# Кандидат в номер карты/счёта: последовательности из 13-20 цифр (с разделителями)
_DIGITS_SEQ = re.compile(r"(?<!\d)(?:\d[\s\-]?){13,20}\d(?!\d)")
# ИНН: 10 (юр) или 12 (физ) цифр
_INN = re.compile(r"(?<!\d)\d{10}(?:\d{2})?(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class PIIResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@lru_cache(maxsize=1)
def _whitelist() -> set[str]:
    path = settings.pii_whitelist_file
    if not path:
        return set()
    p = Path(path)
    if not p.is_absolute():
        from app.config import BASE_DIR
        p = BASE_DIR / p
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {ln.strip().lower() for ln in lines if ln.strip() and not ln.startswith("#")}


# --- Имена (ФИО): детерминированные правила -------------------------------
# Опорные сигналы с высокой точностью, чтобы НЕ задевать технические термины:
#   • отчество с надёжным окончанием — почти уникальный признак имени;
#   • инициалы «Фамилия И.О.» / «И.О. Фамилия» — типовой шаблон документов;
#   • имя из словаря РЯДОМ с фамилиеподобным словом (одиночное имя-омоним, вроде
#     «Вера», «Роман», «Лев», не маскируется — нужен сосед-фамилия).

# Заглавное русское слово, в т.ч. двойное через дефис (Римский-Корсаков)
_CAP = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
_CAP_RE = re.compile(_CAP)

# Отчество с «сильным» окончанием — практически не встречается у обычных слов
_PATRO_STRONG = re.compile(r"^[А-ЯЁ][а-яё]+(?:ович|евич|овна|евна|ична|инична)$")

# Инициалы: «Фамилия И.О.» или «И.О. Фамилия»
_INITIALS = re.compile(rf"\b{_CAP}\s+[А-ЯЁ]\.[\s]?[А-ЯЁ]\.|"
                       rf"\b[А-ЯЁ]\.[\s]?[А-ЯЁ]\.\s+{_CAP}")

# Фамильные окончания — применяются ТОЛЬКО рядом с именем/отчеством
_SURNAME_END = re.compile(
    r"(?:ов|ёв|ев|ин|ын|ский|цкий|ской|цкой|ская|цкая|ова|ёва|ева|ина|ына|"
    r"их|ых|енко|чук|швили|дзе|ян)$")

# Компактный словарь распространённых русских имён (расширяемый). Полнота словаря
# влияет только на случай «Имя Фамилия» без отчества/инициалов — самые частые в
# документах шаблоны (полное ФИО, «Фамилия И.О.») ловятся и без словаря.
_FIRST_NAMES = frozenset({
    # мужские
    "александр", "алексей", "анатолий", "андрей", "антон", "аркадий", "арсений",
    "артём", "артем", "артур", "богдан", "борис", "вадим", "валентин", "валерий",
    "василий", "виктор", "виталий", "владимир", "владислав", "вячеслав", "геннадий",
    "георгий", "глеб", "григорий", "даниил", "данила", "денис", "дмитрий", "евгений",
    "егор", "иван", "игорь", "илья", "кирилл", "константин", "лев", "леонид", "максим",
    "марк", "матвей", "михаил", "никита", "николай", "олег", "павел", "пётр", "петр",
    "роман", "ростислав", "руслан", "святослав", "семён", "семен", "сергей", "станислав",
    "степан", "тимофей", "тимур", "фёдор", "федор", "филипп", "эдуард", "юрий", "яков",
    "ярослав",
    # женские
    "александра", "алла", "анастасия", "ангелина", "анна", "антонина", "валентина",
    "валерия", "варвара", "вера", "вероника", "виктория", "галина", "дарья", "диана",
    "евгения", "екатерина", "елена", "елизавета", "жанна", "зинаида", "зоя", "инна",
    "ирина", "кристина", "ксения", "лариса", "лидия", "любовь", "людмила", "маргарита",
    "марина", "мария", "надежда", "наталья", "наталия", "нина", "оксана", "олеся",
    "ольга", "полина", "раиса", "светлана", "софья", "тамара", "татьяна", "ульяна",
    "юлия", "яна",
})


def _gap_is_space(text: str, a: int, b: int) -> bool:
    """Между позициями a и b только пробелы/переводы строк (слова идут подряд)."""
    return a <= b and text[a:b].strip() == ""


def _name_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []

    # 1) Инициалы «Фамилия И.О.» / «И.О. Фамилия»
    for m in _INITIALS.finditer(text):
        spans.append((m.start(), m.end()))

    # 2) Токены — заглавные слова с позициями
    toks = [(m.group(0), m.start(), m.end()) for m in _CAP_RE.finditer(text)]
    n = len(toks)
    for i, (w, s, e) in enumerate(toks):
        wl = w.lower()
        if _PATRO_STRONG.match(w):
            # отчество-якорь: присоединяем до 2 предшествующих подряд заглавных
            # слов (фамилия, имя)
            k = i
            while (k - 1 >= 0 and i - (k - 1) <= 2
                   and _gap_is_space(text, toks[k - 1][2], toks[k][1])):
                k -= 1
            spans.append((toks[k][1], e))
        elif wl.endswith("ич") and i - 1 >= 0 and toks[i - 1][0].lower() in _FIRST_NAMES \
                and _gap_is_space(text, toks[i - 1][2], s):
            # «слабое» отчество на -ич (Ильич, Фомич) — только рядом с именем
            k = i - 1
            if (k - 1 >= 0 and _gap_is_space(text, toks[k - 1][2], toks[k][1])
                    and _SURNAME_END.search(toks[k - 1][0].lower())):
                k -= 1
            spans.append((toks[k][1], e))
        elif wl in _FIRST_NAMES:
            # имя из словаря рядом с фамилиеподобным словом (в любом порядке)
            if (i + 1 < n and _gap_is_space(text, e, toks[i + 1][1])
                    and _SURNAME_END.search(toks[i + 1][0].lower())):
                spans.append((s, toks[i + 1][2]))
            elif (i - 1 >= 0 and _gap_is_space(text, toks[i - 1][2], s)
                    and _SURNAME_END.search(toks[i - 1][0].lower())):
                spans.append((toks[i - 1][1], e))
    return spans


def _mask_names(text: str, counts: dict[str, int]) -> str:
    spans = _name_spans(text)
    if not spans:
        return text
    # Слить пересекающиеся диапазоны
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    whitelist = _whitelist()
    # Заменяем справа налево, чтобы не сбить смещения
    for s, e in sorted(merged, reverse=True):
        if text[s:e].lower() in whitelist:
            continue
        text = text[:s] + "[ФИО]" + text[e:]
        counts["ФИО"] = counts.get("ФИО", 0) + 1
    return text


# --- Основной проход ---

def _sub_count(pattern: re.Pattern, placeholder: str, label: str,
               text: str, counts: dict[str, int], *, validate=None) -> str:
    def repl(m: re.Match) -> str:
        raw = m.group(0)
        if validate is not None:
            digits = re.sub(r"\D", "", raw)
            if not validate(digits):
                return raw
        if raw.strip().lower() in _whitelist():
            return raw
        counts[label] = counts.get(label, 0) + 1
        return placeholder
    return pattern.sub(repl, text)


def mask_text(text: str) -> PIIResult:
    """Замаскировать ПДн в тексте. Возвращает очищенный текст и счётчики по типам."""
    if not text or not settings.pii_filter:
        return PIIResult(text=text, counts={})

    counts: dict[str, int] = {}
    # Порядок важен: длинные/специфичные паттерны раньше общих числовых
    text = _sub_count(_EMAIL, "[EMAIL]", "EMAIL", text, counts)
    text = _sub_count(_PHONE, "[ТЕЛЕФОН]", "ТЕЛЕФОН", text, counts)
    text = _sub_count(_SNILS, "[СНИЛС]", "СНИЛС", text, counts)
    # Карты и счета: длинные последовательности; карту подтверждаем по Луну
    text = _sub_count(_DIGITS_SEQ, "[КАРТА]", "КАРТА", text, counts,
                      validate=lambda d: 13 <= len(d) <= 19 and _luhn_ok(d))
    text = _sub_count(_DIGITS_SEQ, "[СЧЁТ]", "СЧЁТ", text, counts,
                      validate=lambda d: len(d) in (20, 22, 25))
    text = _sub_count(_PASSPORT, "[ПАСПОРТ]", "ПАСПОРТ", text, counts)
    text = _sub_count(_INN, "[ИНН]", "ИНН", text, counts)
    text = _mask_names(text, counts)
    return PIIResult(text=text, counts=counts)
